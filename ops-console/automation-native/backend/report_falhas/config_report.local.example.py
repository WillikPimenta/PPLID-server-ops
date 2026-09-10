# -*- coding: utf-8 -*-
"""
Configuracao local (nao versionar).

Como usar:
  copy report_falhas\\config_report.local.example.py report_falhas\\config_report.local.py
  Ajuste EXCEL_PATH e EMAIL_LISTS com os e-mails das listas do Outlook.
"""
from typing import Optional

EXCEL_PATH = (
    r"%USERPROFILE%\OneDrive - EXPERIAN SERVICES CORP"
    r"\Planejamento - IDF - Bots\report-falhas-criticas\FALHAS_CRITICAS_MANUAL.xlsx"
)
OUTPUT_DIR: Optional[str] = (
    r"%USERPROFILE%\OneDrive - EXPERIAN SERVICES CORP"
    r"\Planejamento - IDF - Bots\report-falhas-criticas"
)
EMAIL_FROM = "seu.email@empresa.com"

# Alternativa sem editar arquivo (PowerShell):
#   $env:REPORT_EMAIL_FROM = "seu.email@empresa.com"

# Listas nomeadas — copie os e-mails das listas de contato do Outlook.
# Se a TI tiver alias de DL (grupo Exchange), pode usar um unico e-mail por chave.
EMAIL_LISTS = {
    "lideres_bsb": [
        # "Lideres BSB - IDF" (8 contatos)
        "lider.bsb1@experian.com",
        "lider.bsb2@experian.com",
    ],
    "lideres_sc": [
        # "Lideres SC - IDF" (13 contatos)
        "lider.sc1@experian.com",
    ],
    "capacitacao": [
        # "Capacitacao - IDF"
        "capacitacao@experian.com",
    ],
    "planejamento": [
        # "Planejamento - IDF"
        "planejamento@experian.com",
    ],
    "processos_riscos": [
        # "Processos e riscos - IDF"
        "processos.riscos@experian.com",
    ],
    "gerencia_executivo": [
        "rogerio.velista@experian.com",
        "sulamita.nunes@experian.com",
        "asriel.bacelar@experian.com",
        "desiree.ribeiro@experian.com",
    ],
    "gerencia_executivo_cc": [
        "lucelia.hiratani@experian.com",
        "matheus.santos@experian.com",
    ],
}

# Quais listas entram em cada BU (padrao; pode sobrescrever se necessario).
EMAIL_SCOPE_LISTS = {
    "Brasília": ["lideres_bsb", "capacitacao", "planejamento", "processos_riscos"],
    "São Carlos": ["lideres_sc", "capacitacao", "planejamento", "processos_riscos"],
    "Executivo": ["gerencia_executivo"],
}

# Cc opcional por escopo (chaves de EMAIL_LISTS).
EMAIL_CC_BY_SCOPE: dict[str, list[str]] = {
    "Executivo": ["gerencia_executivo_cc"],
}

# Escopos que NAO abrem previa de e-mail automaticamente.
EMAIL_SKIP_SCOPES = {"Consolidado"}

# False = .eml sem Para (Reenviar habilitado no Outlook). Listas vao para .txt + clipboard.
EMAIL_EMBED_RECIPIENTS_IN_EML = False
