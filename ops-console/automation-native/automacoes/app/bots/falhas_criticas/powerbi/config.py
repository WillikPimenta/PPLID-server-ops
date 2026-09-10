"""Configurações da automação Power BI (Falhas Críticas)."""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable

from app.config.paths import (
    DEFAULT_FALHAS_CRITICAS_BACKUPS,
    DEFAULT_FALHAS_CRITICAS_EXCEL,
    PASTA_CONFIG,
)

TARGET_EXCEL = DEFAULT_FALHAS_CRITICAS_EXCEL
TARGET_SHEET = "Base"
BACKUPS_DIR = DEFAULT_FALHAS_CRITICAS_BACKUPS
_MES_REFERENCIA: date | None = None

REPORT_URL = (
    "https://app.powerbi.com/groups/a7094877-38a7-4312-8192-f392ce90472d/"
    "reports/f5f8431d-4897-41b2-b082-1b7c91365841/"
    "afee26222104a530428a?"
    "ctid=be67623c-1932-42a6-9d24-6c359fe5ea71&experience=power-bi"
)

PAGE_LOAD_TIMEOUT = 120_000
FILTER_SETTLE_MS = 1_500
FILTER_QUICK_MS = 400
DOWNLOAD_TIMEOUT = 120_000
ACTION_TIMEOUT = 30_000

FILTERS = {
    "operacional": {
        "checkboxes_unchecked": ["Onboarding", "Veterano"],
        "dropdowns_todos": ["Líder", "Nome Agente", "Atividade", "Equipe"],
    },
    "principal": {
        "dropdowns_todos": [
            "Cliente",
            "Workflow",
            "Origem análise",
            "Módulo falha",
            "Tendência",
            "Tipo de análise",
            "Nível de Dificuldade",
            "Cenário de falhas",
        ],
        "dropdowns_value": {
            "Tipo de Falha": "Manual",
            "Categoria falha": "Crítica",
        },
        "checkboxes_unchecked": [
            "Operations Com",
            "Operations IDF",
            "Brasília",
            "São Carlos",
        ],
    },
    "periodo": {
        "buttons_selected": ["FY27"],
        "dropdowns_todos": ["Mês"],
    },
}

REFRESH_QUERIES_AFTER_MERGE = True
QUERY_REFRESH_TIMEOUT_SEC = 900

REPORT_LOAD_MARKERS = [
    "Relatório detalhado",
    "Última atualização",
    "Filtro operacional",
]

LOGIN_MARKERS = [
    "Sign in",
    "Entrar",
    "Pick an account",
    "Escolha uma conta",
    "login.microsoftonline.com",
]

# Auto-click SSO Microsoft (conta salva / Entrar / Sim) — sem digitar senha.
LOGIN_POLL_SECONDS = 3
LOGIN_AUTO_SETTLE_MS = 2500
LOGIN_MANUAL_HINT_AFTER_SEC = 180

LOGIN_AUTO_CLICK_SELECTORS = [
    '[data-test-id="account"]',
    ".table-row",
    "#idSIButton9",
    'input[type="submit"]',
    'button[type="submit"]',
]

LOGIN_AUTO_CLICK_LABELS = [
    r"^Entrar$",
    r"^Sign in$",
    r"^Continuar$",
    r"^Continue$",
    r"^Sim$",
    r"^Yes$",
    r"^Next$",
    r"^Próximo$",
]

LOGIN_ACCOUNT_EMAIL_HINTS = ("@experian", "@serasa", "experian.com")

WaitFn = Callable[[str], None]

_wait_fn: WaitFn | None = None
EXCEL_VISIBLE = True
DOWNLOADS_DIR = DEFAULT_FALHAS_CRITICAS_EXCEL.parent / "bi"
ERRORS_DIR = DOWNLOADS_DIR / "errors"
BROWSER_PROFILE_DIR = PASTA_CONFIG / "falhas_criticas" / "browser-profile"


def set_wait_fn(fn: WaitFn | None) -> None:
    global _wait_fn
    _wait_fn = fn


def wait_for_user(message: str, *, poll_seconds: float = 5.0, max_wait_seconds: float = 600.0) -> None:
    """Aguarda login/ação do usuário (callback do bot ou input no terminal)."""
    if _wait_fn is not None:
        _wait_fn(message)
        return
    print(message)
    try:
        input()
    except EOFError:
        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            time.sleep(poll_seconds)


