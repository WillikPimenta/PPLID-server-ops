"""Importação de escala a partir do Excel ESCALA_COMPLIANCE."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.utils import timezone

from apps.workforce.excel_utils import is_empty, normalize_column, normalize_name
from apps.workforce.models import Agent

from ..models import Escala, EscalaImportBatch, JobActivity, Location, Schedule
from .schedule_today import ScheduleTodayService

FIXED_COLUMNS = {
    "bloco",
    "colaborador",
    "matricula",
    "lideranca",
    "horario",
    "atividade",
    "uf",
    "equipe",
}

SKIP_SHEET_PREFIXES = ("planilha", "base", "tbl_", "divisao")

SUMMARY_COLUMNS = {
    "dias_do_mes",
    "escalado",
    "folga",
    "ferias",
    "afastado",
    "e_cad",
}

DATE_COL_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

TIME_RE = re.compile(r"^\d{2}:\d{2}\s*-\s*\d{2}:\d{2}$")

ABSENCE_CODES = {"FOLGA", "FERIAS", "AFASTADO", "BH"}

BULK_CHUNK_SIZE = 500


class ImportInProgressError(Exception):
    """Outra importação ainda está em andamento."""


@dataclass
class ImportResult:
    batch_id: str
    sheets_processed: list[str] = field(default_factory=list)
    rows_upserted: int = 0
    schedules_synced: int = 0
    dates_rebuilt: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def can_access_planning(user) -> bool:
    from apps.automacoes.permissions import can_access_planning as rbac_can_access_planning

    if not user.is_authenticated:
        return False
    from .permissions import open_access_enabled

    if open_access_enabled():
        return True
    return rbac_can_access_planning(user)


def finalize_stale_import_batches(*, minutes: int = 30) -> int:
    """Marca importações antigas em processing como failed (servidor reiniciado, etc.)."""
    cutoff = timezone.now() - timedelta(minutes=minutes)
    return EscalaImportBatch.objects.filter(
        status=EscalaImportBatch.STATUS_PROCESSING,
        created_at__lt=cutoff,
    ).update(
        status=EscalaImportBatch.STATUS_FAILED,
        failure_detail="Importação expirou ou foi interrompida. Tente novamente.",
        finished_at=timezone.now(),
    )


def import_in_progress() -> bool:
    finalize_stale_import_batches()
    return EscalaImportBatch.objects.filter(
        status=EscalaImportBatch.STATUS_PROCESSING,
    ).exists()


def normalize_absence(value: str) -> str:
    text = normalize_name(value).upper().replace(" ", "")
    if text in {"FERIAS", "FERIA"}:
        return "FERIAS"
    return text


def normalize_horario(value: str) -> str:
    if not value:
        return ""
    text = str(value).strip()
    match = TIME_RE.match(text)
    if match:
        return text
    parts = re.split(r"\s*-\s*", text)
    if len(parts) == 2:
        try:
            start = datetime.strptime(parts[0].strip(), "%H:%M").strftime("%H:%M")
            end = datetime.strptime(parts[1].strip(), "%H:%M").strftime("%H:%M")
            return f"{start} - {end}"
        except ValueError:
            pass
    return text


def parse_column_date(col) -> date | None:
    if isinstance(col, datetime):
        return col.date()
    if hasattr(col, "date") and callable(col.date):
        try:
            return col.date()
        except (TypeError, ValueError):
            pass
    text = str(col).strip()
    if DATE_COL_RE.match(text):
        parsed = pd.to_datetime(text, format="%d/%m/%Y", errors="coerce")
        if pd.notna(parsed):
            return parsed.date()
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.notna(parsed):
        return parsed.date()
    return None


def is_valid_schedule_sheet(df: pd.DataFrame) -> bool:
    has_matricula = any(normalize_column(c) == "matricula" for c in df.columns)
    if not has_matricula:
        return False
    return any(parse_column_date(c) is not None for c in df.columns)


def build_column_map(df: pd.DataFrame) -> tuple[dict, list[tuple[str, date]]]:
    rename: dict = {}
    date_columns: list[tuple[str, date]] = []
    for col in df.columns:
        norm = normalize_column(col)
        rename[col] = norm
        col_date = parse_column_date(col)
        if (
            col_date
            and norm not in FIXED_COLUMNS
            and norm not in SUMMARY_COLUMNS
            and not norm.startswith("unnamed")
        ):
            date_columns.append((norm, col_date))
    return rename, date_columns


def should_skip_sheet(name: str) -> bool:
    normalized = normalize_column(name)
    return any(normalized.startswith(prefix) for prefix in SKIP_SHEET_PREFIXES)


def is_header_row(row: pd.Series, date_col_keys: list[str]) -> bool:
    matricula = row.get("matricula")
    colaborador = row.get("colaborador")
    if is_empty(matricula) and is_empty(colaborador):
        return True
    if not is_empty(matricula) and is_empty(colaborador):
        try:
            float(str(matricula).replace(",", "."))
            return True
        except ValueError:
            pass
    for key in date_col_keys[:3]:
        val = row.get(key)
        if not is_empty(val):
            try:
                float(str(val))
                return True
            except ValueError:
                pass
    return False


def cell_to_dia_escala(value) -> str:
    if is_empty(value):
        return ""
    text = str(value).strip()
    if TIME_RE.match(text):
        return normalize_horario(text)
    upper = normalize_absence(text)
    if upper in ABSENCE_CODES:
        return upper
    if TIME_RE.match(normalize_horario(text)):
        return normalize_horario(text)
    return text.upper()


def _bulk_upsert_escala(records: list[Escala]) -> None:
    if not records:
        return
    Escala.objects.bulk_create(
        records,
        update_conflicts=True,
        unique_fields=["agent", "data"],
        update_fields=[
            "leader",
            "job_activity",
            "location",
            "bloco",
            "equipe",
            "horario",
            "dia_escala",
            "import_batch",
        ],
    )


def _bulk_upsert_schedule(records: list[Schedule]) -> None:
    if not records:
        return
    Schedule.objects.bulk_create(
        records,
        update_conflicts=True,
        unique_fields=["agent", "date"],
        update_fields=["work_schedule", "work_day"],
    )


def _dedupe_chunk_pairs(
    escala_objs: list[Escala],
    schedule_objs: list[Schedule],
) -> tuple[list[Escala], list[Schedule], int]:
    """Remove duplicatas (agent_id, data) no lote; mantém a última ocorrência."""
    merged: dict[tuple, tuple[Escala, Schedule]] = {}
    for escala_obj, schedule_obj in zip(escala_objs, schedule_objs):
        key = (escala_obj.agent_id, escala_obj.data)
        merged[key] = (escala_obj, schedule_obj)
    duplicates_removed = len(escala_objs) - len(merged)
    if not merged:
        return [], [], duplicates_removed
    pairs = list(merged.values())
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs], duplicates_removed


def _save_batch_progress(batch: EscalaImportBatch, result: ImportResult) -> None:
    batch.sheets_processed = result.sheets_processed
    batch.rows_upserted = result.rows_upserted
    batch.schedules_synced = result.schedules_synced
    batch.dates_rebuilt = result.dates_rebuilt
    batch.errors = result.errors
    batch.warnings = result.warnings
    batch.save(
        update_fields=[
            "sheets_processed",
            "rows_upserted",
            "schedules_synced",
            "dates_rebuilt",
            "errors",
            "warnings",
        ]
    )


class EscalaExcelImporter:
    def __init__(self, user=None):
        self.user = user
        self._agent_by_lan: dict[str, Agent] = {}
        self._agent_by_name: dict[str, Agent] = {}
        self._activity_cache: dict[str, JobActivity] = {}
        self._location_cache: dict[str, Location] = {}
        self._load_agents()
        self._load_dimensions()

    def _load_agents(self):
        for agent in Agent.objects.all():
            self._agent_by_lan[agent.user_lan_id.lower()] = agent
            key = normalize_name(agent.full_name)
            if key and key not in self._agent_by_name:
                self._agent_by_name[key] = agent

    def _load_dimensions(self):
        for act in JobActivity.objects.all():
            self._activity_cache[normalize_name(act.name)] = act
        for loc in Location.objects.all():
            for key in (normalize_name(loc.city_name), normalize_name(loc.display_name)):
                if key:
                    self._location_cache[key] = loc

    def resolve_agent(self, matricula: str) -> Agent | None:
        if is_empty(matricula):
            return None
        lan = str(matricula).strip().lower()
        return self._agent_by_lan.get(lan)

    def resolve_leader(self, name: str) -> tuple[Agent | None, str | None]:
        if is_empty(name) or str(name).strip() in {"0", "0.0"}:
            return None, None
        key = normalize_name(str(name))
        agent = self._agent_by_name.get(key)
        if agent:
            return agent, None
        return None, f"Liderança não encontrada: {name}"

    def resolve_activity(self, name: str) -> JobActivity | None:
        if is_empty(name):
            return None
        key = normalize_name(str(name))
        if key in self._activity_cache:
            return self._activity_cache[key]
        activity, _ = JobActivity.objects.get_or_create(
            name=str(name).strip(),
            defaults={"active": True},
        )
        self._activity_cache[key] = activity
        return activity

    def resolve_location(self, name: str) -> Location | None:
        if is_empty(name):
            return None
        key = normalize_name(str(name))
        if key in self._location_cache:
            return self._location_cache[key]
        city = str(name).strip()
        location, _ = Location.objects.get_or_create(
            city_name=city,
            defaults={"display_name": city, "active": True},
        )
        self._location_cache[key] = location
        return location

    def unpivot_sheet(self, df: pd.DataFrame, sheet_name: str) -> list[dict]:
        rename, date_columns = build_column_map(df)
        if not date_columns:
            return []

        df = df.rename(columns=rename)
        date_col_keys = [k for k, _ in date_columns]

        rows_out: list[dict] = []
        for idx, row in df.iterrows():
            if is_header_row(row, date_col_keys):
                continue
            matricula = row.get("matricula")
            colaborador = row.get("colaborador")
            if is_empty(matricula) or is_empty(colaborador):
                continue

            horario = normalize_horario(str(row.get("horario") or ""))
            bloco_raw = row.get("bloco")
            bloco = None
            if not is_empty(bloco_raw):
                try:
                    bloco = int(float(bloco_raw))
                except (TypeError, ValueError):
                    pass

            base = {
                "sheet_name": sheet_name,
                "excel_row": int(idx) + 2,
                "matricula": str(matricula).strip().lower(),
                "lideranca": row.get("lideranca"),
                "horario": horario,
                "atividade": row.get("atividade"),
                "uf": row.get("uf"),
                "equipe": str(row.get("equipe") or "").strip(),
                "bloco": bloco,
            }

            for col_key, col_date in date_columns:
                dia_val = cell_to_dia_escala(row.get(col_key))
                rows_out.append({**base, "data": col_date, "dia_escala": dia_val})

        return rows_out

    def map_to_schedule(self, horario: str, dia_escala: str) -> tuple[str, bool]:
        if dia_escala and TIME_RE.match(dia_escala):
            return dia_escala, True
        if dia_escala in ABSENCE_CODES:
            return "", False
        if dia_escala and not TIME_RE.match(dia_escala):
            upper = normalize_absence(dia_escala)
            if upper in ABSENCE_CODES:
                return "", False
        if horario:
            return horario, True
        return "", False

    def _flush_chunks(
        self,
        escala_objs: list[Escala],
        schedule_objs: list[Schedule],
        result: ImportResult,
        affected_dates: set[date],
        batch: EscalaImportBatch,
    ) -> None:
        escala_objs, schedule_objs, duplicates_removed = _dedupe_chunk_pairs(
            escala_objs,
            schedule_objs,
        )
        if duplicates_removed > 0:
            result.warnings.append(
                {
                    "message": (
                        f"{duplicates_removed} linha(s) duplicada(s) (matrícula + data) "
                        "no lote; mantida a última ocorrência."
                    ),
                }
            )
        if not escala_objs:
            return
        with transaction.atomic():
            _bulk_upsert_escala(escala_objs)
            _bulk_upsert_schedule(schedule_objs)
        result.rows_upserted += len(escala_objs)
        result.schedules_synced += len(schedule_objs)
        for schedule_obj in schedule_objs:
            affected_dates.add(schedule_obj.date)
        _save_batch_progress(batch, result)

    def _process_sheet_rows(
        self,
        unpivoted: list[dict],
        sheet_name: str,
        batch: EscalaImportBatch,
        result: ImportResult,
        affected_dates: set[date],
    ) -> None:
        escala_chunk: list[Escala] = []
        schedule_chunk: list[Schedule] = []

        for row in unpivoted:
            agent = self.resolve_agent(row["matricula"])
            if not agent:
                result.errors.append(
                    {
                        "sheet": sheet_name,
                        "row": row["excel_row"],
                        "matricula": row["matricula"],
                        "message": "Agente não encontrado",
                    }
                )
                continue

            leader, warn = self.resolve_leader(row["lideranca"])
            if warn:
                result.warnings.append(
                    {
                        "sheet": sheet_name,
                        "row": row["excel_row"],
                        "matricula": row["matricula"],
                        "message": warn,
                    }
                )

            activity = self.resolve_activity(row["atividade"])
            location = self.resolve_location(row["uf"])
            work_schedule, work_day = self.map_to_schedule(
                row["horario"],
                row["dia_escala"],
            )

            escala_chunk.append(
                Escala(
                    agent=agent,
                    data=row["data"],
                    leader=leader,
                    job_activity=activity,
                    location=location,
                    bloco=row["bloco"],
                    equipe=row["equipe"],
                    horario=row["horario"],
                    dia_escala=row["dia_escala"],
                    import_batch=batch,
                )
            )
            schedule_chunk.append(
                Schedule(
                    agent=agent,
                    date=row["data"],
                    work_schedule=work_schedule,
                    work_day=work_day,
                )
            )

            if len(escala_chunk) >= BULK_CHUNK_SIZE:
                self._flush_chunks(escala_chunk, schedule_chunk, result, affected_dates, batch)
                escala_chunk = []
                schedule_chunk = []

        if escala_chunk:
            self._flush_chunks(escala_chunk, schedule_chunk, result, affected_dates, batch)

    def import_file(
        self,
        file_obj,
        filename: str = "upload.xlsx",
        *,
        batch: EscalaImportBatch | None = None,
        rebuild_panel: bool = True,
        rebuild_async: bool = True,
        finalize_status: bool = True,
    ) -> ImportResult:
        content = file_obj.read() if hasattr(file_obj, "read") else Path(file_obj).read_bytes()
        xl = pd.ExcelFile(BytesIO(content))

        if batch is None:
            batch = EscalaImportBatch.objects.create(
                filename=filename,
                uploaded_by=self.user,
                status=EscalaImportBatch.STATUS_PROCESSING,
            )
        result = ImportResult(batch_id=str(batch.id))
        affected_dates: set[date] = set()

        for sheet_name in xl.sheet_names:
            if should_skip_sheet(sheet_name):
                continue
            df = pd.read_excel(BytesIO(content), sheet_name=sheet_name)
            if not is_valid_schedule_sheet(df):
                continue

            unpivoted = self.unpivot_sheet(df, sheet_name)
            if not unpivoted:
                continue

            result.sheets_processed.append(sheet_name)
            self._process_sheet_rows(unpivoted, sheet_name, batch, result, affected_dates)

        sorted_dates = sorted(affected_dates)
        if rebuild_panel and sorted_dates:
            if rebuild_async:
                _schedule_panel_rebuild_async(sorted_dates)
                result.dates_rebuilt = [d.isoformat() for d in sorted_dates]
            else:
                for target in sorted_dates:
                    ScheduleTodayService.build_for_date(target)
                    result.dates_rebuilt.append(target.isoformat())

        batch.sheets_processed = result.sheets_processed
        batch.rows_upserted = result.rows_upserted
        batch.schedules_synced = result.schedules_synced
        batch.dates_rebuilt = result.dates_rebuilt
        batch.errors = result.errors
        batch.warnings = result.warnings
        if finalize_status:
            if result.sheets_processed:
                batch.status = EscalaImportBatch.STATUS_COMPLETED
            else:
                batch.status = EscalaImportBatch.STATUS_FAILED
                batch.failure_detail = "Nenhuma aba mensal válida encontrada no arquivo."
            batch.finished_at = timezone.now()
        batch.save()

        return result


def _schedule_panel_rebuild_async(dates: list[date]) -> None:
    """Reconstrói o painel em background para não estourar timeout HTTP."""

    def worker():
        close_old_connections()
        try:
            for target in dates:
                ScheduleTodayService.build_for_date(target)
        finally:
            close_old_connections()

    threading.Thread(target=worker, daemon=True).start()


def _run_import_job(
    batch_id: str,
    content: bytes,
    filename: str,
    user_id: int | None,
) -> None:
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first() if user_id else None
    batch = EscalaImportBatch.objects.filter(pk=batch_id).first()
    if not batch:
        return

    try:
        importer = EscalaExcelImporter(user=user)
        result = importer.import_file(
            BytesIO(content),
            filename=filename,
            batch=batch,
            finalize_status=False,
        )
        if not result.sheets_processed:
            batch.status = EscalaImportBatch.STATUS_FAILED
            batch.failure_detail = "Nenhuma aba mensal válida encontrada no arquivo."
        else:
            batch.status = EscalaImportBatch.STATUS_COMPLETED
    except Exception as exc:
        batch.status = EscalaImportBatch.STATUS_FAILED
        batch.failure_detail = str(exc)[:500]
    finally:
        batch.finished_at = timezone.now()
        batch.save(
            update_fields=[
                "status",
                "failure_detail",
                "finished_at",
                "sheets_processed",
                "rows_upserted",
                "schedules_synced",
                "dates_rebuilt",
                "errors",
                "warnings",
            ]
        )


def _run_import_job_threadsafe(
    batch_id: str,
    content: bytes,
    filename: str,
    user_id: int | None,
) -> None:
    close_old_connections()
    try:
        _run_import_job(batch_id, content, filename, user_id)
    finally:
        close_old_connections()


def schedule_import_job(
    batch_id: str,
    content: bytes,
    filename: str,
    user_id: int | None,
) -> None:
    threading.Thread(
        target=_run_import_job_threadsafe,
        args=(batch_id, content, filename, user_id),
        daemon=True,
    ).start()


def import_escala_excel(
    file_obj,
    filename: str = "upload.xlsx",
    user=None,
    *,
    batch: EscalaImportBatch | None = None,
    rebuild_panel: bool = True,
    rebuild_async: bool = True,
    finalize_status: bool = True,
) -> ImportResult:
    importer = EscalaExcelImporter(user=user)
    return importer.import_file(
        file_obj,
        filename=filename,
        batch=batch,
        rebuild_panel=rebuild_panel,
        rebuild_async=rebuild_async,
        finalize_status=finalize_status,
    )
