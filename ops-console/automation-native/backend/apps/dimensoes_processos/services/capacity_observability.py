from __future__ import annotations

import json
import logging
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from time import perf_counter
from typing import Any, Callable, Iterator, TypeVar

from django.db import connections
from django.utils import timezone

from apps.dimensoes_processos.models import CapacityDailySnapshot
from apps.dimensoes_processos.services.capacity import CAPACITY_PERIOD_METRIC_VERSION
from apps.dimensoes_processos.services.capacity_fingerprint import (
    current_capacity_source_fingerprint,
)


log = logging.getLogger("capacity.observability")
T = TypeVar("T")


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _emit(event: dict[str, Any], *, level: int = logging.INFO) -> None:
    """Emite uma linha JSON sem exigir formatter ou dependencia adicional."""

    log.log(
        level,
        json.dumps(event, ensure_ascii=False, sort_keys=True, default=_json_default),
    )


def snapshot_state_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    snapshot = meta.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    state = snapshot.get("state")
    return str(state) if state is not None else None


@dataclass
class CapacityObservation:
    operation: str
    scenario: str
    days: int
    count_queries: bool = False
    database: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)
    query_count: int | None = None
    duration_ms: float | None = None
    snapshot_state: str | None = None
    status: str = "running"
    _started_at: float = field(default_factory=perf_counter, repr=False)

    def capture_result(self, payload: dict[str, Any] | None) -> None:
        self.snapshot_state = snapshot_state_from_payload(payload)

    def as_event(self) -> dict[str, Any]:
        event: dict[str, Any] = {
            "event": "capacity_operation",
            "operation": self.operation,
            "status": self.status,
            "scenario": self.scenario,
            "days": self.days,
            "duration_ms": self.duration_ms,
            "snapshot_state": self.snapshot_state or "not_applicable",
        }
        if self.query_count is not None:
            event["query_count"] = self.query_count
        event.update(self.extra)
        return event


class _QueryCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(
        self,
        execute: Callable[..., Any],
        sql: str,
        params: Any,
        many: bool,
        context: dict[str, Any],
    ) -> Any:
        self.count += 1
        return execute(sql, params, many, context)


@contextmanager
def observe_capacity_operation(
    operation: str,
    *,
    scenario: str = "planejamento",
    days: int = 1,
    count_queries: bool = False,
    database: str = "default",
    extra: dict[str, Any] | None = None,
) -> Iterator[CapacityObservation]:
    """Mede uma operacao Capacity e emite telemetria JSON ao final.

    A contagem SQL usa ``execute_wrapper`` e fica desabilitada por padrao para
    evitar overhead no caminho HTTP. Nenhum parametro SQL ou override manual e
    registrado.
    """

    observation = CapacityObservation(
        operation=operation,
        scenario=scenario,
        days=days,
        count_queries=count_queries,
        database=database,
        extra=dict(extra or {}),
    )
    counter = _QueryCounter() if count_queries else None

    try:
        with ExitStack() as stack:
            if counter is not None:
                stack.enter_context(connections[database].execute_wrapper(counter))
            yield observation
        observation.status = "ok"
    except Exception as exc:
        observation.status = "error"
        observation.extra.setdefault("error_type", type(exc).__name__)
        raise
    finally:
        observation.duration_ms = round(
            (perf_counter() - observation._started_at) * 1000,
            2,
        )
        if counter is not None:
            observation.query_count = counter.count
        _emit(
            observation.as_event(),
            level=logging.ERROR if observation.status == "error" else logging.INFO,
        )


