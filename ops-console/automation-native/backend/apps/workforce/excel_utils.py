"""Utilitários compartilhados para leitura da planilha BASE.xlsx."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

DEFAULT_XLSX = Path(__file__).resolve().parents[3] / "data" / "BASE.xlsx"

SHEET_USERS_ALIASES = ("USERS", "AGENT", "AGENTS", "QUERY", "QUERY_2", "DIMUSERS")
SHEET_HISTORY_ALIASES = (
    "USER_HISTORY",
    "AGENT_HISTORY",
    "AGENT_HISTOTY",
    "AGENT_HISTORy",
    "QUERY",
    "QUERY_1",
    "TBHEADCOUNT",
    "HEADCOUNT",
)

AGENT_COLUMN_ALIASES = {
    "user_lan_id",
    "user_lanid",
    "userlanid",
    "lan_id",
    "agent_id",
    "agent",
    "agent_userlanid",
    "colaborador_id",
}
# Preferência para import SharePoint: LAN antes do nome "Agent".
AGENT_LAN_COLUMN_ALIASES = {
    "user_lan_id",
    "user_lanid",
    "userlanid",
    "lan_id",
    "agent_userlanid",
    "colaborador_id",
}
AGENT_NAME_ALIASES = {
    "full_name",
    "fullname",
    "nome",
    "colaborador",
    "agent_name",
    "employee_name",
    "agent",
}

LEADER_COLUMN_ALIASES = {
    "leader_id",
    "leader",
    "leader_userlanid",
    "lider",
    "lider_id",
    "manager",
    "gestor",
}
LEADER_LAN_COLUMN_ALIASES = {
    "leader_lan_id",
    "leader_userlanid",
    "leader_id",
    "lider_id",
}
FACILITATOR_COLUMN_ALIASES = {
    "facilitador_id",
    "facilitator_id",
    "facilitator",
    "facilitator_userlanid",
    "facilitador",
}
FACILITATOR_LAN_COLUMN_ALIASES = {
    "facilitator_lan_id",
    "facilitator_userlanid",
    "facilitator_id",
    "facilitador_id",
}
SHAREPOINT_ID_COLUMN_ALIASES = {"id", "sharepoint_id", "sharepoint_item_id", "item_id"}


def normalize_name(value: str | None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_column(name: str) -> str:
    text = str(name).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    renamed = {col: normalize_column(col) for col in df.columns}
    df = df.rename(columns=renamed)
    return apply_standard_column_names(df)


# SharePoint / IDF export → nomes canônicos do import
STANDARD_COLUMN_RENAMES = {
    "userlanid": "user_lan_id",
    "fullname": "full_name",
    "hiredate": "hire_date",
    "timetrackingid": "time_tracking_id",
    "oracleid": "oracle_id",
    "agent_userlanid": "user_lan_id",
    "leader_userlanid": "leader_lan_id",
    "facilitator_userlanid": "facilitator_lan_id",
    "jobtitle": "job_title",
    "jobactivity": "job_activity",
    "startdate": "start_date",
    "finaldate": "final_date",
    "productivitydiscount": "productivity_discount",
    "team_sector": "team_sector",
    "jobtitle_jobtitlesector": "job_title_sector",
    "jobtitle_jobtitleactivity": "job_title_activity",
    "journey_entrytime": "journey_entry_time",
    "journey_exittime": "journey_exit_time",
    "journey_shift": "journey_shift",
    "insstype": "inss_type",
    "agent_hiredate": "agent_hire_date",
    "firedtype": "external_movement_type",
}


def apply_standard_column_names(df: pd.DataFrame) -> pd.DataFrame:
    renames = {
        old: new for old, new in STANDARD_COLUMN_RENAMES.items() if old in df.columns
    }
    if renames:
        df = df.rename(columns=renames)
    return df


def find_sheet(path: Path, aliases: tuple[str, ...]) -> str:
    xl = pd.ExcelFile(path)
    normalized = {normalize_column(name): name for name in xl.sheet_names}
    for alias in aliases:
        key = normalize_column(alias)
        if key in normalized:
            return normalized[key]
    alias_keys = {normalize_column(a) for a in aliases}
    query_sheets = sorted(
        (norm, original) for norm, original in normalized.items() if norm.startswith("query")
    )
    history_aliases = {
        "user_history",
        "agent_history",
        "agent_histoty",
        "agent_histor y",
        "query_1",
        "tbheadcount",
        "headcount",
    }
    user_aliases = {"users", "agent", "agents", "query_2", "dimusers"}
    if query_sheets and alias_keys & history_aliases:
        for norm, original in query_sheets:
            if norm.endswith("1"):
                return original
        return query_sheets[0][1]
    if query_sheets and alias_keys & user_aliases:
        for norm, original in query_sheets:
            if norm.endswith("2"):
                return original
        return query_sheets[-1][1]
    raise ValueError(
        f"Nenhuma aba encontrada para {aliases}. Abas disponíveis: {xl.sheet_names}"
    )


def read_sheet(path: Path, aliases: tuple[str, ...]) -> pd.DataFrame:
    sheet = find_sheet(path, aliases)
    df = pd.read_excel(path, sheet_name=sheet)
    return normalize_columns(df)


def pick_column(df: pd.DataFrame, aliases: set[str]) -> str | None:
    for col in df.columns:
        if col in aliases or normalize_column(col) in aliases:
            return col
    return None


def parse_bool(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "sim", "s", "yes", "y"}


def parse_date(value) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_decimal(value) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            if len(parts) == 3:
                hours, minutes, seconds = (int(p) for p in parts)
                return Decimal(hours * 3600 + minutes * 60 + seconds) / Decimal(3600)
            if len(parts) == 2:
                hours, minutes = (int(p) for p in parts)
                return Decimal(hours * 60 + minutes) / Decimal(60)
        except ValueError:
            return None
    text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def quantize_productivity_discount(value: Decimal | None) -> Decimal | None:
    """Espelha DecimalField(max_digits=5, decimal_places=2) do AgentHistory."""
    if value is None:
        return None
    return value.quantize(Decimal("0.01"))


def parse_active(value) -> bool:
    """Mesma regra de import_base_xlsx._parse_active (None/NaN → False)."""
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return parse_bool(value)


def str_or_blank(value) -> str:
    if is_empty(value):
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def is_empty(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == ""
