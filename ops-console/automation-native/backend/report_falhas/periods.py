# report_falhas/periods.py
from datetime import date, timedelta
import pandas as pd

from report_falhas.io.data_loader import safe_to_datetime
from report_falhas.config_report import AUDIT_GRACE_BUSINESS_DAYS, COL_DATA

def first_day(dt: date) -> date:
    return dt.replace(day=1)

def prev_month_first_day(dt: date) -> date:
    return date(dt.year - 1, 12, 1) if dt.month == 1 else date(dt.year, dt.month - 1, 1)

def month_last_day(dt: date) -> date:
    nxt = (dt.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)

def april_start(today: date) -> date:
    ano = today.year if today.month >= 4 else today.year - 1
    return date(ano, 4, 1)

def months_from_to(start_month_first: date, end_month_first: date) -> list[date]:
    out = []
    cur = start_month_first.replace(day=1)
    end = end_month_first.replace(day=1)
    while cur <= end:
        out.append(cur)
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out

def add_months_first_day(dt: date, delta_months: int) -> date:
    """Retorna o primeiro dia do mês deslocado por delta_months (pode ser negativo)."""
    y = dt.year
    m = dt.month + int(delta_months)
    while m <= 0:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return date(y, m, 1)


def next_calendar_month_range(ref: date) -> tuple[date, date]:
    """Primeiro e último dia do mês calendário seguinte a ref."""
    start = add_months_first_day(ref.replace(day=1), 1)
    end = month_last_day(start)
    return start, end

def humanize_ymd(start: date, end: date) -> str:
    if pd.isna(start) or pd.isna(end) or start is None or end is None:
        return '—'
    s = pd.Timestamp(start).date()
    e = pd.Timestamp(end).date()
    if e < s:
        s, e = e, s
    years = e.year - s.year
    months = e.month - s.month
    days = e.day - s.day
    if days < 0:
        months -= 1
        prev_month = (e.month - 1) or 12
        prev_year = e.year - 1 if e.month == 1 else e.year
        days += (pd.Timestamp(prev_year, prev_month, 1).days_in_month)
    if months < 0:
        months += 12
        years -= 1
    parts = []
    if years > 0:
        parts.append(f"{years} {'ano' if years == 1 else 'anos'}")
    if months > 0:
        parts.append(f"{months} {'mês' if months == 1 else 'meses'}")
    if days > 0 or not parts:
        parts.append(f"{days} {'dia' if days == 1 else 'dias'}")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} e {parts[1]}"
    return f"{parts[0]}, {parts[1]} e {parts[2]}"

def filter_by_date_range(df: pd.DataFrame, start_d: date, end_d: date, date_col: str):
    if start_d is None or end_d is None or date_col not in df.columns:
        return df.iloc[0:0].copy()
    mask = (df[date_col].dt.date >= start_d) & (df[date_col].dt.date <= end_d)
    return df.loc[mask].copy()


def get_comparativo_by_same_period(
    df: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    prev_start: date,
    date_col: str,
):
    """Comparativo padrão: mesma duração em dias corridos no mês anterior.

    Ex.: período atual 01/05 a 07/05 (7 dias) → comparativo 01/04 a 07/04.
    """
    return get_comparativo_by_same_period_on(df, cur_start, cur_end, prev_start, date_col)


def dias_corridos_periodo(cur_start: date | None, cur_end: date | None) -> int:
    if not cur_start or not cur_end or cur_end < cur_start:
        return 0
    return (cur_end - cur_start).days + 1


def label_comparativo_mes_anterior(
    cur_start: date | None,
    cur_end: date | None,
    *,
    mes_atual_zero_falhas: bool = False,
) -> str:
    """Texto curto para cards/HTML do comparativo."""
    if mes_atual_zero_falhas:
        return "mês anterior inteiro — período atual sem falhas"
    dias = dias_corridos_periodo(cur_start, cur_end)
    if dias <= 0:
        return "mesmo número de dias no mês anterior"
    if dias == 1:
        return "1 dia de calendário no mês anterior (mesma posição relativa)"
    return f"{dias} dias de calendário no mês anterior (do dia 1 do mês)"


def nth_weekday_of_month(year: int, month: int, n: int) -> date:
    """Retorna o n-ésimo dia útil (seg–sex) do mês calendário."""
    count = 0
    d = date(year, month, 1)
    last = month_last_day(d)
    while d <= last:
        if d.weekday() < 5:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    return last


def audit_grace_end(cur_end: date, *, business_days: int | None = None) -> date:
    """Último dia da carência de auditoria: n-ésimo dia útil do mês seguinte a cur_end."""
    n = business_days if business_days is not None else AUDIT_GRACE_BUSINESS_DAYS
    next_month = add_months_first_day(cur_end.replace(day=1), 1)
    return nth_weekday_of_month(next_month.year, next_month.month, n)