def measure_capacity_call(
    function: Callable[..., T],
    *args: Any,
    operation: str,
    scenario: str = "planejamento",
    days: int = 1,
    count_queries: bool = False,
    database: str = "default",
    extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[T, dict[str, Any]]:
    """Executa uma funcao e devolve resultado mais o evento medido."""

    with observe_capacity_operation(
        operation,
        scenario=scenario,
        days=days,
        count_queries=count_queries,
        database=database,
        extra=extra,
    ) as observation:
        result = function(*args, **kwargs)
        if isinstance(result, dict):
            observation.capture_result(result)
    return result, observation.as_event()


def inspect_snapshot_health(
    date_from: date,
    date_to: date,
    *,
    refresh_before_minutes: int = 120,
    metric_version: str = CAPACITY_PERIOD_METRIC_VERSION,
    database: str = "default",
    scenario_id: str = "planejamento",
) -> dict[str, Any]:
    """Classifica cobertura, versao e vencimento dos snapshots de Planejamento."""

    if date_from > date_to:
        raise ValueError("date_from nao pode ser posterior a date_to")
    if refresh_before_minutes < 0:
        raise ValueError("refresh_before_minutes nao pode ser negativo")

    now = timezone.now()
    refresh_deadline = now + timedelta(minutes=refresh_before_minutes)
    db_connection = connections[database]
    with db_connection.cursor() as cursor:
        columns = {
            column.name
            for column in db_connection.introspection.get_table_description(
                cursor,
                CapacityDailySnapshot._meta.db_table,
            )
        }

    # O worktree pode estar entre a migration v1 e v2. Limpar o ordering e
    # selecionar apenas colunas existentes mantem o health check utilizavel
    # antes da migration, sem mascarar essa condicao no resultado.
    queryset = CapacityDailySnapshot.objects.using(database).filter(
        calculation_date__range=(date_from, date_to)
    ).order_by()
    value_fields = [
        "calculation_date",
        "payload",
        "generated_at",
        "valid_until",
        "generation_id",
    ]
    schema_version = "v1"
    if "scenario_id" in columns and "metric_version" in columns:
        schema_version = "v2"
        queryset = queryset.filter(scenario_id=scenario_id)
        value_fields.append("metric_version")
        value_fields.append("source_fingerprint")

    expected_source_fingerprint = (
        current_capacity_source_fingerprint(
            date_from,
            date_to,
            scenario_id=scenario_id,
        )
        if schema_version == "v2"
        else None
    )

    rows_by_date: dict[date, list[dict[str, Any]]] = {}
    for row in queryset.values(*value_fields):
        rows_by_date.setdefault(row["calculation_date"], []).append(row)

    missing_dates: list[str] = []
    incompatible_dates: list[str] = []
    expired_dates: list[str] = []
    expiring_dates: list[str] = []
    fresh_dates: list[str] = []
    generation_ids: set[str] = set()

    day_count = (date_to - date_from).days + 1
    for offset in range(day_count):
        on_date = date_from + timedelta(days=offset)
        candidates = rows_by_date.get(on_date, [])
        if not candidates:
            missing_dates.append(on_date.isoformat())
            continue

        compatible_candidates = []
        for row in candidates:
            payload = row["payload"] if isinstance(row["payload"], dict) else {}
            series = (
                payload.get("series")
                if isinstance(payload.get("series"), dict)
                else {}
            )
            row_metric_version = row.get("metric_version") or series.get(
                "metric_version"
            )
            fingerprint_matches = (
                expected_source_fingerprint is None
                or row.get("source_fingerprint") == expected_source_fingerprint
            )
            if row_metric_version == metric_version and fingerprint_matches:
                compatible_candidates.append(row)

        if not compatible_candidates:
            incompatible_dates.append(on_date.isoformat())
            continue

        row = max(compatible_candidates, key=lambda item: item["valid_until"])
        generation_ids.add(str(row["generation_id"]))
        if row["valid_until"] <= now:
            expired_dates.append(on_date.isoformat())
        elif row["valid_until"] <= refresh_deadline:
            expiring_dates.append(on_date.isoformat())
        else:
            fresh_dates.append(on_date.isoformat())

    if missing_dates:
        state = "missing"
    elif incompatible_dates:
        state = "incompatible"
    elif expired_dates:
        state = "expired"
    elif expiring_dates:
        state = "expiring"
    else:
        state = "fresh"

    return {
        "state": state,
        "refresh_required": state != "fresh",
        "scenario": "planejamento",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": day_count,
        "metric_version": metric_version,
        "snapshot_schema": schema_version,
        "checked_at": now.isoformat(),
        "refresh_before_minutes": refresh_before_minutes,
        "counts": {
            "fresh": len(fresh_dates),
            "expiring": len(expiring_dates),
            "expired": len(expired_dates),
            "incompatible": len(incompatible_dates),
            "missing": len(missing_dates),
        },
        "dates": {
            "expiring": expiring_dates,
            "expired": expired_dates,
            "incompatible": incompatible_dates,
            "missing": missing_dates,
        },
        "generation_ids": sorted(generation_ids),
    }
