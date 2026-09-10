# -*- coding: utf-8 -*-
"""Escreve planilha suplemento vazia (NA + Treinamentos) para storage CS."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

SUPPLEMENT_HEADERS = {
    "NA_Demandas": [
        "Cliente",
        "Demanda",
        "Data da Abertura",
        "Data do Retorno",
        "Mês",
        "Quantidade de Protolocos",
        "Falhas Manuais",
        "Falhas Processuais",
        "Falhas Automáticas",
    ],
    "NA_Falhas": [
        "CLIENTE",
        "PROTOCOLO",
        "DATA DE CADASTRO",
        "DATA DE NOTIFICAÇÃO",
        "MOTIVO DA FALHA",
        "RESULTADO DO CLIENTE",
        "RESULTADO DA AUDITORIA",
        "DEMANDA",
    ],
    "Treinamentos": [
        "Event: EventTitle",
        "AssignmentDate",
        "SignatureDate",
        "Session: StartDate",
        "Session: FinalDate",
    ],
}


def write_empty_supplement_workbook(path: Path) -> Path:
    """Cria .xlsx mínimo com abas suplemento (só cabeçalho)."""
    path = Path(path)
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    for sheet, headers in SUPPLEMENT_HEADERS.items():
        ws = wb.create_sheet(sheet)
        ws.append(headers)
    wb.save(path)
    return path
