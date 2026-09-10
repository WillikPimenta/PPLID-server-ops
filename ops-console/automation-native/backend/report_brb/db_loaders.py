# -*- coding: utf-8 -*-
"""Carrega auditados, falhas e contestação da EO para o BRBDataBundle."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from django.db.models import Q

from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.contestacao_derivation import (
    derive_conforme_value,
)
from apps.qualidade_operacional.services.eo_db_scope import (
    filtered_auditados_for_client,
    filtered_falhas_for_client,
)
from report_brb.brb_filters import (
    add_case_columns,
    classificar_conforme,
    classificar_fn_fp,
    dedupe_by_case_key,
    filter_period,
    norm_protocolo,
    parse_excel_date,
    safe_str,
)
from apps.qualidade_operacional.services.normalize import is_contestacao_tipo_analise


def _ts(value) -> pd.Timestamp | pd.NaT:
    if value is None:
        return pd.NaT
    return pd.Timestamp(value)


def _workflow_names(ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(
        DimWorkflow.objects.filter(id_workflow__in=ids).values_list("id_workflow", "nome")
    )


def _cliente_nome(id_cliente: int) -> str:
    return (
        DimCliente.objects.filter(id_cliente=id_cliente).values_list("nome", flat=True).first()
        or ""
    )


def _date_filter(field: str, inicio: date | None, fim: date | None) -> Q:
    q = Q()
    if inicio is not None:
        q &= Q(**{f"{field}__gte": inicio})
    if fim is not None:
        q &= Q(**{f"{field}__lte": fim})
    return q


def _base_auditados_qs(id_cliente: int):
    """Compat: escopo bruto por cliente (batimento sem período). Prefer ``filtered_auditados_for_client``."""
    return QualidadeAuditado.objects.filter(id_cliente=id_cliente)


def _base_falhas_qs(id_cliente: int):
    return QualidadeFalha.objects.filter(id_cliente=id_cliente)


def _rows_to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def load_auditados_from_db(
    id_cliente: int,
    *,
    inicio: date | None = None,
    fim: date | None = None,
    cliente_nome: str = "",
) -> pd.DataFrame:
    """QualidadeAuditado → colunas compatíveis com aba Auditados (escopo EO / Indicadores)."""
    qs = filtered_auditados_for_client(id_cliente, start=inicio, end=fim)

    rows = list(
        qs.values(
            "data",
            "data_analise",
            "protocolo",
            "etapa",
            "id_workflow",
            "tipo_analise",
            "cenario",
            "status",
            "irregularidades_apontadas",
            "cadastrado_anteriormente",
            "resultado_destino",
            "resultado_origem",
            "protocolo_destino",
            "matricula",
            "procedencia",
        )
    )
    if not rows:
        return pd.DataFrame()

    wf_map = _workflow_names({int(r["id_workflow"]) for r in rows if r.get("id_workflow")})
    nome = cliente_nome or _cliente_nome(id_cliente)

    out_rows: list[dict[str, Any]] = []
    for r in rows:
        out_rows.append(
            {
                "Data": _ts(r["data"]),
                "Data análise": _ts(r["data_analise"]),
                "Protocolo": r["protocolo"],
                "Etapa": r["etapa"],
                "Cliente": nome,
                "Workflow": wf_map.get(int(r["id_workflow"]), "") if r.get("id_workflow") else "",
                "Tipo de análise": r["tipo_analise"],
                "Cenário": r["cenario"],
                "STATUS": r["status"],
                "IRREGULARIDADES_APONTADAS": r["irregularidades_apontadas"],
                "Cadastrado anteriormente": r["cadastrado_anteriormente"],
                "Resultado Destino": r["resultado_destino"],
                "Resultado Origem": r["resultado_origem"],
                "Protocolo Destino": r["protocolo_destino"],
                "Matrícula": r["matricula"],
                "procedencia": r["procedencia"],
            }
        )

    out = _rows_to_df(out_rows)
    out["Data"] = parse_excel_date(out["Data"])
    out["Data análise"] = parse_excel_date(out["Data análise"])
    out["_protocolo_norm"] = out["Protocolo"].map(norm_protocolo)
    return out


def load_falhas_gerais_from_db(
    id_cliente: int,
    *,
    inicio: date | None = None,
    fim: date | None = None,
    cliente_nome: str = "",
) -> tuple[pd.DataFrame, int, int]:
    """QualidadeFalha → colunas compatíveis com Falhas_Gerais (escopo EO / Indicadores)."""
    qs = filtered_falhas_for_client(id_cliente, start=inicio, end=fim)

    rows = list(
        qs.values(
            "protocolo",
            "matricula",
            "data_analise",
            "data",
            "cenario",
            "novo_resultado",
            "id_workflow",
            "segmento",
            "tipo_analise",
            "tendencia",
            "tipo_falha",
            "categoria_falha",
            "lider",
            "procedencia",
            "des_problemas",
            "etapa",
            "modulo",
        )
    )
    if not rows:
        empty = add_case_columns(pd.DataFrame(), "Protocolo", "Matrícula Agente")
        return empty, 0, 0

    wf_map = _workflow_names({int(r["id_workflow"]) for r in rows if r.get("id_workflow")})
    nome = cliente_nome or _cliente_nome(id_cliente)

    out_rows: list[dict[str, Any]] = []
    for r in rows:
        cenario = r["cenario"] or r["novo_resultado"] or ""
        out_rows.append(
            {
                "Protocolo": r["protocolo"],
                "Matrícula Agente": r["matricula"],
                "Nome Agente": "",
                "Data de Análise": _ts(r["data_analise"]),
                "Data Auditoria": _ts(r["data"]),
                "Novo cenário": cenario,
                "Cenário": cenario,
                "Cliente": nome,
                "Workflow": wf_map.get(int(r["id_workflow"]), "") if r.get("id_workflow") else "",
                "Segmento": r["segmento"],
                "Tipo de análise": r["tipo_analise"],
                "Tendência": r["tendencia"],
                "Tipo de falha": r["tipo_falha"] or r["categoria_falha"] or "",
                "Líder": r["lider"],
                "procedencia": r["procedencia"],
                "Motivo": r["des_problemas"],
                "Etapa": r["etapa"],
                "Módulo": r["modulo"],
            }
        )

    out = _rows_to_df(out_rows)
    out = add_case_columns(out, "Protocolo", "Matrícula Agente")
    for col in ("Data de Análise", "Data Auditoria"):
        out[col] = parse_excel_date(out[col])
    cen = out.get("Novo cenário", out.get("Cenário", pd.Series(dtype=str)))
    out["fn_fp"] = cen.map(classificar_fn_fp)
    linhas_brutas = len(out)
    deduped = dedupe_by_case_key(out, "Data de Análise")
    duplicatas = max(0, linhas_brutas - len(deduped))
    return deduped, linhas_brutas, duplicatas


def derive_contestacao_from_eo(
    id_cliente: int,
    *,
    inicio: date | None = None,
    fim: date | None = None,
    cliente_nome: str = "",
    falhas_gerais: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Monta aba Contestacao a partir de auditados/falhas contestação na EO."""
    from apps.qualidade_operacional.services.contestacao_derivation import (
        apply_contestacao_reception_period,
        build_falha_lookup_indexes,
        contestacao_auditados_scope,
        contestacao_falhas_scope,
        lookup_falha_for_auditado,
    )
    from apps.qualidade_operacional.services.contestacao_metrics import (
        contestacao_operacional_auditado_q,
        contestacao_operacional_falha_q,
    )
    from apps.qualidade_operacional.services.eo_db_scope import build_eo_filter_params

    params = build_eo_filter_params(id_cliente, start=inicio, end=fim)
    aud_qs = contestacao_auditados_scope(params).filter(contestacao_operacional_auditado_q())
    aud_qs = apply_contestacao_reception_period(aud_qs, start=inicio, end=fim)

    fal_qs = contestacao_falhas_scope(params).filter(contestacao_operacional_falha_q())

    auditados = list(
        aud_qs.values(
            "id",
            "protocolo",
            "matricula",
            "data",
            "data_analise",
            "data_recepcao_contestacao",
            "data_conclusao_origem",
                "cenario",
                "etapa",
                "tipo_analise",
            "procedencia",
            "origem_tratado",
        )
    )
    if not auditados:
        return pd.DataFrame()

    falhas = list(
        fal_qs.values(
            "id",
            "protocolo",
            "matricula",
            "tipo_falha",
            "categoria_falha",
            "procedencia",
            "data_analise",
        )
    )

    falha_by_case, falha_by_protocolo = build_falha_lookup_indexes(falhas)

    nome = cliente_nome or _cliente_nome(id_cliente)
    out_rows: list[dict[str, Any]] = []
    for aud in auditados:
        fal = lookup_falha_for_auditado(
            aud,
            by_case=falha_by_case,
            by_protocolo=falha_by_protocolo,
        )
        conforme = derive_conforme_value(
            procedencia=aud.get("procedencia") or (fal or {}).get("procedencia", ""),
            tipo_falha=(fal or {}).get("tipo_falha") or (fal or {}).get("categoria_falha", ""),
            has_contestacao_falha=fal is not None,
        )
        recepcao = aud.get("data_recepcao_contestacao") or aud.get("data")
        out_rows.append(
            {
                "_auditado_id": aud.get("id") or 0,
                "Cliente": nome,
                "Protocolo": aud["protocolo"],
                "Matrícula": aud["matricula"],
                "CONFORME": conforme,
                "Data": _ts(recepcao),
                "Data de Análise": _ts(aud.get("data_analise")),
                "Data de Cadastro": _ts(aud.get("data")),
                "Data de Conclusão": _ts(aud.get("data_conclusao_origem")),
                "Cenário": aud.get("cenario", ""),
                "Etapa": aud.get("etapa", ""),
                "Origem": aud.get("origem_tratado", "") or aud.get("tipo_analise", ""),
                "Tipo de análise": aud.get("tipo_analise", ""),
            }
        )

    out = _rows_to_df(out_rows)
    out = add_case_columns(out, "Protocolo", "Matrícula")
    out["classificacao_conforme"] = out["CONFORME"].map(classificar_conforme)
    for col in ("Data de Análise", "Data de Cadastro", "Data de Conclusão", "Data"):
        if col in out.columns:
            out[col] = parse_excel_date(out[col])
    out = out.sort_values(
        ["Data de Análise", "_auditado_id"],
        ascending=[True, True],
        na_position="first",
        kind="stable",
    )
    out = out.drop_duplicates(subset=["chave_caso"], keep="last")
    out = out.drop(columns=["_auditado_id"])
    return filter_period(out, "Data", inicio, fim)


