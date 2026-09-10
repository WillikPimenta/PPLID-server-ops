# -*- coding: utf-8 -*-
"""Carrega NA/Treinamentos das tabelas report_* para DataFrames do bundle."""
from __future__ import annotations

from datetime import date

import pandas as pd

from apps.brb_report.models import ReportNaDemanda, ReportNaFalha, ReportTreinamento
from report_brb.brb_loaders import (
    _prep_na_demandas,
    _prep_na_falhas,
    _prep_treinamentos,
    _prep_treinamentos_horas,
)
from report_brb.client_registry import resolve_id_cliente


_CORPORATE_TRAINING_ROWS: list[dict] | None = None


def _has_supplement_db_data(id_cliente: int) -> bool:
    return (
        ReportNaDemanda.objects.filter(id_cliente=id_cliente).exists()
        or ReportNaFalha.objects.filter(id_cliente=id_cliente).exists()
        or ReportTreinamento.objects.filter(id_cliente__in=[id_cliente, 0]).exists()
    )


def load_supplement_from_db(
    client_slug: str,
    *,
    inicio: date | None = None,
    fim: date | None = None,
) -> dict[str, pd.DataFrame] | None:
    """Retorna NA/Treinamentos do DB ou None se vazio."""
    try:
        from apps.brb_report.services.client_catalog import resolve_client_config

        id_cliente = (resolve_client_config(client_slug).get("id_cliente") or resolve_id_cliente(client_slug))
    except KeyError:
        return None
    if not _has_supplement_db_data(id_cliente):
        return None

    dem_rows = ReportNaDemanda.objects.filter(id_cliente=id_cliente)
    fal_rows = ReportNaFalha.objects.filter(id_cliente=id_cliente)
    # Treinamentos corporativos (id_cliente=0) são compartilhados por todos
    # os clientes; a camada de portfólio deduplica o total corporativo.
    global _CORPORATE_TRAINING_ROWS
    specific_rows = list(ReportTreinamento.objects.filter(id_cliente=id_cliente).values(
        "id_cliente", "event_title", "matricula", "assignment_date", "signature_date",
        "session_start", "session_final", "horas", "status", "scope",
    ))
    if _CORPORATE_TRAINING_ROWS is None:
        _CORPORATE_TRAINING_ROWS = list(ReportTreinamento.objects.filter(id_cliente=0).values(
            "id_cliente", "event_title", "matricula", "assignment_date", "signature_date",
            "session_start", "session_final", "horas", "status", "scope",
        ))
    class _Rows:
        def values(self, *args):
            return specific_rows + list(_CORPORATE_TRAINING_ROWS or [])
    tr_rows = _Rows()

    na_demandas = _demandas_to_df(dem_rows)
    na_falhas = _falhas_to_df(fal_rows)
    treinamentos, treinamentos_horas = _treinamentos_to_dfs(tr_rows)

    return {
        "na_demandas": _prep_na_demandas(na_demandas, inicio, fim, client_slug),
        "na_falhas": _prep_na_falhas(na_falhas, inicio, fim, client_slug),
        "treinamentos": _prep_treinamentos(treinamentos, inicio, fim, client_slug),
        "treinamentos_horas": _prep_treinamentos_horas(treinamentos_horas, inicio, fim, client_slug),
    }


def _demandas_to_df(qs) -> pd.DataFrame:
    rows = list(
        qs.values(
            "demanda",
            "data_abertura",
            "data_retorno",
            "mes",
            "quantidade_protocolos",
            "falhas_manuais",
            "falhas_processuais",
            "falhas_automaticas",
            "situacao",
            "cliente_label",
        )
    )
    if not rows:
        return pd.DataFrame()
    out = []
    for r in rows:
        out.append(
            {
                "Cliente": r["cliente_label"],
                "Demanda": r["demanda"],
                "Data da Abertura": r["data_abertura"],
                "Data do Retorno": r["data_retorno"],
                "Mês": r["mes"],
                "Quantidade de Protolocos": r["quantidade_protocolos"],
                "Falhas Manuais": r["falhas_manuais"],
                "Falhas Processuais": r["falhas_processuais"],
                "Falhas Automáticas": r["falhas_automaticas"],
                "Situação": r["situacao"],
            }
        )
    return pd.DataFrame(out)


def _falhas_to_df(qs) -> pd.DataFrame:
    rows = list(
        qs.values(
            "protocolo",
            "matricula",
            "data_cadastro",
            "data_notificacao",
            "motivo_falha",
            "resultado_cliente",
            "resultado_auditoria",
            "demanda",
            "cliente_label",
        )
    )
    if not rows:
        return pd.DataFrame()
    out = []
    for r in rows:
        out.append(
            {
                "CLIENTE": r["cliente_label"],
                "PROTOCOLO": r["protocolo"],
                "MATRÍCULA": r["matricula"],
                "DATA DE CADASTRO": r["data_cadastro"],
                "DATA DE NOTIFICAÇÃO": r["data_notificacao"],
                "MOTIVO DA FALHA": r["motivo_falha"],
                "RESULTADO DO CLIENTE": r["resultado_cliente"],
                "RESULTADO DA AUDITORIA": r["resultado_auditoria"],
                "DEMANDA": r["demanda"],
            }
        )
    return pd.DataFrame(out)


def _treinamentos_to_dfs(qs) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = list(
        qs.values(
            "id_cliente",
            "event_title",
            "matricula",
            "assignment_date",
            "signature_date",
            "session_start",
            "session_final",
            "horas",
            "status",
            "scope",
        )
    )
    if not rows:
        return pd.DataFrame(), pd.DataFrame()
    trein = []
    horas = []
    for r in rows:
        base = {
            "Event: EventTitle": r["event_title"],
            "Agent: UserLanID": r["matricula"],
            "AssignmentDate": r["assignment_date"],
            "SignatureDate": r["signature_date"],
            "Session: StartDate": r["session_start"],
            "Session: FinalDate": r["session_final"],
            "Status": r["status"],
            "scope_treinamento": r.get("scope") or ("corporativo" if not r.get("id_cliente") else "cliente"),
        }
        trein.append(base)
        if r["horas"] is not None:
            horas.append(
                {
                    "Event: EventTitle": r["event_title"],
                    "StartDate": r["session_start"],
                    "FinalDate": r["session_final"],
                    "Event: EstimatedDuration": r["horas"],
                    "SessionStatus": r["status"],
                    "scope_treinamento": r.get("scope") or ("corporativo" if not r.get("id_cliente") else "cliente"),
                }
            )
    return pd.DataFrame(trein), pd.DataFrame(horas)
