# -*- coding: utf-8 -*-
"""Serviço transacional do ledger mensal de consumo D-1."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.normalization import normalize_key
from apps.replicacao_d1.services.config_audit import registrar_historico

ORIGEM_CONFIRMADO = "confirmado"
ORIGEM_AJUSTE_MANUAL = "ajuste_manual"


def carregar_consumo_meta_mensal(competencia: str) -> dict[str, int]:
    """Retorna consumo acumulado por workflow_chave na competência."""
    qs = (
        ReplicacaoD1LedgerConsumo.objects.filter(competencia=str(competencia))
        .values("workflow_chave")
        .annotate(total=Sum("protocolos"))
    )
    out: dict[str, int] = {}
    for row in qs:
        chave = str(row.get("workflow_chave") or "").strip()
        if not chave:
            continue
        out[chave] = int(row.get("total") or 0)
    return out


@transaction.atomic
def registrar_consumo_meta_run(
    *,
    competencia: str,
    run_id: str,
    consumo_por_workflow: dict[str, int],
    cliente_por_workflow: dict[str, str] | None = None,
    origem: str = ORIGEM_CONFIRMADO,
    observacao: str = "",
    data_execucao: datetime | None = None,
    snapshot_hash: str = "",
    config_version: int | None = None,
    user=None,
) -> bool:
    """Idempotente por (run_id, competencia, workflow_chave, origem)."""
    if not run_id:
        return False
    agora = data_execucao or timezone.now()
    cliente_por_workflow = cliente_por_workflow or {}
    criou_algum = False

    for wf_raw, qtd in (consumo_por_workflow or {}).items():
        qtd = int(qtd or 0)
        if qtd <= 0:
            continue
        wf_key = normalize_key(str(wf_raw))
        if not wf_key:
            continue
        workflow = ReplicacaoD1Workflow.objects.filter(chave_normalizada=wf_key).first()
        cliente_nome = str(cliente_por_workflow.get(wf_raw, "") or cliente_por_workflow.get(wf_key, "") or "")
        cliente = None
        if cliente_nome:
            cliente = ReplicacaoD1Cliente.objects.filter(chave_normalizada=normalize_key(cliente_nome)).first()

        obj, created = ReplicacaoD1LedgerConsumo.objects.update_or_create(
            run_id=str(run_id),
            competencia=str(competencia),
            workflow_chave=wf_key,
            origem=str(origem),
            defaults={
                "cliente": cliente,
                "cliente_nome": cliente_nome,
                "workflow": workflow,
                "workflow_nome": str(wf_raw),
                "data_execucao": agora,
                "protocolos": qtd,
                "observacao": str(observacao or ""),
                "ajuste": 0,
                "snapshot_hash": str(snapshot_hash or ""),
                "config_version": config_version,
            },
        )
        if created:
            criou_algum = True
            registrar_historico(
                "ReplicacaoD1LedgerConsumo",
                obj.pk,
                "create",
                user,
                None,
                _ledger_row_dict(obj),
            )
        else:
            before = {"protocolos": obj.protocolos}
            if obj.protocolos != qtd:
                registrar_historico(
                    "ReplicacaoD1LedgerConsumo",
                    obj.pk,
                    "update",
                    user,
                    before,
                    {"protocolos": qtd},
                )

    return criou_algum or bool(consumo_por_workflow)


@transaction.atomic
def aplicar_ajuste_manual(
    *,
    competencia: str,
    workflow_chave: str,
    consumo_acumulado: int,
    user=None,
    cliente_nome: str = "",
    observacao: str = "ajuste_manual",
) -> ReplicacaoD1LedgerConsumo:
    """Substitui consumo manual do workflow na competência (origem=ajuste_manual)."""
    wf_key = normalize_key(workflow_chave)
    if not wf_key:
        raise ValueError("workflow_chave inválida")
    if consumo_acumulado < 0:
        raise ValueError("consumo_acumulado deve ser >= 0")

    workflow = ReplicacaoD1Workflow.objects.filter(chave_normalizada=wf_key).first()
    cliente = None
    if cliente_nome:
        cliente = ReplicacaoD1Cliente.objects.filter(chave_normalizada=normalize_key(cliente_nome)).first()

    run_id = f"manual_{competencia}_{wf_key}"
    before_obj = ReplicacaoD1LedgerConsumo.objects.filter(
        competencia=str(competencia),
        workflow_chave=wf_key,
        origem=ORIGEM_AJUSTE_MANUAL,
    ).first()
    before = _ledger_row_dict(before_obj) if before_obj else None

    if consumo_acumulado == 0:
        if before_obj:
            before_obj.delete()
            registrar_historico(
                "ReplicacaoD1LedgerConsumo",
                before_obj.pk,
                "delete",
                user,
                before,
                None,
            )
        return ReplicacaoD1LedgerConsumo(
            competencia=str(competencia),
            workflow_chave=wf_key,
            run_id=run_id,
            origem=ORIGEM_AJUSTE_MANUAL,
            protocolos=0,
            data_execucao=timezone.now(),
        )

    obj, created = ReplicacaoD1LedgerConsumo.objects.update_or_create(
        run_id=run_id,
        competencia=str(competencia),
        workflow_chave=wf_key,
        origem=ORIGEM_AJUSTE_MANUAL,
        defaults={
            "cliente": cliente,
            "cliente_nome": str(cliente_nome or ""),
            "workflow": workflow,
            "workflow_nome": workflow.nome_canonico if workflow else wf_key,
            "data_execucao": timezone.now(),
            "protocolos": int(consumo_acumulado),
            "observacao": str(observacao or ""),
            "ajuste": int(consumo_acumulado),
        },
    )
    registrar_historico(
        "ReplicacaoD1LedgerConsumo",
        obj.pk,
        "create" if created else "update",
        user,
        before,
        _ledger_row_dict(obj),
    )
    return obj


def purge_ledger_por_runs(run_ids: list[str]) -> int:
    """Remove entradas de ledger associadas aos run_ids informados."""
    ids = [str(r).strip() for r in run_ids if str(r).strip()]
    if not ids:
        return 0
    deleted, _ = ReplicacaoD1LedgerConsumo.objects.filter(run_id__in=ids).delete()
    return int(deleted)


@transaction.atomic
def recalcular_consumo_competencia(
    competencia: str,
    *,
    user=None,
) -> dict[str, int]:
    """
    Recalcula agregados derivados sem remover linhas origem=ajuste_manual.
    Retorna consumo total por workflow após recálculo lógico.
    """
    manual_keys = set(
        ReplicacaoD1LedgerConsumo.objects.filter(
            competencia=str(competencia),
            origem=ORIGEM_AJUSTE_MANUAL,
        ).values_list("workflow_chave", flat=True)
    )
    consumo = carregar_consumo_meta_mensal(competencia)
    registrar_historico(
        "ReplicacaoD1LedgerConsumo",
        competencia,
        "recalcular",
        user,
        None,
        {
            "competencia": competencia,
            "workflows": len(consumo),
            "ajustes_manuais_preservados": sorted(manual_keys),
        },
    )
    return consumo


def _ledger_row_dict(obj: ReplicacaoD1LedgerConsumo | None) -> dict[str, Any] | None:
    if obj is None:
        return None
    return {
        "competencia": obj.competencia,
        "workflow_chave": obj.workflow_chave,
        "run_id": obj.run_id,
        "origem": obj.origem,
        "protocolos": obj.protocolos,
        "ajuste": obj.ajuste,
        "cliente_nome": obj.cliente_nome,
    }