def load_contestacao_batimento_keys(
    id_cliente: int,
    *,
    cliente_nome: str = "",
) -> set[str]:
    """Índice de batimento — toda contestação EO/TSV do cliente (sem recorte de período)."""
    from report_brb.brb_filters import contestacao_batimento_keys_from_df

    full = derive_contestacao_from_eo(
        id_cliente,
        inicio=None,
        fim=None,
        cliente_nome=cliente_nome,
    )
    return contestacao_batimento_keys_from_df(full)


def load_eo_core_frames(
    client_slug: str,
    *,
    inicio: date | None = None,
    fim: date | None = None,
) -> dict[str, Any]:
    """Auditados + FG + Contestacao derivada a partir da EO."""
    from apps.brb_report.services.client_catalog import resolve_client_config

    cfg = resolve_client_config(client_slug)
    id_cliente = int(cfg["id_cliente"])
    cliente_nome = cfg.get("nome", client_slug)

    auditados = load_auditados_from_db(
        id_cliente, inicio=inicio, fim=fim, cliente_nome=cliente_nome
    )
    falhas_gerais, fg_linhas, fg_dup = load_falhas_gerais_from_db(
        id_cliente, inicio=inicio, fim=fim, cliente_nome=cliente_nome
    )
    contestacao = derive_contestacao_from_eo(
        id_cliente,
        inicio=inicio,
        fim=fim,
        cliente_nome=cliente_nome,
        falhas_gerais=falhas_gerais,
    )
    return {
        "auditados": auditados,
        "falhas_gerais": falhas_gerais,
        "contestacao": contestacao,
        "fg_linhas_brutas": fg_linhas,
        "fg_duplicatas": fg_dup,
        "id_cliente": id_cliente,
    }


def is_contestacao_externa_tipo(value: object) -> bool:
    return is_contestacao_tipo_analise(value) and "EXTERNA" in safe_str(value).upper()
