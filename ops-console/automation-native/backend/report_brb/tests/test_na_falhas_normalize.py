# -*- coding: utf-8 -*-
"""Testes de normalização NA_Falhas / Falhas (SharePoint)."""
from datetime import datetime

import pandas as pd

from report_brb.brb_filters import parse_excel_date
from report_brb.brb_loaders import _prep_na_falhas
from report_brb.na_falhas_normalize import (
    drop_blank_na_falhas_rows,
    drop_junk_excel_columns,
    normalize_na_falhas_columns,
    normalize_na_falhas_raw,
    resolve_na_falhas_sheet_name,
    supplement_na_falhas_satisfied,
)


def test_resolve_na_falhas_sheet_name_prefers_na_falhas():
    assert resolve_na_falhas_sheet_name(["NA_Demandas", "NA_Falhas"]) == "NA_Falhas"
    assert resolve_na_falhas_sheet_name(["Falhas", "NA_Demandas"]) == "Falhas"
    assert resolve_na_falhas_sheet_name(["NA_Demandas"]) is None


def test_supplement_na_falhas_satisfied():
    assert supplement_na_falhas_satisfied(["Falhas"])
    assert supplement_na_falhas_satisfied(["NA_Falhas"])
    assert not supplement_na_falhas_satisfied(["NA_Demandas"])


def test_drop_junk_excel_columns():
    df = pd.DataFrame({"Cliente": ["BRB"], "Column19": [None], "Column20": [None]})
    out = drop_junk_excel_columns(df)
    assert list(out.columns) == ["Cliente"]


def test_normalize_sharepoint_headers():
    raw = pd.DataFrame(
        [
            {
                "Cliente": "BRB - BANCO DE BRASILIA S.A.",
                "Protocolo": "123456",
                "Data de Cadastro": "15/01/2026",
                "Data de Notificação": "20/01/2026",
                "Motivo da Falha": "SINALIZAÇÃO INCORRETA",
                "Resultado do Cliente": "SEM RISCO APARENTE",
                "Resultado da Auditoria": "COM RISCO NO DOCUMENTO",
                "Demanda": "Demanda X",
                "Column19": None,
            },
            {"Cliente": None, "Protocolo": None, "Data de Cadastro": None},
        ]
    )
    out = normalize_na_falhas_raw(raw)
    assert list(out.columns) == [
        "CLIENTE",
        "PROTOCOLO",
        "DATA DE CADASTRO",
        "DATA DE NOTIFICAÇÃO",
        "MOTIVO DA FALHA",
        "RESULTADO DO CLIENTE",
        "RESULTADO DA AUDITORIA",
        "DEMANDA",
    ]
    assert len(out) == 1
    assert out.iloc[0]["CLIENTE"].startswith("BRB")


def test_normalize_na_falhas_columns_idempotent():
    canonical = pd.DataFrame({"CLIENTE": ["BRB"], "DATA DE CADASTRO": ["01/02/2026"]})
    out = normalize_na_falhas_columns(canonical)
    assert list(out.columns) == ["CLIENTE", "DATA DE CADASTRO"]


def test_drop_blank_na_falhas_rows():
    df = pd.DataFrame([{"CLIENTE": "BRB", "PROTOCOLO": "1"}, {"CLIENTE": "", "PROTOCOLO": None}])
    out = drop_blank_na_falhas_rows(df)
    assert len(out) == 1


def test_prep_na_falhas_parses_sharepoint_data_cadastro():
    raw = pd.DataFrame(
        [
            {
                "Cliente": "BRB - BANCO DE BRASILIA S.A.",
                "Protocolo": "999888",
                "Data de Cadastro": datetime(2026, 1, 15),
                "Data de Notificação": datetime(2026, 1, 20),
                "Motivo da Falha": "SINALIZAÇÃO INCORRETA",
                "Resultado do Cliente": "SEM RISCO APARENTE",
                "Resultado da Auditoria": "COM RISCO NO DOCUMENTO",
                "Demanda": "D1",
            }
        ]
    )
    out = _prep_na_falhas(raw, datetime(2026, 1, 1), datetime(2026, 1, 31), "brb")
    assert len(out) == 1
    cad = parse_excel_date(out["DATA DE CADASTRO"]).iloc[0]
    assert cad == pd.Timestamp(2026, 1, 15)
