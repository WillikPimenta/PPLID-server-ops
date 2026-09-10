# -*- coding: utf-8 -*-
"""Sync: rotina_detalhado_bruto_record → fatos SLA útil."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.dimensoes_processos.models import DimCliente, DimNivelHierarquico, DimWorkflow
from apps.monitoramento_sla.models import SlaUtilDetalhe, SlaUtilSyncRun
from apps.monitoramento_sla.services.classificacao import classificar_ajustado, classificar_natural
from apps.monitoramento_sla.services.consolidado import rebuild_consolidado
from apps.monitoramento_sla.services.faixas import classificar_faixa
from apps.monitoramento_sla.services.keys import chave_nh, date_key, key_cwn, normalize_nome
from apps.monitoramento_sla.services.projecao_lookup import (
    find_projecao_dia,
    load_projecao_rows_for_range,
)
from apps.monitoramento_sla.services.sla_util import calcular_sla_util_segundos
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/Sao_Paulo")


def _dim_indexes() -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[int, str], dict[int, str], dict[int, str]]:
    clientes = {normalize_nome(n): int(i) for i, n in DimCliente.objects.values_list("id_cliente", "nome")}
    workflows = {normalize_nome(n): int(i) for i, n in DimWorkflow.objects.values_list("id_workflow", "nome")}
    nhs = {normalize_nome(n): int(i) for i, n in DimNivelHierarquico.objects.values_list("id_nh", "nome")}
    nomes_c = {int(i): n for i, n in DimCliente.objects.values_list("id_cliente", "nome")}
    nomes_w = {int(i): n for i, n in DimWorkflow.objects.values_list("id_workflow", "nome")}
    nomes_n = {int(i): n for i, n in DimNivelHierarquico.objects.values_list("id_nh", "nome")}
    return clientes, workflows, nhs, nomes_c, nomes_w, nomes_n


def _dedupe_origem(qs) -> dict[tuple, Any]:
    """Último por (protocolo, workflow, nh) preferindo concluído."""
    best: dict[tuple, Any] = {}
    for row in qs.iterator(chunk_size=2000):
        if not row.protocolo or not row.data_cadastro:
            continue
        key = (
            str(row.protocolo),
            normalize_nome(row.workflow),
            normalize_nome(row.nivel_hierarquico),
        )
        prev = best.get(key)
        if prev is None:
            best[key] = row
            continue
        # Preferir com conclusão
        prev_closed = prev.data_conclusao is not None
        cur_closed = row.data_conclusao is not None
        if cur_closed and not prev_closed:
            best[key] = row
        elif cur_closed == prev_closed and (row.report_date or date.min) >= (prev.report_date or date.min):
            best[key] = row
    return best


def sync_monitoramento_sla(
    *,
    days: int = 90,
    user=None,
    cutover_at: datetime | None = None,
) -> SlaUtilSyncRun:
    cutover = cutover_at or timezone.now().astimezone(TZ)
    run = SlaUtilSyncRun.objects.create(
        status=SlaUtilSyncRun.STATUS_RUNNING,
        cutover_at=cutover,
        triggered_by=user if getattr(user, "pk", None) else None,
    )
    try:
        since = (cutover.date() - timedelta(days=max(1, days)))
        origem = RotinaDetalhadoBrutoRecord.objects.filter(
            data_cadastro__gte=since,
        ).order_by("report_date")
        rows_map = _dedupe_origem(origem)
        clientes, workflows, nhs, nomes_c, nomes_w, nomes_n = _dim_indexes()

        date_min = since
        date_max = cutover.date()
        for r in rows_map.values():
            if r.data_cadastro and r.data_cadastro < date_min:
                date_min = r.data_cadastro
            if r.data_conclusao and r.data_conclusao > date_max:
                date_max = r.data_conclusao
        proj_rows = load_projecao_rows_for_range(date_min=date_min, date_max=date_max + timedelta(days=60))

        # Pré-cálculo ranking denso por KeyCWN+date_cadastro (protocolo crescente)
        groups: dict[tuple, list[str]] = defaultdict(list)
        staged: list[dict[str, Any]] = []

        for row in rows_map.values():
            problemas: list[str] = []
            id_cliente = clientes.get(normalize_nome(row.cliente))
            id_workflow = workflows.get(normalize_nome(row.workflow))
            id_nh = nhs.get(normalize_nome(row.nivel_hierarquico))
            if id_cliente is None:
                problemas.append("CLIENTE_NAO_ENCONTRADO")
            if id_workflow is None:
                problemas.append("WORKFLOW_NAO_ENCONTRADO")
            if id_nh is None:
                problemas.append("NIVEL_NAO_ENCONTRADO")

            data_cad = row.data_cadastro
            # A origem não possui horário. Use 00:00 somente como âncora da
            # regra diária de SLA; persista NULL para não criar pico falso.
            hora_cad_calculo = time(0, 0, 0)
            hora_cad = None
            em_aberto = row.data_conclusao is None
            if em_aberto:
                data_fim = cutover.date()
                hora_fim = cutover.timetz().replace(tzinfo=None)
            else:
                data_fim = row.data_conclusao
                hora_fim = time(0, 0, 0)

            sla_seg = None
            proj_cad = None
            vencimento = None
            nat = ""
            aj = ""
            faixa = None
            kcwn = None

            if id_cliente is not None and id_workflow is not None and id_nh is not None:
                kcwn = key_cwn(id_cliente, id_workflow, id_nh)
                sla_seg, p_sla = calcular_sla_util_segundos(
                    id_cliente=id_cliente,
                    id_workflow=id_workflow,
                    id_nh=id_nh,
                    data_cadastro=data_cad,
                    hora_cadastro=hora_cad_calculo,
                    data_fim=data_fim,
                    hora_fim=hora_fim,
                    proj_rows=proj_rows,
                )
                problemas.extend(p_sla)
                proj_cad, p_pc = find_projecao_dia(
                    id_cliente=id_cliente,
                    id_workflow=id_workflow,
                    id_nh=id_nh,
                    on_date=data_cad,
                    rows=proj_rows,
                )
                problemas.extend(p_pc)
                nat, vencimento, p_nat = classificar_natural(
                    id_workflow=id_workflow,
                    data_cadastro=data_cad,
                    data_referencia=data_fim,
                    sla_segundos=sla_seg,
                    proj_cadastro=proj_cad,
                    em_aberto=em_aberto,
                    id_cliente=id_cliente,
                    id_nh=id_nh,
                    proj_rows=proj_rows,
                )
                problemas.extend(p_nat)
                faixa = classificar_faixa(sla_seg)
                groups[(kcwn, data_cad)].append(str(row.protocolo))

            staged.append(
                {
                    "protocolo": str(row.protocolo),
                    "id_cliente": id_cliente,
                    "id_workflow": id_workflow,
                    "id_nh": id_nh,
                    "cliente_nome": nomes_c.get(id_cliente or -1, row.cliente or ""),
                    "workflow_nome": nomes_w.get(id_workflow or -1, row.workflow or ""),
                    "nh_nome": nomes_n.get(id_nh or -1, row.nivel_hierarquico or ""),
                    "data_cadastro": data_cad,
                    "hora_cadastro": hora_cad,
                    "hora_cadastro_fonte": SlaUtilDetalhe.HORA_FONTE_INDISPONIVEL,
                    "data_conclusao": None if em_aberto else data_fim,
                    "hora_conclusao": None if em_aberto else hora_fim,
                    "em_aberto": em_aberto,
                    "resultado": row.resultado or "",
                    "tipo_conclusao": "",
                    "avaliacao": "",
                    "date_key_cadastro": date_key(data_cad) or 0,
                    "date_key_conclusao": date_key(None if em_aberto else data_fim),
                    "key_cwn": kcwn,
                    "chave_nh": chave_nh(row.protocolo, id_nh or 0),
                    "sla_segundos": sla_seg,
                    "data_vencimento_d2u": vencimento,
                    "sla_descricao_natural": nat,
                    "sla_descricao_ajustado": aj,
                    "faixa": faixa or "",
                    "problemas": sorted(set(problemas)),
                    "proj_cad": proj_cad,
                }
            )

        # ranks densos
        ranks: dict[tuple[int | None, date, str], int] = {}
        for (kc, d), protos in groups.items():
            for i, p in enumerate(sorted(set(protos)), start=1):
                ranks[(kc, d, p)] = i

        for item in staged:
            proj = item.pop("proj_cad", None)
            rnk = ranks.get((item["key_cwn"], item["data_cadastro"], item["protocolo"]))
            item["sla_descricao_ajustado"] = classificar_ajustado(
                sla_natural=item["sla_descricao_natural"] or "Fora",
                sla_segundos=item["sla_segundos"],
                proj_cadastro=proj,
                rank_protocolo=rnk,
            )

        with transaction.atomic():
            # Upsert por (protocolo, workflow, nh)
            existing = {
                (r.protocolo, r.id_workflow, r.id_nh): r
                for r in SlaUtilDetalhe.objects.filter(data_cadastro__gte=since)
            }
            to_create: list[SlaUtilDetalhe] = []
            to_update: list[SlaUtilDetalhe] = []
            for item in staged:
                key = (item["protocolo"], item["id_workflow"], item["id_nh"])
                cur = existing.get(key)
                if cur is None:
                    to_create.append(SlaUtilDetalhe(sync_run=run, **item))
                else:
                    for k, v in item.items():
                        setattr(cur, k, v)
                    cur.sync_run = run
                    to_update.append(cur)
            if to_create:
                SlaUtilDetalhe.objects.bulk_create(to_create, batch_size=1000)
            if to_update:
                fields = [
                    "id_cliente",
                    "cliente_nome",
                    "workflow_nome",
                    "nh_nome",
                    "data_cadastro",
                    "hora_cadastro",
                    "hora_cadastro_fonte",
                    "data_conclusao",
                    "hora_conclusao",
                    "em_aberto",
                    "resultado",
                    "tipo_conclusao",
                    "avaliacao",
                    "date_key_cadastro",
                    "date_key_conclusao",
                    "key_cwn",
                    "chave_nh",
                    "sla_segundos",
                    "data_vencimento_d2u",
                    "sla_descricao_natural",
                    "sla_descricao_ajustado",
                    "faixa",
                    "problemas",
                    "sync_run",
                    "updated_at",
                ]
                SlaUtilDetalhe.objects.bulk_update(to_update, fields, batch_size=1000)

            n_cons = rebuild_consolidado(sync_run=run)

        run.status = SlaUtilSyncRun.STATUS_OK
        run.rows_detalhe = len(staged)
        run.rows_consolidado = n_cons
        run.finished_at = timezone.now()
        run.message = f"OK detalhe={len(staged)} consolidado={n_cons}"
        run.save(
            update_fields=[
                "status",
                "rows_detalhe",
                "rows_consolidado",
                "finished_at",
                "message",
            ]
        )
        return run
    except Exception as exc:
        log.exception("sync_monitoramento_sla failed")
        run.status = SlaUtilSyncRun.STATUS_ERROR
        run.message = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "finished_at"])
        raise


# Compatibilidade para imports antigos. A implementação ativa é incremental.
from apps.monitoramento_sla.services.sync_incremental import (
    sync_monitoramento_sla as sync_monitoramento_sla,
)
