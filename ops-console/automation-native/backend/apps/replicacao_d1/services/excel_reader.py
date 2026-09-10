# -*- coding: utf-8 -*-
"""Leitura tipada do Excel D-1 (abas Plano + Resumo + Dashboard)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from django.utils import timezone as dj_timezone

from apps.replicacao_d1.normalization import normalize_protocolo
from apps.replicacao_d1.services.read_result import ReadResult

_DATE_BR = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
REQUIRED_PLANO_COLUMNS = ("Protocolo",)
REQUIRED_RESUMO_COLUMNS = ("Workflow",)
_DATETIME_BR = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$"
)


def _as_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    return text


def _as_optional_int(value: Any) -> int | None:
    text = _as_str(value)
    if not text:
        return None
    try:
        return int(float(str(text).replace(",", ".").replace("%", "")))
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> float | None:
    text = _as_str(value)
    if not text:
        return None
    try:
        return float(str(text).replace(",", ".").replace("%", ""))
    except (TypeError, ValueError):
        return None


def parse_date_br(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date()
        except Exception:
            pass
    text = _as_str(value)
    if not text:
        return None
    match = _DATE_BR.match(text.split()[0] if " " in text else text)
    if match:
        day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_datetime_br(value: Any) -> datetime | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        dt = value
        if dj_timezone.is_naive(dt):
            return dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())
        return dt
    if isinstance(value, date) and not isinstance(value, datetime):
        dt = datetime(value.year, value.month, value.day)
        return dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())
    if hasattr(value, "to_pydatetime"):
        try:
            dt = value.to_pydatetime()
            if isinstance(dt, datetime) and dj_timezone.is_naive(dt):
                return dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())
            return dt
        except Exception:
            pass
    text = _as_str(value)
    if not text:
        return None
    match = _DATETIME_BR.match(text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        second = int(match.group(6) or 0)
        try:
            dt = datetime(year, month, day, hour, minute, second)
            return dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
        if dj_timezone.is_naive(dt):
            return dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())
        return dt
    except ValueError:
        d = parse_date_br(text)
        if not d:
            return None
        dt = datetime(d.year, d.month, d.day)
        return dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())


@dataclass
class ParsedWorkflow:
    workflow_config: str
    workflow_d1: str = ""
    workflow_brflow: str = ""
    canal_destino: str = ""
    cliente: str = ""
    segmento: str = ""
    categoria: str = ""
    fila: str = ""
    amostra_diaria: int | None = None
    amostra_solicitada: int | None = None
    amostra_efetiva: int | None = None
    protocolos_salvos: int | None = None
    pct_atingido: float | None = None
    disponivel_d1: int | None = None
    status_amostra: str = ""
    status_brflow: str = ""
    data_hora_upload_brflow: str = ""
    upload_em: datetime | None = None
    faixa_horaria: str = ""


@dataclass
class ParsedProtocolo:
    protocolo: str
    workflow_config: str
    workflow_d1: str = ""
    data_analise: datetime | None = None
    hora: int | None = None
    canal_destino: str = ""
    status_brflow: str = ""


@dataclass
class ParsedReport:
    run_id: str
    data_referencia_d1: date
    data_execucao: datetime | None = None
    parquet_referencia: str = ""
    auditores_ativos_brflow: int | None = None
    auditores_ativos_case: int | None = None
    workflows: list[ParsedWorkflow] = field(default_factory=list)
    protocolos: list[ParsedProtocolo] = field(default_factory=list)
    read_stats: ReadResult | None = None


def _col(df: pd.DataFrame, *names: str) -> str | None:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        found = lower_map.get(name.strip().lower())
        if found is not None:
            return found
    return None


def _require_columns(df: pd.DataFrame, required: tuple[str, ...], sheet: str, result: ReadResult) -> bool:
    missing = [name for name in required if _col(df, name) is None]
    if missing:
        result.add_error(
            line=0,
            column=",".join(missing),
            code="STRUCTURE",
            message=f"Colunas obrigatórias ausentes na aba {sheet}",
        )
        return False
    return True


def _dashboard_metric_map(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    col_m = _col(df, "Metrica", "Métrica", "metrica")
    col_v = _col(df, "Valor", "valor")
    if not col_m or not col_v:
        return {}
    out: dict[str, Any] = {}
    for _, row in df.iterrows():
        key = _as_str(row.get(col_m))
        if key:
            out[key] = row.get(col_v)
    return out


def read_replicacao_d1_excel(path: Path, *, run_id: str = "") -> ParsedReport:
    path = Path(path)
    stats = ReadResult[ParsedProtocolo]()
    df_resumo = pd.read_excel(path, sheet_name="Resumo", engine="openpyxl")
    df_plano = pd.read_excel(path, sheet_name="Plano", engine="openpyxl")
    try:
        df_dashboard = pd.read_excel(path, sheet_name="Dashboard", engine="openpyxl")
    except Exception:
        df_dashboard = pd.DataFrame()

    if not _require_columns(df_resumo, REQUIRED_RESUMO_COLUMNS, "Resumo", stats):
        raise ValueError(f"Estrutura inválida (Resumo): {path}")
    if not df_plano.empty and not _require_columns(df_plano, REQUIRED_PLANO_COLUMNS, "Plano", stats):
        raise ValueError(f"Estrutura inválida (Plano): {path}")

    resolved_run = run_id or ""
    data_ref: date | None = None
    data_exec: datetime | None = None
    parquet_ref = ""
    aud_brflow: int | None = None
    aud_case: int | None = None

    metrics = _dashboard_metric_map(df_dashboard)
    if metrics:
        resolved_run = resolved_run or _as_str(metrics.get("RunId"))
        data_ref = parse_date_br(metrics.get("DataReferenciaD1")) or data_ref
        data_exec = parse_datetime_br(metrics.get("DataFimExecucao")) or data_exec
        parquet_ref = _as_str(metrics.get("ParquetReferencia")) or parquet_ref
        aud_brflow = _as_optional_int(metrics.get("AuditoresAtivos"))
        aud_case = _as_optional_int(metrics.get("AuditoresAtivosCase"))

    col_wf = _col(df_resumo, "Workflow")
    if col_wf and not df_resumo.empty:
        first = None
        for _, row in df_resumo.iterrows():
            if _as_str(row.get(col_wf)) != "TOTAL":
                first = row
                break
        if first is not None:
            resolved_run = resolved_run or _as_str(
                first.get(_col(df_resumo, "RunId") or "RunId")
            )
            col_data_ref = _col(df_resumo, "Data Referencia D1", "Data Referência D1")
            if col_data_ref:
                data_ref = data_ref or parse_date_br(first.get(col_data_ref))
            col_data_exec = _col(df_resumo, "Data Execucao", "Data Execução")
            if col_data_exec:
                data_exec = data_exec or parse_datetime_br(first.get(col_data_exec))
            col_parquet = _col(df_resumo, "Parquet Referencia", "Parquet Referência")
            if col_parquet:
                parquet_ref = parquet_ref or _as_str(first.get(col_parquet))
            col_aud = _col(df_resumo, "Auditores Ativos")
            if col_aud and aud_brflow is None:
                aud_brflow = _as_optional_int(first.get(col_aud))

    if not resolved_run:
        from apps.replicacao_d1.services.source_path import parse_run_id_from_name

        resolved_run = parse_run_id_from_name(path.name)
    if not resolved_run:
        stats.add_error(line=0, column="RunId", code="STRUCTURE", message="run_id ausente")
        raise ValueError(f"run_id ausente no relatório: {path}")
    if data_ref is None:
        stats.add_error(line=0, column="Data Referencia D1", code="STRUCTURE", message="data ausente")
        raise ValueError(f"data_referencia_d1 ausente no relatório: {path}")

    workflows: list[ParsedWorkflow] = []
    status_by_wf: dict[str, str] = {}
    if col_wf is not None:
        c_d1 = _col(df_resumo, "Workflow D1")
        c_br = _col(df_resumo, "Workflow BRFlow")
        c_canal = _col(df_resumo, "Canal Destino")
        c_cli = _col(df_resumo, "Cliente")
        c_seg = _col(df_resumo, "Segmento")
        c_cat = _col(df_resumo, "Categoria")
        c_fila = _col(df_resumo, "Fila")
        c_ad = _col(df_resumo, "Amostra Diaria", "Amostra Diária")
        c_as = _col(df_resumo, "Amostra Solicitada")
        c_ae = _col(df_resumo, "Amostra Efetiva")
        c_ps = _col(df_resumo, "Protocolos Salvos")
        c_pct = _col(df_resumo, "Pct Atingido")
        c_disp = _col(df_resumo, "Disponivel D1", "Disponível D1")
        c_st = _col(df_resumo, "Status")
        c_st_br = _col(df_resumo, "Status BRFlow")
        c_up = _col(df_resumo, "Data Hora Upload BRFlow")
        c_fx = _col(df_resumo, "Faixa Horaria")

        for _, row in df_resumo.iterrows():
            wf = _as_str(row.get(col_wf))
            if not wf or wf == "TOTAL":
                continue
            status_br = _as_str(row.get(c_st_br)) if c_st_br else ""
            status_by_wf[wf] = status_br or "PENDENTE"
            upload_raw = _as_str(row.get(c_up)) if c_up else ""
            workflows.append(
                ParsedWorkflow(
                    workflow_config=wf,
                    workflow_d1=_as_str(row.get(c_d1)) if c_d1 else "",
                    workflow_brflow=_as_str(row.get(c_br)) if c_br else "",
                    canal_destino=_as_str(row.get(c_canal)) if c_canal else "",
                    cliente=_as_str(row.get(c_cli)) if c_cli else "",
                    segmento=_as_str(row.get(c_seg)) if c_seg else "",
                    categoria=_as_str(row.get(c_cat)) if c_cat else "",
                    fila=_as_str(row.get(c_fila)) if c_fila else "",
                    amostra_diaria=_as_optional_int(row.get(c_ad)) if c_ad else None,
                    amostra_solicitada=_as_optional_int(row.get(c_as)) if c_as else None,
                    amostra_efetiva=_as_optional_int(row.get(c_ae)) if c_ae else None,
                    protocolos_salvos=_as_optional_int(row.get(c_ps)) if c_ps else None,
                    pct_atingido=_as_optional_float(row.get(c_pct)) if c_pct else None,
                    disponivel_d1=_as_optional_int(row.get(c_disp)) if c_disp else None,
                    status_amostra=_as_str(row.get(c_st)) if c_st else "",
                    status_brflow=status_br or "PENDENTE",
                    data_hora_upload_brflow=upload_raw,
                    upload_em=parse_datetime_br(upload_raw) if upload_raw else None,
                    faixa_horaria=_as_str(row.get(c_fx)) if c_fx else "",
                )
            )

    wf_d1_by_config = {w.workflow_config: w.workflow_d1 for w in workflows}
    protocolos: list[ParsedProtocolo] = []
    seen_raw: set[str] = set()
    seen_norm: set[str] = set()
    if not df_plano.empty:
        stats.source_rows = len(df_plano)
        c_prot = _col(df_plano, "Protocolo")
        c_wfc = _col(df_plano, "WorkflowConfig", "Workflow Config")
        c_wfp = _col(df_plano, "Workflow")
        c_da = _col(df_plano, "Data de Análise", "Data de Analise")
        c_hora = _col(df_plano, "Hora")
        c_canal = _col(df_plano, "Canal Destino")
        c_status = _col(df_plano, "Status BRFlow", "Status Brflow", "Status")
        for idx, row in df_plano.iterrows():
            line_no = int(idx) + 2
            protocolo = _as_str(row.get(c_prot)) if c_prot else ""
            if not protocolo:
                stats.rejected_rows += 1
                stats.add_error(line=line_no, column="Protocolo", code="EMPTY", message="protocolo vazio")
                continue
            wf_config = _as_str(row.get(c_wfc)) if c_wfc else ""
            if not wf_config and c_wfp:
                wf_config = _as_str(row.get(c_wfp))
            if not wf_config:
                stats.rejected_rows += 1
                stats.add_error(line=line_no, column="Workflow", code="MISSING", message="workflow ausente")
                continue
            hora = _as_optional_int(row.get(c_hora)) if c_hora else None
            if hora is not None and not (0 <= hora <= 23):
                stats.rejected_rows += 1
                stats.add_error(line=line_no, column="Hora", code="INVALID", message="hora fora de 0-23")
                continue
            norm = normalize_protocolo(protocolo)
            if protocolo in seen_raw:
                stats.duplicate_rows += 1
                continue
            if norm and norm in seen_norm:
                stats.duplicate_rows += 1
                continue
            seen_raw.add(protocolo)
            if norm:
                seen_norm.add(norm)
            item = ParsedProtocolo(
                protocolo=protocolo,
                workflow_config=wf_config,
                workflow_d1=wf_d1_by_config.get(wf_config, _as_str(row.get(c_wfp)) if c_wfp else ""),
                data_analise=parse_datetime_br(row.get(c_da)) if c_da else None,
                hora=hora,
                canal_destino=_as_str(row.get(c_canal)) if c_canal else "",
                status_brflow=(
                    _as_str(row.get(c_status))
                    if c_status and _as_str(row.get(c_status))
                    else status_by_wf.get(wf_config, "PENDENTE")
                ),
            )
            protocolos.append(item)
            stats.valid_rows += 1
            stats.records.append(item)

    stats.metadata = {
        "run_id": resolved_run,
        "workflows_count": len(workflows),
        "protocolos_count": len(protocolos),
    }

    return ParsedReport(
        run_id=resolved_run,
        data_referencia_d1=data_ref,
        data_execucao=data_exec,
        parquet_referencia=parquet_ref,
        auditores_ativos_brflow=aud_brflow,
        auditores_ativos_case=aud_case,
        workflows=workflows,
        protocolos=protocolos,
        read_stats=stats,
    )
