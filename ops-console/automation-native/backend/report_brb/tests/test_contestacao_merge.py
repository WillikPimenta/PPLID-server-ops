# -*- coding: utf-8 -*-
import pandas as pd

from report_brb.brb_filters import contestacao_batimento_key, contestacao_batimento_key_from_row
from report_brb.brb_loaders import merge_contestacao_eo_with_excel


def _row(**kwargs):
    base = {
        "Cliente": "BRB Banco de Brasília",
        "Protocolo": "12345",
        "Matrícula": "c90001a",
        "Etapa": "ANÁLISE PRE QUALITY",
        "Cenário": "NÃO SINALIZADO - FORMATAÇÃO",
        "CONFORME": "Não",
        "Data": pd.Timestamp("2026-02-01"),
        "Data de Análise": pd.Timestamp("2026-01-15"),
    }
    base.update(kwargs)
    return base


def test_batimento_key_includes_cenario_etapa_cliente():
    key = contestacao_batimento_key(
        protocolo="12345",
        cliente="BRB",
        etapa="Etapa A",
        matricula="c90001a",
        cenario="Cenario X",
    )
    assert key == contestacao_batimento_key(
        protocolo="12345",
        cliente="BRB",
        etapa="Etapa A",
        matricula="c90001a",
        cenario="Cenario X",
    )
    different_etapa = contestacao_batimento_key(
        protocolo="12345",
        cliente="BRB",
        etapa="Outra etapa",
        matricula="c90001a",
        cenario="Cenario X",
    )
    assert key != different_etapa


def test_merge_skips_excel_row_when_batimento_matches_eo():
    eo = pd.DataFrame([_row()])
    eo["classificacao_conforme"] = "falha"
    excel = pd.DataFrame([_row()])
    batimento = {contestacao_batimento_key_from_row(eo.iloc[0])}

    merged, stats = merge_contestacao_eo_with_excel(
        eo,
        excel,
        batimento_keys=batimento,
        inicio=None,
        fim=None,
        client_slug="brb",
    )
    assert len(merged) == 1
    assert stats["excel_skipped_batimento"] == 1
    assert stats["excel_appended"] == 0


def test_merge_appends_excel_only_when_not_in_batimento():
    eo = pd.DataFrame([_row(Etapa="Etapa EO")])
    eo["classificacao_conforme"] = "falha"
    excel = pd.DataFrame([_row(Etapa="Etapa Planilha", Protocolo="99999")])
    batimento = {contestacao_batimento_key_from_row(eo.iloc[0])}

    merged, stats = merge_contestacao_eo_with_excel(
        eo,
        excel,
        batimento_keys=batimento,
        inicio=None,
        fim=None,
        client_slug="brb",
    )
    assert len(merged) == 2
    assert stats["excel_appended"] == 1
    assert set(merged["Protocolo"].astype(str)) == {"12345", "99999"}
