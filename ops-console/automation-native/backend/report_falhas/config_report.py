# report_falhas/config_report.py
# -*- coding: utf-8 -*-

"""Canonical configuration for report_falhas (Etapa 1)."""

import os
from datetime import date
from pathlib import Path
from typing import Optional

# ===== EMAIL =====
DEFAULT_EMAIL_FROM = "relatorio@local"
EMAIL_FROM = os.environ.get("REPORT_EMAIL_FROM", "").strip() or DEFAULT_EMAIL_FROM

# Destinatarios por BU — preencher em config_report.local.py (EMAIL_LISTS).
EMAIL_LISTS: dict[str, list[str]] = {}
EMAIL_SCOPE_LISTS: dict[str, list[str]] = {
    "Brasília": ["lideres_bsb", "capacitacao", "planejamento", "processos_riscos"],
    "São Carlos": ["lideres_sc", "capacitacao", "planejamento", "processos_riscos"],
    "Executivo": ["gerencia_executivo"],
}
EMAIL_CC_BY_SCOPE: dict[str, list[str]] = {
    "Executivo": ["gerencia_executivo_cc"],
}
EMAIL_SKIP_SCOPES: frozenset[str] = frozenset({"Consolidado"})
# False = .eml sem Para/Cc (Reenviar habilitado no Outlook); listas vao para .txt + clipboard.
EMAIL_EMBED_RECIPIENTS_IN_EML = False


def _normalize_email(addr: str) -> Optional[str]:
    s = (addr or "").strip()
    if not s or "@" not in s:
        return None
    _local, domain = s.rsplit("@", 1)
    if not _local or not domain or "." not in domain:
        return None
    return s


