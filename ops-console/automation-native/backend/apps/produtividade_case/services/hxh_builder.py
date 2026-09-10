# -*- coding: utf-8 -*-
"""Agrega Excel Case (hora + consolidado) → linhas no formato HxH."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from apps.produtividade.models import CASE_ETAPA, CASE_STAGE_GOAL
from apps.produtividade.services.excel_reader import ParsedRow
from apps.produtividade_case.services.source_path import extract_periodo_mes

TZ_BR = ZoneInfo("America/Sao_Paulo")

_HORA_TS_RE = re.compile(r"prod_hora_(\d{4}-\d{2}-\d{2})")


def _norm_mat(value: object) -> str:
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
    return text.strip().lower()


def _parse_br_dt(data_s: str, hora_s: str) -> datetime | None:
    data_s = (data_s or "").strip()
    hora_s = (hora_s or "").strip()
    if not data_s or data_s == "-":
        return None
    try:
        d = datetime.strptime(data_s, "%d/%m/%Y").date()
    except ValueError:
        return None
    t = time(0, 0, 0)
    if hora_s and hora_s != "-":
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                t = datetime.strptime(hora_s, fmt).time()
                break
            except ValueError:
                continue
    return datetime.combine(d, t, tzinfo=TZ_BR)


def _tempo_secs(text: object) -> int:
    s = "" if text is None else str(text).strip()
    if not s or s == "-":
        return 0
    parts = s.split(":")
    if len(parts) != 3:
        return 0
    try:
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        return max(0, h * 3600 + m * 60 + sec)
    except ValueError:
        return 0


def dia_from_prod_hora_path(path: Path) -> date | None:
    m = _HORA_TS_RE.search(path.name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def find_sibling_files(path: Path, periodo_mes: str) -> tuple[Path | None, Path | None]:
    """Localiza consolidado e prod_hora mais recentes no mesmo periodo_mes."""
    consolidado: Path | None = None
    hora: Path | None = None
    name = path.name.lower()
    if "relatorio_produtividade_consolidado" in name or name.startswith("relatorio_"):
        consolidado = path if path.is_file() else None
    if name.startswith("prod_hora_"):
        hora = path if path.is_file() else None

    # Sobe até achar pastas irmãs sob IDF Docs Relatórios
    for parent in [path.parent, *path.parents]:
        # parent = .../mai-2026 or report root
        candidates_cons = list(parent.glob("**/Relatorio_Produtividade_Consolidado_*.xlsx"))
        if not candidates_cons and parent.name.lower() == periodo_mes.lower():
            candidates_cons = list(parent.glob("Relatorio_Produtividade_Consolidado_*.xlsx"))
        # also search sibling month folders under reports root
        root = parent.parent if parent.name.lower() == periodo_mes.lower() else parent
        if not candidates_cons:
            candidates_cons = list(root.glob(f"**/{periodo_mes}/Relatorio_Produtividade_Consolidado_*.xlsx"))
        if candidates_cons:
            consolidado = consolidado or max(candidates_cons, key=lambda p: p.stat().st_mtime)

        candidates_hora = list(root.glob(f"**/{periodo_mes}/prod_hora_*.xlsx"))
        if not candidates_hora and parent.name.lower() == periodo_mes.lower():
            candidates_hora = list(parent.glob("prod_hora_*.xlsx"))
        if candidates_hora:
            hora = hora or max(candidates_hora, key=lambda p: p.stat().st_mtime)
        if consolidado or hora:
            break

    if path.is_file():
        if "consolidado" in path.name.lower():
            consolidado = path
        if path.name.lower().startswith("prod_hora_"):
            hora = path
    return consolidado, hora


def aggregate_consolidado_from_rows(
    rows: list[dict],
) -> dict[tuple[str, date, int], dict]:
    """(matricula, dia, hora) → {count, seconds} a partir de rows já parseadas."""
    buckets: dict[tuple[str, date, int], dict] = defaultdict(
        lambda: {"count": 0, "seconds": 0}
    )
    for row in rows:
        mat = _norm_mat(row.get("matricula_destino"))
        if not mat:
            continue
        dt = row.get("conclusao_destino_at")
        if not isinstance(dt, datetime):
            continue
        key = (mat, dt.date(), dt.hour)
        buckets[key]["count"] += 1
        try:
            secs = int(row.get("tempo_analise_segundos") or 0)
        except (TypeError, ValueError):
            secs = 0
        buckets[key]["seconds"] += max(0, secs)
    return dict(buckets)


def aggregate_consolidado(
    path: Path,
    *,
    rows: list[dict] | None = None,
) -> dict[tuple[str, date, int], dict]:
    """(matricula, dia, hora) → {count, seconds}."""
    if rows is not None:
        return aggregate_consolidado_from_rows(rows)

    df = pd.read_excel(path, sheet_name="Produtividade", engine="openpyxl")
    buckets: dict[tuple[str, date, int], dict] = defaultdict(lambda: {"count": 0, "seconds": 0})
    if df.empty:
        return {}
    for raw in df.to_dict(orient="records"):
        mat = _norm_mat(raw.get("Matricula Destino"))
        if not mat:
            continue
        dt = _parse_br_dt(
            str(raw.get("Data Conclusao Destino") or ""),
            str(raw.get("Hora Conclusao Destino") or ""),
        )
        if not dt:
            continue
        key = (mat, dt.date(), dt.hour)
        buckets[key]["count"] += 1
        buckets[key]["seconds"] += _tempo_secs(raw.get("Tempo de Analise"))
    return dict(buckets)


def aggregate_prod_hora(path: Path, dia: date) -> dict[tuple[str, date, int], int]:
    """(matricula, dia, hora) → qtd."""
    df = pd.read_excel(path, engine="openpyxl", index_col=0)
    out: dict[tuple[str, date, int], int] = {}
    if df.empty:
        return out
    for matricula, row in df.iterrows():
        mat = _norm_mat(matricula)
        if not mat or mat == "total hora":
            continue
        for col, val in row.items():
            if str(col).strip().upper() == "TOTAL AGENTE":
                continue
            try:
                hora = int(col)
            except (TypeError, ValueError):
                continue
            if hora < 0 or hora > 23:
                continue
            try:
                qtd = int(float(val)) if pd.notna(val) else 0
            except (TypeError, ValueError):
                qtd = 0
            if qtd <= 0:
                continue
            out[(mat, dia, hora)] = qtd
    return out


def build_case_parsed_rows(
    *,
    consolidado_path: Path | None,
    hora_path: Path | None,
    consolidado_rows: list[dict] | None = None,
) -> list[ParsedRow]:
    """
    Fonte C: volume (count) preferencialmente de prod_hora;
    segundos do consolidado; stage_goal fixo 320; etapa Case.

    `consolidado_rows` opcional evita segunda leitura do Excel no mesmo job.
    """
    seconds_map: dict[tuple[str, date, int], dict] = {}
    if consolidado_path and consolidado_path.is_file():
        seconds_map = aggregate_consolidado(
            consolidado_path, rows=consolidado_rows
        )

    counts_map: dict[tuple[str, date, int], int] = {}
    if hora_path and hora_path.is_file():
        dia = dia_from_prod_hora_path(hora_path)
        if dia is None:
            # fallback: maior dia presente no consolidado do mesmo mês
            if seconds_map:
                dia = max(k[1] for k in seconds_map)
            else:
                dia = datetime.now(TZ_BR).date()
        counts_map = aggregate_prod_hora(hora_path, dia)

    keys = set(seconds_map) | set(counts_map)
    rows: list[ParsedRow] = []
    for key in sorted(keys):
        mat, dia, hora = key
        count = counts_map.get(key)
        if count is None:
            count = int(seconds_map.get(key, {}).get("count") or 0)
        seconds = int(seconds_map.get(key, {}).get("seconds") or 0)
        if count <= 0 and seconds <= 0:
            continue
        recorded_at = datetime.combine(dia, time(hora, 0, 0), tzinfo=TZ_BR)
        rows.append(
            ParsedRow(
                matricula_norm=mat,
                etapa=CASE_ETAPA,
                analysis_seconds=seconds,
                analysis_count=max(count, 0),
                stage_goal=Decimal(CASE_STAGE_GOAL),
                recorded_at=recorded_at,
            )
        )
    return rows


def resolve_case_inputs(path: Path) -> tuple[str, Path | None, Path | None]:
    periodo = extract_periodo_mes(path)
    cons, hora = find_sibling_files(path, periodo)
    return periodo, cons, hora
