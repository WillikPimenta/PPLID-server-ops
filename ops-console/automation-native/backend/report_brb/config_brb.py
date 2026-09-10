# -*- coding: utf-8 -*-
"""Configuração do Report Executivo BRB."""
from pathlib import Path

from report_brb.client_registry import get_client_config

BASE_DIR = Path(__file__).resolve().parent.parent
EXCEL_PATH = BASE_DIR / "BRB_Report_atualizado.xlsx"
OUTPUT_DIR = BASE_DIR / "output"

CLIENT = get_client_config("brb")

SUPPLEMENT_SHEETS = [
    "NA_Demandas",
    "NA_Falhas",
    "Treinamentos",
]

EXPECTED_SHEETS = [
    *SUPPLEMENT_SHEETS,
    "TreinamentosComHoras",
    "Falhas_Gerais-BRB",
    "Contestacao",
    "Auditados",
]

ATTACK_KEYWORDS = (
    "ADULTER",
    "FRAUDE",
    "FRAUDADOR",
    "MANIPUL",
    "QUADRILHA",
    "SOBREPOSI",
    "FALSIF",
)

LEGACY_FILES_TO_REMOVE = [
    "BRB_Report.xlsx",
    "_BRB_latest.xlsx",
    "_BRB_Report_copy.xlsx",
]

MES_PT = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARÇO": 3,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}
