# -*- coding: utf-8 -*-
"""Resolução em lote de pendências do comparativo (automáticas / POC-teste)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import Max

from apps.dimensoes_processos.models import (
    DerivacaoEtapaComparativo,
    DimCliente,
    DimEtapa,
    DimNomeAlias,
    DimWorkflow,
)
from apps.dimensoes_processos.services.derivacao_etapa.normalize import (
    normalize_csv_etapa_key,
    normalize_csv_name_key,
)

# Prefixos de etapa automática (mais longos primeiro).
ETAPA_AUTOMATIC_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Gerenciador de Regras Batimento de Dados II", "Gerenciador de Regras Batimento de Dados II"),
    ("Gerenciador de Regras Batimento de Dados", "Gerenciador de Regras Batimento de Dados"),
    ("Gerenciador de Regras OCR + Tipificação", "Gerenciador de Regras OCR + Tipificação"),
    ("Gerenciador de Regras Validação de Faces", "Gerenciador de Regras Validação de Faces"),
    ("Gerenciador de Regras Validação Cadastral", "Gerenciador de Regras Validação Cadastral"),
    ("Gerenciador de Regras Resultado Cliente", "Gerenciador de Regras Resultado Cliente"),
    ("Gerenciador de Regras Consulta CPF", "Gerenciador de Regras Consulta CPF"),
    ("Gerenciador de Regras Integrador", "Gerenciador de Regras Integrador"),
    ("Gerenciador de Entrada Documentoscopia", "Gerenciador de Entrada Documentoscopia"),
    ("Gerenciador de Validação OCR", "Gerenciador de Validação OCR"),
    ("Gerenciador de Fila Automático II", "Gerenciador de Fila Automático II"),
    ("Gerenciador de Fila Automático", "Gerenciador de Fila Automático"),
    ("Gerenciador de Regras Facematch", "Gerenciador de Regras Facematch"),
    ("Gerenciador de Regras Selfie", "Gerenciador de Regras Selfie"),
    ("Gerenciador de Regras PQDOC", "Gerenciador de Regras PQDOC"),
    ("Gerenciador de Regras OCR", "Gerenciador de Regras OCR"),
    ("Gerenciador de Entrada", "Gerenciador de Entrada"),
    ("Gerenciador de Regras CE", "Gerenciador de Regras CE"),
    ("Gerenciador de Regras AV", "Gerenciador de Regras AV"),
    ("Gerenciador de Regras", "Gerenciador de Regras"),
    ("Documentoscopia Digital", "Documentoscopia Digital"),
    ("Consulta Externa", "Consulta Externa"),
    ("Risk Manager", "Risk Manager"),
    ("Facematch", "Facematch"),
    ("Integrador", "Integrador"),
    ("OCR", "OCR"),
)

POC_TEST_PATTERN = re.compile(
    r"(?i)\b("
    r"poc|piloto|plt-|homolog|"
    r"\bqa\b|teste|sandbox|"
    r"\bamx-|juvo"
    r")\b"
)


@dataclass
class AutoResolveStats:
    cliente_criados: int = 0
    workflow_criados: int = 0
    etapa_buckets_criados: int = 0
    aliases_criados: int = 0
    aliases_atualizados: int = 0
    etapa_automatica: int = 0
    poc_teste: int = 0
    producao: int = 0
    amostras: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cliente_criados": self.cliente_criados,
            "workflow_criados": self.workflow_criados,
            "etapa_buckets_criados": self.etapa_buckets_criados,
            "aliases_criados": self.aliases_criados,
            "aliases_atualizados": self.aliases_atualizados,
            "etapa_automatica": self.etapa_automatica,
            "poc_teste": self.poc_teste,
            "producao": self.producao,
            "amostras": self.amostras[:40],
        }


def is_poc_test_name(nome: str) -> bool:
    return bool(POC_TEST_PATTERN.search(nome or ""))


def etapa_bucket_label(nome_origem: str) -> str:
    normalized = (nome_origem or "").strip()
    for prefix, bucket in ETAPA_AUTOMATIC_PREFIXES:
        if normalized.startswith(prefix):
            return bucket
    head = normalized.split(" - ", 1)[0].strip()
    return head or normalized


def _next_id(model, pk_field: str) -> int:
    agg = model.objects.aggregate(mx=Max(pk_field))
    return int(agg["mx"] or 0) + 1


def _remember_sample(stats: AutoResolveStats, *, dimensao: str, nome: str, classificacao: str, destino: str):
    if len(stats.amostras) >= 40:
        return
    stats.amostras.append(
        {
            "dimensao": dimensao,
            "nome_origem": nome,
            "classificacao": classificacao,
            "destino": destino,
        }
    )


def _get_or_create_bucket_etapa(label: str, *, stats: AutoResolveStats, persist: bool) -> DimEtapa | None:
    canonical_name = f"{label} (automática)"
    existing = DimEtapa.objects.filter(nome__iexact=canonical_name, manual=False).first()
    if existing:
        return existing
    existing = DimEtapa.objects.filter(nome__iexact=label, manual=False).first()
    if existing:
        return existing
    if not persist:
        stats.etapa_buckets_criados += 1
        return None
    etapa = DimEtapa.objects.create(
        id_etapa=_next_id(DimEtapa, "id_etapa"),
        nome=canonical_name,
        manual=False,
    )
    stats.etapa_buckets_criados += 1
    return etapa


def _get_or_create_poc_cliente(nome: str, *, stats: AutoResolveStats, persist: bool, user) -> DimCliente | None:
    key = normalize_csv_name_key(nome)
    existing = DimCliente.objects.filter(nome__iexact=nome).first()
    if existing:
        if persist and existing.operations:
            existing.operations = False
            existing.save(update_fields=["operations"])
        return existing
    if not persist:
        stats.cliente_criados += 1
        return None
    cliente = DimCliente.objects.create(
        id_cliente=_next_id(DimCliente, "id_cliente"),
        nome=nome,
        operations=False,
        id_classificacao=99,
    )
    stats.cliente_criados += 1
    return cliente


def _get_or_create_poc_workflow(nome: str, *, stats: AutoResolveStats, persist: bool, user) -> DimWorkflow | None:
    existing = DimWorkflow.objects.filter(nome__iexact=nome).first()
    if existing:
        if persist and existing.ind_considerar:
            existing.ind_considerar = False
            existing.save(update_fields=["ind_considerar"])
        return existing
    if not persist:
        stats.workflow_criados += 1
        return None
    workflow = DimWorkflow.objects.create(
        id_workflow=_next_id(DimWorkflow, "id_workflow"),
        nome=nome,
        ind_considerar=False,
    )
    stats.workflow_criados += 1
    return workflow


def _upsert_alias(
    *,
    dimensao: str,
    nome_origem: str,
    classificacao: str,
    stats: AutoResolveStats,
    persist: bool,
    user,
    cliente=None,
    workflow=None,
    etapa=None,
) -> None:
    if dimensao == DimNomeAlias.DIM_CLIENTE:
        key = normalize_csv_name_key(nome_origem)
    elif dimensao == DimNomeAlias.DIM_WORKFLOW:
        key = normalize_csv_name_key(nome_origem)
    else:
        key = normalize_csv_etapa_key(nome_origem)

    if not persist:
        stats.aliases_criados += 1
        return

    _, created = DimNomeAlias.objects.update_or_create(
        dimensao=dimensao,
        nome_origem_key=key,
        defaults={
            "nome_origem": nome_origem,
            "cliente": cliente,
            "workflow": workflow,
            "etapa": etapa,
            "classificacao": classificacao,
            "notas": "Resolução automática Megazord (derivacao_etapa)",
            "ativo": True,
            "created_by": user,
        },
    )
    if created:
        stats.aliases_criados += 1
    else:
        stats.aliases_atualizados += 1


def auto_resolve_comparativo(
    *,
    scan_run_id: int,
    user=None,
    dry_run: bool = False,
) -> AutoResolveStats:
    """Cadastra/associa pendências restantes classificando automáticas e POC/teste."""

    stats = AutoResolveStats()
    persist = not dry_run
    pending = DerivacaoEtapaComparativo.objects.filter(
        scan_run_id=scan_run_id,
        status=DerivacaoEtapaComparativo.STATUS_UNMATCHED,
    ).order_by("-registros_total")

    with transaction.atomic():
        for row in pending:
            nome = row.nome_origem
            if row.dimensao == DimNomeAlias.DIM_CLIENTE:
                classificacao = DimNomeAlias.CLASS_POC_TESTE
                cliente = _get_or_create_poc_cliente(nome, stats=stats, persist=persist, user=user)
                stats.poc_teste += 1
                _remember_sample(
                    stats,
                    dimensao="cliente",
                    nome=nome,
                    classificacao=classificacao,
                    destino=cliente.nome if cliente else f"Cliente POC: {nome}",
                )
                continue

            if row.dimensao == DimNomeAlias.DIM_WORKFLOW:
                classificacao = DimNomeAlias.CLASS_POC_TESTE
                workflow = _get_or_create_poc_workflow(nome, stats=stats, persist=persist, user=user)
                stats.poc_teste += 1
                destino = workflow.nome if workflow else f"Workflow POC: {nome}"
                _remember_sample(
                    stats,
                    dimensao="workflow",
                    nome=nome,
                    classificacao=classificacao,
                    destino=destino,
                )
                continue

            # Etapa: tratar como automática e alias → bucket canônico.
            bucket = etapa_bucket_label(nome)
            classificacao = DimNomeAlias.CLASS_ETAPA_AUTOMATICA
            etapa = _get_or_create_bucket_etapa(bucket, stats=stats, persist=persist)
            stats.etapa_automatica += 1
            _upsert_alias(
                dimensao=DimNomeAlias.DIM_ETAPA,
                nome_origem=nome,
                classificacao=classificacao,
                stats=stats,
                persist=persist,
                user=user,
                etapa=etapa,
            )
            _remember_sample(
                stats,
                dimensao="etapa",
                nome=nome,
                classificacao=classificacao,
                destino=etapa.nome if etapa else f"{bucket} (automática)",
            )

        if dry_run:
            transaction.set_rollback(True)

    return stats