def is_fechamento_mes(ref: date, cur_start: date, cur_end: date, today: date) -> bool:
    """True quando o report está em modo fechamento mensal (mês encerrado + carência operacional)."""
    ref_month = ref.replace(day=1)
    today_month = today.replace(day=1)
    if ref_month < today_month:
        return True
    if cur_end == month_last_day(ref_month) and today <= audit_grace_end(cur_end):
        return True
    return False


def effective_audit_end(
    cur_start: date,
    cur_end: date,
    ref: date,
    today: date,
    *,
    business_days: int | None = None,
) -> date:
    """Limite superior de Data Auditoria para spill: cur_end ou fim da carência no fechamento."""
    ref_month = ref.replace(day=1)
    if is_fechamento_mes(ref_month, cur_start, cur_end, today):
        return audit_grace_end(cur_end, business_days=business_days)
    return cur_end


def spill_auditoria_mask(
    audit_series: pd.Series,
    cur_start: date,
    effective_audit_end: date,
) -> pd.Series:
    """True onde a auditoria está fora de [cur_start, effective_audit_end] (fora de fase)."""
    if audit_series is None or audit_series.empty:
        return pd.Series(dtype=bool)
    aud_dates = audit_series.dt.date
    return audit_series.notna() & ((aud_dates < cur_start) | (aud_dates > effective_audit_end))


def mask_spill_auditoria(
    df: pd.DataFrame,
    audit_col: str,
    cur_start: date,
    cur_end: date,
    effective_audit_end: date,
) -> pd.Series:
    """Máscara de spill em df já filtrado por Data de Análise em [cur_start, cur_end]."""
    if df is None or df.empty or audit_col not in df.columns:
        return pd.Series(False, index=df.index if df is not None else [])
    series = df[audit_col]
    if not pd.api.types.is_datetime64_any_dtype(series):
        series = safe_to_datetime(series)
    return spill_auditoria_mask(series, cur_start, effective_audit_end)


def resolve_mtd_period(ref: date, *, today: date | None = None) -> tuple[date, date]:
    """MTD do mês de referência: dia 1 até hoje se for o mês corrente; senão mês cheio."""
    today = today or date.today()
    cur_start = first_day(ref)
    if cur_start.year == today.year and cur_start.month == today.month:
        cur_end = min(today, month_last_day(cur_start))
    else:
        cur_end = month_last_day(cur_start)
    return cur_start, cur_end


def get_reincidence_periods(today: date):
    cur_start = first_day(today)
    cur_end_today = min(today, month_last_day(cur_start))
    prev_start_full = prev_month_first_day(today)
    prev_end_full = month_last_day(prev_start_full)
    return prev_start_full, prev_end_full, cur_start, cur_end_today


def filter_by_date_range_on(df: pd.DataFrame, start_d: date, end_d: date, date_col: str) -> pd.DataFrame:
    """Filtra df entre start_d e end_d (inclusive) usando uma coluna de data específica.

    Se a coluna não estiver em datetime, tenta converter via safe_to_datetime.
    """
    if start_d is None or end_d is None or date_col not in df.columns:
        return df.iloc[0:0].copy()

    dfx = df.copy()
    try:
        if not pd.api.types.is_datetime64_any_dtype(dfx[date_col]):
            dfx[date_col] = safe_to_datetime(dfx[date_col])
    except Exception:
        return df.iloc[0:0].copy()

    dfx = dfx.dropna(subset=[date_col])
    if dfx.empty:
        return dfx

    mask = (dfx[date_col].dt.date >= start_d) & (dfx[date_col].dt.date <= end_d)
    return dfx.loc[mask].copy()


def get_comparativo_by_same_period_on(df: pd.DataFrame, cur_start: date, cur_end: date, prev_start: date, date_col: str):
    """Comparativo MTD com dias corridos usando coluna de data customizável.

    Calcula o mesmo intervalo de dias corridos no período anterior.
    Exemplo: se período atual é 01/05 a 07/05 (7 dias), o período anterior
    será 01/04 a 07/04 (mesmos 7 dias corridos).
    """
    if cur_end is None or cur_start is None or date_col not in df.columns:
        return 0, 0, None, None

    df_cur_mtd = filter_by_date_range_on(df, cur_start, cur_end, date_col)
    total_atual = len(df_cur_mtd)

    dias_intervalo = (cur_end - cur_start).days
    prev_end = prev_start + timedelta(days=dias_intervalo)

    df_prev_equal = filter_by_date_range_on(df, prev_start, prev_end, date_col)
    total_prev_equal = len(df_prev_equal)

    prev_equal_start = prev_start
    prev_equal_end = prev_end

    return total_atual, total_prev_equal, prev_equal_start, prev_equal_end