def apply_settings(settings: dict | None) -> None:
    """Aplica paths e flags vindos do portal / env."""
    global TARGET_EXCEL, TARGET_SHEET, BACKUPS_DIR, DOWNLOADS_DIR, ERRORS_DIR, REFRESH_QUERIES_AFTER_MERGE, EXCEL_VISIBLE
    settings = settings or {}

    excel_raw = (
        settings.get("falhas_excel_path")
        or os.environ.get("FALHAS_CRITICAS_EXCEL_PATH")
        or os.environ.get("REPORT_EXCEL_PATH")
        or ""
    ).strip()
    if excel_raw:
        TARGET_EXCEL = Path(excel_raw).expanduser()
        if TARGET_EXCEL.is_dir():
            TARGET_EXCEL = TARGET_EXCEL / "FALHAS_CRITICAS_MANUAL.xlsx"

    sheet = (settings.get("falhas_excel_sheet") or os.environ.get("FALHAS_CRITICAS_EXCEL_SHEET") or TARGET_SHEET).strip()
    if sheet:
        TARGET_SHEET = sheet

    backups_raw = (settings.get("falhas_backups_dir") or os.environ.get("FALHAS_CRITICAS_BACKUPS_DIR") or "").strip()
    BACKUPS_DIR = Path(backups_raw).expanduser() if backups_raw else TARGET_EXCEL.parent / "backups"

    DOWNLOADS_DIR = TARGET_EXCEL.parent / "bi"
    ERRORS_DIR = DOWNLOADS_DIR / "errors"

    profile_raw = (settings.get("falhas_browser_profile") or os.environ.get("FALHAS_CRITICAS_BROWSER_PROFILE") or "").strip()
    if profile_raw:
        BROWSER_PROFILE_DIR = Path(profile_raw).expanduser()
    else:
        BROWSER_PROFILE_DIR = PASTA_CONFIG / "falhas_criticas" / "browser-profile"

    refresh_raw = settings.get("falhas_refresh_queries", os.environ.get("FALHAS_CRITICAS_REFRESH_QUERIES", "1"))
    if isinstance(refresh_raw, str):
        REFRESH_QUERIES_AFTER_MERGE = refresh_raw.strip().lower() in ("1", "true", "yes", "on")
    else:
        REFRESH_QUERIES_AFTER_MERGE = bool(refresh_raw)

    if "excel_visible" in settings:
        EXCEL_VISIBLE = bool(settings.get("excel_visible"))
    elif settings.get("modo_segundo_plano"):
        EXCEL_VISIBLE = False
    else:
        EXCEL_VISIBLE = True

    for path in (DOWNLOADS_DIR, ERRORS_DIR, BROWSER_PROFILE_DIR, BACKUPS_DIR):
        path.mkdir(parents=True, exist_ok=True)

    global _MES_REFERENCIA
    raw_mes = (settings.get("mes_referencia") or os.environ.get("FALHAS_MES_REFERENCIA") or "").strip()
    _MES_REFERENCIA = _parse_mes_referencia(raw_mes) if raw_mes else None


def _backend_root() -> Path:
    env = os.environ.get("REPORT_FALHAS_BACKEND_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[5] / "backend"


def _ensure_report_falhas_importable() -> None:
    backend = _backend_root()
    backend_str = str(backend)
    if backend.exists() and backend_str not in sys.path:
        sys.path.insert(0, backend_str)


def _parse_mes_referencia(raw: str) -> date:
    """Converte 'MM/AAAA' no primeiro dia do mês."""
    s = str(raw).strip().replace("-", "/")
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m2 = re.fullmatch(r"(\d{2})(\d{4})", s)
    if m2:
        return date(int(m2.group(2)), int(m2.group(1)), 1)
    return date.today().replace(day=1)


def get_date_range(*, today: date | None = None) -> tuple[str, str]:
    """Intervalo Power BI alinhado ao mes_referencia (fechamento ou MTD)."""
    _ensure_report_falhas_importable()
    from report_falhas.periods import resolve_mtd_period

    today = today or date.today()
    ref = _MES_REFERENCIA or today
    cur_start, cur_end = resolve_mtd_period(ref, today=today)
    return cur_start.strftime("%d/%m/%Y"), cur_end.strftime("%d/%m/%Y")


def get_fiscal_quarter(reference: date | None = None) -> str:
    d = reference or date.today()
    month = d.month
    if 4 <= month <= 6:
        return "Q1"
    if 7 <= month <= 9:
        return "Q2"
    if 10 <= month <= 12:
        return "Q3"
    return "Q4"


def get_fiscal_quarter_for_range(start: str, end: str) -> str:
    from datetime import datetime

    end_date = datetime.strptime(end, "%d/%m/%Y").date()
    return get_fiscal_quarter(end_date)