def _expand_email_list_keys(list_keys: list[str], lists: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for key in list_keys or []:
        for raw in lists.get(key, []) or []:
            email = _normalize_email(str(raw))
            if not email:
                continue
            dedupe_key = email.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(email)
    return out


def should_skip_email_preview(scope_name: str) -> bool:
    return scope_name in EMAIL_SKIP_SCOPES


def resolve_email_recipients(scope_name: str) -> tuple[list[str], list[str]]:
    """Expande listas nomeadas (EMAIL_LISTS) para To/Cc do escopo da BU."""
    if should_skip_email_preview(scope_name):
        return [], []
    to_list = _expand_email_list_keys(EMAIL_SCOPE_LISTS.get(scope_name, []), EMAIL_LISTS)
    cc_list = _expand_email_list_keys(EMAIL_CC_BY_SCOPE.get(scope_name, []), EMAIL_LISTS)
    return to_list, cc_list


def resolve_email_from(prompt_fn=None) -> str:
    """Retorna o remetente do .eml; pergunta se ainda estiver no placeholder local.

    Outlook só permite reenviar como você se o From for seu e-mail corporativo.
    Configure REPORT_EMAIL_FROM, config_report.local.py ou responda ao prompt.
    """
    addr = (EMAIL_FROM or "").strip()
    if addr and addr != DEFAULT_EMAIL_FROM and "@" in addr and "." in addr.split("@", 1)[-1]:
        return addr
    if prompt_fn is None:
        return addr or DEFAULT_EMAIL_FROM
    print(
        "Remetente atual: relatorio@local — o Outlook não deixa reenviar assim.\n"
        "Informe seu e-mail corporativo para a prévia sair pronta para envio."
    )
    while True:
        typed = prompt_fn("Seu e-mail (remetente da prévia): ").strip()
        if typed and "@" in typed and "." in typed.split("@", 1)[-1]:
            return typed
        print("E-mail inválido. Ex.: nome.sobrenome@empresa.com")


# ===== FILES / SHEETS =====
# Prioridade: env > config_report.local.py (gitignored) > IDF-Bots canonico.
# Veja config_report.local.example.py para criar overrides locais.

_ONEDRIVE_EXP_CORP = Path.home() / "OneDrive - EXPERIAN SERVICES CORP"
DEFAULT_FALHAS_CRITICAS_DIR = (
    _ONEDRIVE_EXP_CORP / "Planejamento - IDF - Bots" / "report-falhas-criticas"
)
DEFAULT_FALHAS_CRITICAS_EXCEL = DEFAULT_FALHAS_CRITICAS_DIR / "FALHAS_CRITICAS_MANUAL.xlsx"


def canonical_falhas_excel_path() -> Path:
    """Planilha master de Falhas Criticas no SharePoint IDF-Bots."""
    return DEFAULT_FALHAS_CRITICAS_EXCEL


def canonical_falhas_output_dir() -> Path:
    """Pasta padrao de saida dos HTML/XLSX do report."""
    return DEFAULT_FALHAS_CRITICAS_DIR


def _is_legacy_falhas_excel_path(raw: str) -> bool:
    """Detecta copias locais ou caminho antigo IDF-Bases para report-falhas."""
    s = (raw or "").replace("\\", "/").lower()
    if "downloads" in s and "falhas_criticas" in s:
        return True
    if "idf - bases" in s and "report-falhas-criticas" in s:
        return True
    return False


def _resolve_excel_path_default() -> str:
    canon = canonical_falhas_excel_path()
    if canon.is_file():
        return str(canon)
    return ""


EXCEL_PATH = (
    os.environ.get("EXCEL_SOURCE_PATH", "").strip()
    or os.environ.get("REPORT_EXCEL_PATH", "").strip()
)

# Pasta de saida para HTML/XLSX gerados. None = mesma pasta do EXCEL_PATH.
OUTPUT_DIR: Optional[str] = None


def _apply_local_config() -> None:
    """Carrega report_falhas/config_report.local.py se existir (nao versionado)."""
    global EXCEL_PATH, OUTPUT_DIR, EMAIL_FROM
    global EMAIL_LISTS, EMAIL_SCOPE_LISTS, EMAIL_CC_BY_SCOPE, EMAIL_SKIP_SCOPES
    global EMAIL_EMBED_RECIPIENTS_IN_EML
    local_path = Path(__file__).with_name("config_report.local.py")
    if not local_path.is_file():
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("report_falhas_config_local", local_path)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not EXCEL_PATH and getattr(mod, "EXCEL_PATH", None):
        EXCEL_PATH = str(mod.EXCEL_PATH).strip()
    if OUTPUT_DIR is None and getattr(mod, "OUTPUT_DIR", None) is not None:
        OUTPUT_DIR = mod.OUTPUT_DIR
    local_email = getattr(mod, "EMAIL_FROM", None)
    if local_email and not os.environ.get("REPORT_EMAIL_FROM", "").strip():
        EMAIL_FROM = str(local_email).strip()
    if getattr(mod, "EMAIL_LISTS", None):
        EMAIL_LISTS = dict(mod.EMAIL_LISTS)
    if getattr(mod, "EMAIL_SCOPE_LISTS", None):
        EMAIL_SCOPE_LISTS = dict(mod.EMAIL_SCOPE_LISTS)
    if getattr(mod, "EMAIL_CC_BY_SCOPE", None):
        EMAIL_CC_BY_SCOPE = dict(mod.EMAIL_CC_BY_SCOPE)
    if getattr(mod, "EMAIL_SKIP_SCOPES", None) is not None:
        EMAIL_SKIP_SCOPES = frozenset(mod.EMAIL_SKIP_SCOPES)
    if getattr(mod, "EMAIL_EMBED_RECIPIENTS_IN_EML", None) is not None:
        EMAIL_EMBED_RECIPIENTS_IN_EML = bool(mod.EMAIL_EMBED_RECIPIENTS_IN_EML)


def _finalize_excel_paths() -> None:
    """Garante planilha/saida no IDF-Bots quando nao houver override valido."""
    global EXCEL_PATH, OUTPUT_DIR
    canon = canonical_falhas_excel_path()
    if EXCEL_PATH and _is_legacy_falhas_excel_path(EXCEL_PATH) and canon.is_file():
        EXCEL_PATH = str(canon)
    if not EXCEL_PATH:
        fallback = _resolve_excel_path_default()
        if fallback:
            EXCEL_PATH = fallback
    if OUTPUT_DIR is None and canon.parent.is_dir():
        OUTPUT_DIR = str(canonical_falhas_output_dir())


_apply_local_config()
_finalize_excel_paths()


def require_excel_path() -> str:
    """Retorna EXCEL_PATH validado ou levanta erro com instrucoes claras."""
    if not EXCEL_PATH:
        raise FileNotFoundError(
            "Planilha Excel nao configurada.\n"
            "  1) $env:REPORT_EXCEL_PATH = '...\\IDF - Bots\\report-falhas-criticas\\FALHAS_CRITICAS_MANUAL.xlsx'\n"
            "  2) Copie report_falhas/config_report.local.example.py para config_report.local.py\n"
            "  3) Edite EXCEL_PATH em report_falhas/config_report.py"
        )
    path = Path(EXCEL_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"Planilha nao encontrada: {path}")
    return str(path)


def resolve_output_dir() -> Path:
    """Retorna o diretorio de saida dos artefatos do relatorio."""
    if OUTPUT_DIR:
        out = Path(OUTPUT_DIR)
        out.mkdir(parents=True, exist_ok=True)
        return out
    excel = require_excel_path()
    return Path(excel).parent


def resolve_scope_output_dir(
    base_dir: Path,
    scope_name: str,
    ref_date: date | None = None,
) -> Path:
    """Pasta por praça e dia: base/brasilia/2026-07-01/ …"""
    from report_falhas.io.data_loader import slug

    day = (ref_date or date.today()).strftime("%Y-%m-%d")
    dest = Path(base_dir) / slug(scope_name) / day
    dest.mkdir(parents=True, exist_ok=True)
    return dest


ABA_BASE = "Base"
ABA_HC = "hc"
ABA_SUPORTE = "Suporte"


# ===== BASE COLUMNS =====
COL_DATA = "Data de Análise"
COL_DATA_ANALISE = "Data de Análise"
COL_DATA_AUDITORIA = "Data Auditoria"

# Dias úteis (seg–sex) após o fim do mês para auditoria no fechamento mensal.
# Override em config_report.local.py se necessário.
AUDIT_GRACE_BUSINESS_DAYS = 5
COL_MATRICULA = "Matrícula Agente"
COL_CENARIO = "Novo cenário"
COL_PROTOCOLO = "Protocolo"
COL_CLIENTE = "Cliente"
COL_WORKFLOW_BASE = "Workflow"


# ===== SUPPORT SHEET COLUMNS =====
SUP_COL_DATA = "Data"
SUP_COL_PROTO = "Protocolo"
SUP_COL_CLIENTE = "Cliente"
SUP_COL_WORKFLOW = "Workflow"
SUP_COL_AGENTE = "Agente"
SUP_COL_LIDER_SOL = "Líder solicitante"
SUP_COL_TIPO_SOL = "Tipo de solicitação"
SUP_COL_DUVIDA = "Dúvida"
SUP_COL_CONCL = "Conclusão"
SUP_COL_LOCAL_SOL = "Localidade solicitante"
SUP_COL_OBS = "Observação"
SUP_COL_OBS_SUP = "Observação suporte"
SUP_COL_CONFORM = "Conformidade"
SUP_COL_DIFIC = "Grau de dificuldade"
SUP_COL_CRIT = "Crítico"
SUP_COL_TIPO_DOC = "Tipo de documento"
SUP_COL_UF_EMIS = "UF de emissão"


# ===== CONTESTACOES =====
CONTEST_SHEET_ALIASES_INT = ['Contestacao_Interna', 'Contestação_Interna', 'Contestacao Interna', 'Contestação Interna']
CONTEST_SHEET_ALIASES_EXT = ['Contestacao_Externa', 'Contestação_Externa', 'Contestacao Externa', 'Contestação Externa']
CONTEST_REMOVE_VALUE = 'Desconsiderar a Falha'


# ===== FALHAS REMOVIDAS (log SharePoint tblFailuresRemoved) =====
FALHAS_REMOVIDAS_SHEET_ALIASES = [
    'Falhas removidas', 'Falhas Removidas', 'tblFailuresRemoved',
]
FALHAS_REMOVIDAS_TYPE_DELETE = {
    'exclusao', 'exclusão', 'delete', 'remocao', 'remoção',
}
FALHAS_REMOVIDAS_TYPE_EDIT = {
    'edicao', 'edição', 'edit', 'change', 'alteracao', 'alteração',
}
