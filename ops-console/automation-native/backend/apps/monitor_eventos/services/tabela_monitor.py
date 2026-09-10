# -*- coding: utf-8 -*-
"""Transforma sessões monitor_evento_record na tabela_monitor (port da consulta M do PBI)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
from django.db.models import QuerySet
from django.utils import timezone

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.produtividade.models import ProductivityRecord

JORNADA_CUTOFF = time(5, 15, 0)
META_MADRUGADA_SUBTRACT = 1500  # 25 min
META_THRESHOLDS = (
    (25200, 19800),   # < 06:59:00 → 5.5h
    (28800, 24300),   # < 08:00:00 → 6.75h
)
META_DEFAULT = 27900  # 7.75h


def _local_recorded_at(recorded_at: datetime | pd.Timestamp) -> datetime:
    if isinstance(recorded_at, pd.Timestamp):
        recorded_at = recorded_at.to_pydatetime()
    if timezone.is_aware(recorded_at):
        return timezone.localtime(recorded_at)
    return recorded_at


def _to_local_timestamp(value) -> pd.Timestamp | pd.NaT:
    """Normaliza timestamp para fuso America/Sao_Paulo (hora de Brasília)."""
    if value is None:
        return pd.NaT
    try:
        if pd.isna(value):
            return pd.NaT
    except (TypeError, ValueError):
        pass
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return pd.NaT
    tz = timezone.get_current_timezone()
    if ts.tzinfo is None:
        # Naive: assume já é horário de Brasília.
        return ts.tz_localize(tz)
    return ts.tz_convert(tz)


def _data_jornada(dt: datetime | pd.Timestamp | None) -> date | None:
    if dt is None or pd.isna(dt):
        return None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    # Sempre avaliar cutoff 05:15 no horário de Brasília.
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    t = dt.time().replace(tzinfo=None)
    base = dt.date()
    if t < JORNADA_CUTOFF:
        return base - timedelta(days=1)
    return base


def _inicio_hora(dt: datetime | pd.Timestamp) -> datetime:
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.replace(minute=0, second=0, microsecond=0)


def _fim_hora_inclusiva(dt: datetime | pd.Timestamp) -> datetime:
    inicio = _inicio_hora(dt)
    if dt == inicio:
        return inicio - timedelta(hours=1)
    return inicio


def _gerar_buckets(inicio: datetime, fim: datetime) -> list[datetime]:
    inicio_bucket = _inicio_hora(inicio)
    fim_bucket = _fim_hora_inclusiva(fim)
    if fim_bucket < inicio_bucket:
        return []
    buckets: list[datetime] = []
    current = inicio_bucket
    while current <= fim_bucket:
        buckets.append(current)
        current += timedelta(hours=1)
    return buckets


def _sessoes_validas_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(
            columns=[
                "data_jornada",
                "usuario",
                "data_evento",
                "data_segundo_evento",
            ]
        )

    df = pd.DataFrame(records)
    df["usuario"] = df["matricula_usuario"].astype(str).str.strip().str.upper()
    # Força horário de Brasília antes de bucketing/hora — senão UTC vaza (23h→02h).
    df["data_evento"] = df["data_evento"].map(_to_local_timestamp)
    df["data_segundo_evento"] = df["data_segundo_evento"].map(_to_local_timestamp)
    df["evento"] = df["evento"].fillna("").astype(str).str.strip()
    df["segundo_evento"] = df["segundo_evento"].fillna("").astype(str).str.strip()

    mask = (
        df["evento"].str.contains("Autentica", case=False, na=False)
        & df["segundo_evento"].str.lower().eq("logout")
        & df["data_evento"].notna()
        & df["data_segundo_evento"].notna()
        & (df["data_segundo_evento"] > df["data_evento"])
    )
    df = df[mask].copy()
    if df.empty:
        return pd.DataFrame(
            columns=[
                "data_jornada",
                "usuario",
                "data_evento",
                "data_segundo_evento",
            ]
        )

    df["data_jornada"] = df["data_evento"].apply(_data_jornada)
    return df[["data_jornada", "usuario", "data_evento", "data_segundo_evento"]]


def _expandir_buckets_sessao(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        dt_ini = row.data_evento
        dt_fim = row.data_segundo_evento
        for bucket_start in _gerar_buckets(dt_ini, dt_fim):
            bucket_end = bucket_start + timedelta(hours=1)
            inicio_efetivo = max(dt_ini, bucket_start)
            fim_efetivo = min(dt_fim, bucket_end)
            if fim_efetivo > inicio_efetivo:
                segundos = (fim_efetivo - inicio_efetivo).total_seconds()
                rows.append(
                    {
                        "data_jornada": row.data_jornada,
                        "usuario": row.usuario,
                        "hora_inicio_bucket": bucket_start,
                        "hora_fim_bucket": bucket_end,
                        "primeiro_login": inicio_efetivo,
                        "ultimo_logout": fim_efetivo,
                        "periodo_logado": int(segundos),
                    }
                )
    if not rows:
        return pd.DataFrame(
            columns=[
                "data_jornada",
                "usuario",
                "hora_inicio_bucket",
                "hora_fim_bucket",
                "primeiro_login",
                "ultimo_logout",
                "periodo_logado",
            ]
        )
    return pd.DataFrame(rows)


def _agrupar_logado_por_bucket(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "data_jornada",
                "usuario",
                "hora_inicio_bucket",
                "hora_fim_bucket",
                "total_logado",
                "primeiro_login",
                "ultimo_logout",
            ]
        )
    grouped = (
        df.groupby(
            ["data_jornada", "usuario", "hora_inicio_bucket", "hora_fim_bucket"],
            as_index=False,
        )
        .agg(
            total_logado=("periodo_logado", "sum"),
            primeiro_login=("primeiro_login", "min"),
            ultimo_logout=("ultimo_logout", "max"),
        )
    )
    grouped["total_logado"] = grouped["total_logado"].astype(int)
    return grouped


def _login_logout_dia(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "data_jornada",
                "usuario",
                "primeira_data_login_dia",
                "ultima_data_logout_dia",
            ]
        )
    return (
        df.groupby(["data_jornada", "usuario"], as_index=False)
        .agg(
            primeira_data_login_dia=("data_evento", "min"),
            ultima_data_logout_dia=("data_segundo_evento", "max"),
        )
    )


def _criar_grade_horas(login_logout: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in login_logout.itertuples(index=False):
        for bucket_start in _gerar_buckets(
            row.primeira_data_login_dia,
            row.ultima_data_logout_dia,
        ):
            rows.append(
                {
                    "data_jornada": row.data_jornada,
                    "usuario": row.usuario,
                    "hora_inicio_bucket": bucket_start,
                    "hora_fim_bucket": bucket_start + timedelta(hours=1),
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "data_jornada",
                "usuario",
                "hora_inicio_bucket",
                "hora_fim_bucket",
            ]
        )
    return pd.DataFrame(rows)


def _mesclar_grade_com_logado(grade: pd.DataFrame, logado: pd.DataFrame) -> pd.DataFrame:
    if grade.empty:
        return pd.DataFrame(
            columns=[
                "data_jornada",
                "usuario",
                "hora_inicio_bucket",
                "hora_fim_bucket",
                "total_logado",
                "primeiro_login",
                "ultimo_logout",
            ]
        )
    merged = grade.merge(
        logado,
        on=["data_jornada", "usuario", "hora_inicio_bucket", "hora_fim_bucket"],
        how="left",
    )
    merged["total_logado"] = merged["total_logado"].fillna(0).astype(int)
    return merged


def _adicionar_linha_null_dia(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    null_rows = (
        df[["data_jornada", "usuario"]]
        .drop_duplicates()
        .assign(
            hora_inicio_bucket=pd.NaT,
            hora_fim_bucket=pd.NaT,
            total_logado=pd.NA,
            primeiro_login=pd.NaT,
            ultimo_logout=pd.NaT,
            hora=pd.NA,
        )
    )
    return pd.concat([df, null_rows], ignore_index=True)


def _calc_meta_tempo(
    carga_horaria: float | int | None,
    primeiro_login_dia: datetime | pd.Timestamp | None,
    ultimo_logout_dia: datetime | pd.Timestamp | None,
) -> int:
    if carga_horaria is None or pd.isna(carga_horaria):
        carga = 0
    else:
        carga = int(carga_horaria)

    meta = META_DEFAULT
    for threshold, value in META_THRESHOLDS:
        if carga < threshold:
            meta = value
            break

    subtrair = 0
    if primeiro_login_dia is not None and not pd.isna(primeiro_login_dia):
        if isinstance(primeiro_login_dia, pd.Timestamp):
            primeiro_login_dia = primeiro_login_dia.to_pydatetime()
        if primeiro_login_dia.time() >= time(20, 10, 0):
            subtrair = META_MADRUGADA_SUBTRACT
    if ultimo_logout_dia is not None and not pd.isna(ultimo_logout_dia):
        if isinstance(ultimo_logout_dia, pd.Timestamp):
            ultimo_logout_dia = ultimo_logout_dia.to_pydatetime()
        if ultimo_logout_dia.time() <= time(5, 59, 0):
            subtrair = META_MADRUGADA_SUBTRACT

    return max(0, meta - subtrair)


def _calc_tempo_off(
    primeiro_login_dia,
    ultimo_logout_dia,
    hora_inicio_bucket,
    hora_fim_bucket,
    total_logado,
) -> int:
    if pd.isna(hora_inicio_bucket) or pd.isna(hora_fim_bucket):
        return 0
    if pd.isna(primeiro_login_dia) or pd.isna(ultimo_logout_dia):
        return 0

    inicio = max(primeiro_login_dia, hora_inicio_bucket)
    fim = min(hora_fim_bucket, ultimo_logout_dia)
    if pd.isna(inicio) or pd.isna(fim) or fim <= inicio:
        return 0

    segundos = int((fim - inicio).total_seconds())
    logged = 0 if pd.isna(total_logado) else int(total_logado)
    return max(0, segundos - logged)


def _adicionar_derivadas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    hourly = df[df["hora_inicio_bucket"].notna()].copy()
    if hourly.empty:
        dia_agg = pd.DataFrame(columns=["data_jornada", "usuario", "primeiro_login_dia", "ultimo_logout_dia"])
    else:
        dia_agg = (
            hourly.groupby(["data_jornada", "usuario"], as_index=False)
            .agg(
                primeiro_login_dia=("primeiro_login", "min"),
                ultimo_logout_dia=("ultimo_logout", "max"),
            )
        )

    df = df.merge(dia_agg, on=["data_jornada", "usuario"], how="left")
    df["carga_horaria"] = df.apply(
        lambda row: int((row["ultimo_logout_dia"] - row["primeiro_login_dia"]).total_seconds())
        if pd.notna(row["primeiro_login_dia"]) and pd.notna(row["ultimo_logout_dia"])
        else 0,
        axis=1,
    )
    df["meta_tempo"] = df.apply(
        lambda row: _calc_meta_tempo(
            row["carga_horaria"],
            row["primeiro_login_dia"],
            row["ultimo_logout_dia"],
        ),
        axis=1,
    )
    df["tempo_off"] = df.apply(
        lambda row: _calc_tempo_off(
            row["primeiro_login_dia"],
            row["ultimo_logout_dia"],
            row["hora_inicio_bucket"],
            row["hora_fim_bucket"],
            row["total_logado"],
        ),
        axis=1,
    )
    total_logado = pd.to_numeric(df["total_logado"], errors="coerce").fillna(0).astype(int)
    df["meta_hora"] = total_logado + df["tempo_off"].astype(int)
    return df


def _normalize_date(value: date | datetime | pd.Timestamp | None) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def jornada_from_recorded_at(
    recorded_at: datetime | pd.Timestamp | None,
) -> date | None:
    """Data de jornada (cutoff 05:15, horário de Brasília)."""
    if recorded_at is None:
        return None
    return _normalize_date(_data_jornada(_local_recorded_at(recorded_at)))


def build_tempo_analise_lookup(
    data_jornadas: set[date],
    matriculas: set[str],
) -> dict[tuple[str, date, int], int]:
    """Soma analysis_seconds por (matricula_upper, data_jornada, hora)."""
    jornadas = {_normalize_date(d) for d in data_jornadas}
    jornadas.discard(None)
    if not jornadas or not matriculas:
        return {}

    matriculas_lower = {m.strip().lower() for m in matriculas if m}
    if not matriculas_lower:
        return {}

    min_jornada = min(jornadas)
    max_jornada = max(jornadas)
    start, _ = jornada_window(min_jornada)
    _, end = jornada_window(max_jornada)

    rows = ProductivityRecord.objects.filter(
        recorded_at__gte=start,
        recorded_at__lte=end,
        matricula_norm__in=matriculas_lower,
    ).values("matricula_norm", "recorded_at", "analysis_seconds")

    lookup: dict[tuple[str, date, int], int] = {}
    for row in rows:
        recorded_at = row["recorded_at"]
        if recorded_at is None:
            continue
        local_dt = _local_recorded_at(recorded_at)
        jornada = jornada_from_recorded_at(recorded_at)
        if jornada is None or jornada not in jornadas:
            continue
        matricula = str(row["matricula_norm"] or "").strip().upper()
        if not matricula:
            continue
        hora = int(local_dt.hour)
        key = (matricula, jornada, hora)
        lookup[key] = lookup.get(key, 0) + int(row["analysis_seconds"] or 0)
    return lookup


def _calc_tempo_ocioso(tempo_logado: int, tempo_analise: int) -> int:
    if tempo_analise < tempo_logado:
        return tempo_logado - tempo_analise
    return 0


def _adicionar_tempo_ocioso(
    df: pd.DataFrame,
    tempo_analise_lookup: dict[tuple[str, date, int], int] | None,
) -> pd.DataFrame:
    if df.empty:
        return df

    lookup = tempo_analise_lookup or {}
    hourly_mask = df["hora_inicio_bucket"].notna()

    df = df.copy()
    df["tempo_analise"] = pd.NA
    df["tempo_logado"] = pd.NA
    df["tempo_ocioso"] = pd.NA

    for idx in df.index[hourly_mask]:
        row = df.loc[idx]
        hora = row["hora"]
        if pd.isna(hora):
            continue
        tempo_analise = lookup.get(
            (
                row["usuario"],
                _normalize_date(row["data_jornada"]),
                int(hora),
            ),
            0,
        )
        tempo_logado = int(row["total_logado"]) if pd.notna(row["total_logado"]) else 0
        df.at[idx, "tempo_analise"] = tempo_analise
        df.at[idx, "tempo_logado"] = tempo_logado
        df.at[idx, "tempo_ocioso"] = _calc_tempo_ocioso(tempo_logado, tempo_analise)

    hourly_totals = (
        df[hourly_mask]
        .groupby(["data_jornada", "usuario"], as_index=False)
        .agg(
            tempo_logado_dia=("tempo_logado", "sum"),
            tempo_ocioso_dia=("tempo_ocioso", "sum"),
        )
    )
    df = df.drop(columns=["tempo_logado_dia", "tempo_ocioso_dia"], errors="ignore")
    df = df.merge(hourly_totals, on=["data_jornada", "usuario"], how="left")
    df["tempo_logado_dia"] = pd.to_numeric(df["tempo_logado_dia"], errors="coerce").fillna(0).astype(int)
    df["tempo_ocioso_dia"] = pd.to_numeric(df["tempo_ocioso_dia"], errors="coerce").fillna(0).astype(int)
    return df


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (float, int)) and pd.isna(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _dataframe_to_results(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []

    results: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        hora_val = None if pd.isna(row.hora) else int(row.hora)
        total_logado = None if pd.isna(row.total_logado) else int(row.total_logado)
        tempo_logado = None if pd.isna(row.tempo_logado) else int(row.tempo_logado)
        tempo_ocioso = None if pd.isna(row.tempo_ocioso) else int(row.tempo_ocioso)
        tempo_analise = None if pd.isna(row.tempo_analise) else int(row.tempo_analise)
        results.append(
            {
                "data_jornada": _serialize_value(row.data_jornada),
                "matricula_usuario": row.usuario,
                "hora": hora_val,
                "hora_inicio_bucket": _serialize_value(row.hora_inicio_bucket),
                "hora_fim_bucket": _serialize_value(row.hora_fim_bucket),
                "total_logado": total_logado,
                "tempo_logado": tempo_logado,
                "tempo_ocioso": tempo_ocioso,
                "tempo_analise": tempo_analise,
                "tempo_logado_dia": int(row.tempo_logado_dia),
                "tempo_ocioso_dia": int(row.tempo_ocioso_dia),
                "primeiro_login": _serialize_value(row.primeiro_login),
                "ultimo_logout": _serialize_value(row.ultimo_logout),
                "primeiro_login_dia": _serialize_value(row.primeiro_login_dia),
                "ultimo_logout_dia": _serialize_value(row.ultimo_logout_dia),
                "carga_horaria": int(row.carga_horaria),
                "meta_tempo": int(row.meta_tempo),
                "tempo_off": int(row.tempo_off),
                "meta_hora": int(row.meta_hora),
            }
        )
    return results


def build_tabela_monitor_from_records(
    records: list[dict[str, Any]],
    tempo_analise_lookup: dict[tuple[str, date, int], int] | None = None,
) -> list[dict[str, Any]]:
    """Constrói tabela_monitor a partir de dicts com campos de MonitorEventoRecord."""
    sessoes = _sessoes_validas_df(records)
    if sessoes.empty:
        return []

    if tempo_analise_lookup is None:
        data_jornadas = set(sessoes["data_jornada"].dropna())
        matriculas = set(sessoes["usuario"].dropna())
        tempo_analise_lookup = build_tempo_analise_lookup(data_jornadas, matriculas)

    buckets = _expandir_buckets_sessao(sessoes)
    logado = _agrupar_logado_por_bucket(buckets)
    login_logout = _login_logout_dia(sessoes)
    grade = _criar_grade_horas(login_logout)
    resultado = _mesclar_grade_com_logado(grade, logado)
    resultado["hora"] = resultado["hora_inicio_bucket"].apply(
        lambda dt: (
            int(_to_local_timestamp(dt).hour)
            if pd.notna(dt)
            else pd.NA
        )
    )
    resultado = _adicionar_linha_null_dia(resultado)
    resultado = _adicionar_derivadas(resultado)
    resultado = _adicionar_tempo_ocioso(resultado, tempo_analise_lookup)
    return _dataframe_to_results(resultado)


def records_queryset_to_dicts(qs: QuerySet[MonitorEventoRecord]) -> list[dict[str, Any]]:
    return [
        {
            "matricula_usuario": row.matricula_usuario,
            "data_evento": row.data_evento,
            "data_segundo_evento": row.data_segundo_evento,
            "evento": row.evento,
            "segundo_evento": row.segundo_evento,
        }
        for row in qs
    ]


def jornada_window(data_jornada: date) -> tuple[datetime, datetime]:
    """Intervalo de data_evento que cobre uma data de jornada."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(data_jornada, JORNADA_CUTOFF), tz)
    end = timezone.make_aware(
        datetime.combine(data_jornada + timedelta(days=1), JORNADA_CUTOFF)
        - timedelta(seconds=1),
        tz,
    )
    return start, end


def build_tabela_monitor(qs: QuerySet[MonitorEventoRecord] | None = None) -> list[dict[str, Any]]:
    queryset = qs if qs is not None else MonitorEventoRecord.objects.all()
    records = records_queryset_to_dicts(queryset)
    built = build_tabela_monitor_from_records(records)
    return built
