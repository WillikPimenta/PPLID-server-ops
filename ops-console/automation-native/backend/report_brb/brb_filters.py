# -*- coding: utf-8 -*-
"""Filtros, normalização e chave única Protocolo + Matrícula."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

import numpy as np
import pandas as pd


def safe_str(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
        return ""
    return str(x).strip()


def _norm_client_key(value) -> str:
    """Normaliza nome de cliente para matching (maiúsculas, sem acento, pontuação leve)."""
    s = safe_str(value)
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    # Tipografia / pontuação comum em cadastros
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    s = s.replace("&", " E ")
    s = re.sub(r"[./,;:_()+\[\]{}'\"]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Typo frequente: Braslia / Brasilia
    s = s.replace("BRASLIA", "BRASILIA")
    return s


# Aliases / padrões do BRB (Banco de Brasília) — cobre variações de cadastro.
# Não usar só "BRASIL" (pegaria Mercantil, Vivo, TIM, etc.).
_BRB_TOKEN = re.compile(r"(?<![A-Z0-9])BRB(?![A-Z0-9])")
_BRB_BANCO_BRASILIA = re.compile(
    r"BANCO\s+DE\s+BRASILIA(?:\s+S\s*A)?|"
    r"BANCO\s+BRASILIA(?:\s+S\s*A)?"
)
_BRB_EXTRA_ALIASES = (
    "BRB DTVM",
    "BRB CFI",
    "BRB CREDITO FINANCIAMENTO E INVESTIMENTO",
    "BRB CREDITO FINANCIAMENTO",
    "BRB DISTRIBUIDORA",
)


def _match_brb_key(key: str) -> bool:
    if not key:
        return False
    if _BRB_TOKEN.search(key):
        return True
    if _BRB_BANCO_BRASILIA.search(key):
        return True
    for alias in _BRB_EXTRA_ALIASES:
        if alias in key:
            return True
    return False


def match_client(value, client_slug: str = "brb") -> bool:
    """True se o valor identificar o cliente informado (slug do registry)."""
    slug = (client_slug or "brb").strip().lower()
    key = _norm_client_key(value)
    if not key:
        return False
    if slug == "brb":
        return _match_brb_key(key)
    from report_brb.client_registry import get_client_config

    try:
        cfg = get_client_config(slug)
    except KeyError:
        return False
    for alias in cfg.get("aliases", ()):
        norm_alias = _norm_client_key(alias)
        if norm_alias and norm_alias in key:
            return True
    filtro = _norm_client_key(cfg.get("filtro", ""))
    if filtro and filtro in key:
        return True
    return False


def match_client_series(series: pd.Series, client_slug: str = "brb") -> pd.Series:
    return series.map(lambda v: match_client(v, client_slug))


def is_brb(value) -> bool:
    """True se o texto identificar o cliente BRB / Banco de Brasília.

    Aceita variações como:
    - BRB
    - BRB - BANCO DE BRASILIA S.A.
    - BANCO DE BRASILIA S.A.
    - BRB Banco de Brasília S. A. / Braslia (typo)
    - BRB DTVM / BRB CFI
    Rejeita outros bancos com "Brasil" no nome (ex.: Mercantil do Brasil).
    """
    return match_client(value, "brb")


def is_brb_series(series: pd.Series) -> pd.Series:
    """Máscara vetorizada equivalente a ``series.map(is_brb)``."""
    return series.map(is_brb)


def norm_matricula(x) -> str:
    if x is None or (hasattr(pd, "isna") and pd.isna(x)):
        return ""
    if isinstance(x, (int, np.integer)):
        return str(int(x)).lower()
    if isinstance(x, (float, np.floating)) and np.isfinite(x) and float(x).is_integer():
        return str(int(x)).lower()
    s = safe_str(x)
    if not s or s.lower() in ("nan", "none"):
        return ""
    num = pd.to_numeric(s, errors="coerce")
    if pd.notna(num) and float(num).is_integer():
        return str(int(float(num))).lower()
    s = s.replace("\u00a0", " ")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    m = re.search(r"([A-Za-z]?\d{3,}[A-Za-z]?)(?:\s|$|Nome|[^A-Za-z0-9])", s)
    if m:
        return m.group(1).lower()
    s = re.sub(r"\s+", "", s)
    m = re.fullmatch(r"(\d+)\.0+", s)
    if m:
        return m.group(1)
    return s.lower()


def norm_protocolo(x) -> str:
    if x is None or (hasattr(pd, "isna") and pd.isna(x)):
        return ""
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    if isinstance(x, (float, np.floating)) and np.isfinite(x) and float(x).is_integer():
        return str(int(x))
    s = safe_str(x)
    if not s or s.lower() in ("nan", "none"):
        return ""
    num = pd.to_numeric(s, errors="coerce")
    if pd.notna(num) and float(num).is_integer():
        return str(int(float(num)))
    m = re.fullmatch(r"(\d+)\.0+", s)
    if m:
        return m.group(1)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", s).strip()


def case_key(protocolo, matricula) -> tuple[str, str]:
    return norm_protocolo(protocolo), norm_matricula(matricula)


def classificar_conforme(valor) -> str:
    """CONFORME=SIM → análise conforme (não é falha). CONFORME=NÃO → é falha."""
    v = safe_str(valor).upper()
    v = v.replace("Ã", "A").replace("Õ", "O")
    if v in ("SIM", "S", "YES"):
        return "nao_falha"
    if v in ("NÃO", "NAO", "N", "NO"):
        return "falha"
    return "indefinido"


def classificar_fn_fp(cenario) -> str:
    s = safe_str(cenario).upper()
    if "NÃO SINALIZADO" in s or "NAO SINALIZADO" in s:
        return "FN"
    if "SINALIZAÇÃO INCORRETA" in s or "SINALIZACAO INCORRETA" in s:
        return "FP"
    if "SINALIZAÇÃO VALIDADA" in s or "SINALIZACAO VALIDADA" in s:
        return "FP"
    return ""


def is_possivel_ataque(texto, keywords: tuple[str, ...]) -> bool:
    su = safe_str(texto).upper()
    return any(k in su for k in keywords)


def fix_swapped_dmy_if_future(
    ts: pd.Timestamp,
    ref: date | datetime | pd.Timestamp | None = None,
) -> pd.Timestamp:
    """Corrige inversão mês/dia em datas futuras do ano corrente.

    Só atua se o ano da data for o mesmo de ``ref`` (hoje) e a data for
    posterior ao mês/dia atuais — ex.: em jul/2026, 03/12/2026 → 12/03/2026.
    Anos anteriores (ex.: 2025) não são alterados; o ano nunca é recuado.
    """
    if ts is None or pd.isna(ts):
        return ts
    ts = pd.Timestamp(ts)
    if ref is None:
        ref_ts = pd.Timestamp.today().normalize()
    else:
        ref_ts = pd.Timestamp(ref).normalize()

    # 2025 (ou outro ano ≠ atual) está certo — não mexe
    if int(ts.year) != int(ref_ts.year):
        return ts
    # Só arruma se for futura em relação a hoje (mês atual do ano corrente)
    if ts.normalize() <= ref_ts:
        return ts

    day, month = int(ts.day), int(ts.month)
    if day > 12 or day == month:
        return ts
    try:
        swapped = ts.replace(month=day, day=month)
    except ValueError:
        return ts
    # Mantém o mesmo ano; só aceita se deixar de ser futura
    if swapped.normalize() <= ref_ts:
        return swapped
    return ts


def parse_excel_date(
    series: pd.Series,
    *,
    ref: date | datetime | pd.Timestamp | None = None,
) -> pd.Series:
    """Converte datetime, serial Excel ou string dd/mm/yyyy.

    Aplica correção de inversão mês/dia em datas futuras do ano corrente
    (ver ``fix_swapped_dmy_if_future``).
    """
    if series is None or series.empty:
        return pd.Series(dtype="datetime64[ns]")

    def _one(val):
        if val is None or (isinstance(val, float) and np.isnan(val)) or pd.isna(val):
            return pd.NaT
        dt = pd.NaT
        if isinstance(val, (datetime, pd.Timestamp)):
            dt = pd.Timestamp(val)
        elif isinstance(val, (int, float, np.integer, np.floating)):
            try:
                f = float(val)
                if 30000 < f < 60000:
                    dt = pd.Timestamp("1899-12-30") + pd.Timedelta(days=f)
                elif f > 1e12:
                    dt = pd.to_datetime(f, unit="ms", errors="coerce")
            except Exception:
                dt = pd.NaT
        else:
            s = safe_str(val)
            if not s or s in ("00/00/0000", "-", "nan"):
                return pd.NaT
            if re.match(r"^\d{4}-\d{2}-\d{2}", s):
                dt = pd.to_datetime(s, errors="coerce")
            if pd.isna(dt):
                dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
            if pd.isna(dt):
                dt = pd.to_datetime(s, dayfirst=False, errors="coerce")
            if pd.isna(dt):
                num = pd.to_numeric(s, errors="coerce")
                if pd.notna(num) and 30000 < float(num) < 60000:
                    dt = pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(num))
        if pd.isna(dt):
            return pd.NaT
        return fix_swapped_dmy_if_future(pd.Timestamp(dt), ref=ref)

    return series.map(_one)


def add_case_columns(df: pd.DataFrame, proto_col: str, mat_col: str) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        out["_protocolo_norm"] = pd.Series(dtype=str)
        out["_matricula_norm"] = pd.Series(dtype=str)
        out["chave_caso"] = pd.Series(dtype=str)
        out["matricula_ausente"] = pd.Series(dtype=bool)
        return out
    out["_protocolo_norm"] = out[proto_col].map(norm_protocolo).astype(str)
    out["_matricula_norm"] = out[mat_col].map(norm_matricula).astype(str)
    out["chave_caso"] = out["_protocolo_norm"] + "|" + out["_matricula_norm"]
    out["matricula_ausente"] = out["_matricula_norm"] == ""
    return out


def dedupe_by_case_key(df: pd.DataFrame, date_col: str | None = None) -> pd.DataFrame:
    if df.empty or "chave_caso" not in df.columns:
        return df
    out = df.copy()
    if date_col and date_col in out.columns:
        out["_sort_dt"] = parse_excel_date(out[date_col])
        out = out.sort_values("_sort_dt", na_position="first")
    return out.drop_duplicates(subset=["chave_caso"], keep="last").drop(
        columns=[c for c in ("_sort_dt",) if c in out.columns]
    )


def _period_bounds(inicio, fim) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    start = pd.Timestamp(inicio) if inicio is not None else None
    end = (
        pd.Timestamp(fim) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        if fim is not None
        else None
    )
    return start, end


def _date_in_period(dt: pd.Series, inicio, fim) -> pd.Series:
    """Máscara booleana: data válida dentro de [inicio, fim]."""
    parsed = parse_excel_date(dt)
    mask = parsed.notna()
    start, end = _period_bounds(inicio, fim)
    if start is not None:
        mask &= parsed >= start
    if end is not None:
        mask &= parsed <= end
    return mask


def filter_period(df: pd.DataFrame, date_col: str, inicio, fim) -> pd.DataFrame:
    if df.empty or date_col not in df.columns or (inicio is None and fim is None):
        return df
    return df.loc[_date_in_period(df[date_col], inicio, fim)].copy()


def filter_period_any(df: pd.DataFrame, date_cols: list[str], inicio, fim) -> pd.DataFrame:
    """Mantém a linha se QUALQUER das colunas de data cair no período."""
    if df.empty or (inicio is None and fim is None):
        return df
    cols = [c for c in date_cols if c in df.columns]
    if not cols:
        return df
    mask = pd.Series(False, index=df.index)
    for col in cols:
        mask |= _date_in_period(df[col], inicio, fim)
    return df.loc[mask].copy()


def na_effective_date(df: pd.DataFrame) -> pd.Series:
    """Data efetiva da falha NA: notificação se válida; senão cadastro."""
    if df.empty:
        return pd.Series(dtype="datetime64[ns]")
    cad = (
        parse_excel_date(df["DATA DE CADASTRO"])
        if "DATA DE CADASTRO" in df.columns
        else pd.Series(pd.NaT, index=df.index)
    )
    notif = (
        parse_excel_date(df["DATA DE NOTIFICAÇÃO"])
        if "DATA DE NOTIFICAÇÃO" in df.columns
        else pd.Series(pd.NaT, index=df.index)
    )
    return notif.where(notif.notna(), cad)


def norm_batimento_text(value) -> str:
    """Texto normalizado para batimento contestação (cliente/etapa/cenário)."""
    s = safe_str(value)
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def contestacao_batimento_key(
    *,
    protocolo,
    cliente: str = "",
    etapa: str = "",
    matricula="",
    cenario: str = "",
    tipo_falha: str = "",
) -> str:
    """Chave de batimento: protocolo + cliente + etapa + matrícula + sinalização (cenário)."""
    sinalizacao = norm_batimento_text(cenario) or norm_batimento_text(tipo_falha)
    return "|".join(
        (
            norm_protocolo(protocolo),
            norm_batimento_text(cliente),
            norm_batimento_text(etapa),
            norm_matricula(matricula),
            sinalizacao,
        )
    )


def contestacao_batimento_key_from_row(row) -> str:
    """Extrai chave de batimento de linha Contestação (EO ou Excel)."""
    mat_col = None
    for candidate in ("Matrícula", "Matricula", "Matrícula Agente"):
        if candidate in row.index:
            mat_col = candidate
            break
    return contestacao_batimento_key(
        protocolo=row.get("Protocolo"),
        cliente=row.get("Cliente"),
        etapa=row.get("Etapa"),
        matricula=row.get(mat_col) if mat_col else "",
        cenario=row.get("Cenário") or row.get("Cenario"),
        tipo_falha=row.get("Tipo de falha"),
    )


def contestacao_batimento_keys_from_df(df: pd.DataFrame) -> set[str]:
    if df is None or df.empty:
        return set()
    keys: set[str] = set()
    for _, row in df.iterrows():
        key = contestacao_batimento_key_from_row(row)
        if key.replace("|", ""):
            keys.add(key)
    return keys
