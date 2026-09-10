"""Middleware que registra latencia e status das requisicoes."""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone

from django.utils.deprecation import MiddlewareMixin

from .route_normalizer import normalize_route, should_skip_path

logger = logging.getLogger(__name__)

_BUFFER: list[dict] = []
_TRAFFIC_BUFFER: dict[tuple[datetime, str, str], dict[str, int]] = {}
_TRAFFIC_USER_BUFFER: set[tuple[datetime, int]] = set()
_BUFFER_LOCK = threading.Lock()
_FLUSH_THREAD_STARTED = False
_FLUSH_INTERVAL_SEC = 5.0
_MAX_BUFFER = 200
_MAX_TRAFFIC_BUCKETS = 500
_SLOW_REQUEST_MS = 2000
_MAX_REQUEST_PARAMS_BYTES = 4096
_SENSITIVE_PARAM_SUBSTRINGS = (
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "bearer",
    "credential",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_sensitive_param_key(key: str) -> bool:
    lowered = str(key or "").lower().replace("-", "_")
    return any(part in lowered for part in _SENSITIVE_PARAM_SUBSTRINGS)


def _redact_param_value(value):
    if isinstance(value, dict):
        return {
            key: ("***" if _is_sensitive_param_key(key) else _redact_param_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_param_value(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


def capture_request_params(request) -> dict | None:
    """Captura query string e corpo (form/json) com redacao de campos sensiveis."""
    params: dict[str, object] = {}

    query = request.GET.lists()
    if query:
        params["query"] = {
            key: values[0] if len(values) == 1 else values for key, values in query
        }

    method = (request.method or "GET").upper()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        content_type = (request.content_type or "").lower()
        if "application/json" in content_type:
            raw = getattr(request, "body", b"")[:_MAX_REQUEST_PARAMS_BYTES]
            if raw:
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                    if isinstance(parsed, dict):
                        params["body"] = _redact_param_value(parsed)
                    elif parsed is not None:
                        params["body"] = parsed
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    params["body"] = {"_raw": raw.decode("utf-8", errors="replace")[:500]}
        elif request.POST:
            params["body"] = _redact_param_value(request.POST.dict())

    if not params:
        return None
    encoded = json.dumps(params, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > _MAX_REQUEST_PARAMS_BYTES:
        return {"query": params.get("query"), "body": {"_truncated": True}}
    return params


def _metric_user_id(user) -> int | None:
    """Converte PKs inteiras ou UUIDs em uma chave anônima compatível com IntegerField."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    raw = str(getattr(user, "pk", None) or getattr(user, "id", "")).strip()
    if not raw:
        return None
    try:
        numeric = int(raw)
        if 0 < numeric <= 2_147_483_647:
            return numeric
    except (TypeError, ValueError):
        pass
    digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=4).digest()
    return (int.from_bytes(digest, "big") & 0x7FFF_FFFF) or 1


def _enqueue_metric(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_ms: int,
    user_id: int | None,
    request_params: dict | None = None,
) -> None:
    with _BUFFER_LOCK:
        _BUFFER.append(
            {
                "recorded_at": _utc_now(),
                "method": method[:10],
                "route": route,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "user_id": user_id,
                "request_params": request_params,
            }
        )
        should_flush = len(_BUFFER) >= _MAX_BUFFER

    if should_flush:
        flush_metrics_buffer()


def _enqueue_traffic(
    *, method: str, route: str, status_code: int, duration_ms: int, user_id: int | None
) -> None:
    bucket_start = _utc_now().replace(second=0, microsecond=0)
    key = (bucket_start, method[:10], route)
    with _BUFFER_LOCK:
        bucket = _TRAFFIC_BUFFER.setdefault(
            key,
            {
                "request_count": 0,
                "total_duration_ms": 0,
                "max_duration_ms": 0,
                "status_2xx": 0,
                "status_3xx": 0,
                "status_4xx": 0,
                "status_5xx": 0,
            },
        )
        bucket["request_count"] += 1
        bucket["total_duration_ms"] += duration_ms
        bucket["max_duration_ms"] = max(bucket["max_duration_ms"], duration_ms)
        status_key = f"status_{min(max(status_code // 100, 2), 5)}xx"
        bucket[status_key] += 1
        if user_id is not None:
            _TRAFFIC_USER_BUFFER.add((bucket_start, user_id))
        should_flush = len(_TRAFFIC_BUFFER) >= _MAX_TRAFFIC_BUCKETS

    if should_flush:
        flush_metrics_buffer()


def _restore_buffers(samples, traffic, users) -> None:
    with _BUFFER_LOCK:
        _BUFFER.extend(samples)
        for key, values in traffic.items():
            current = _TRAFFIC_BUFFER.setdefault(key, {name: 0 for name in values})
            for name, value in values.items():
                if name == "max_duration_ms":
                    current[name] = max(current[name], value)
                else:
                    current[name] += value
        _TRAFFIC_USER_BUFFER.update(users)


def clear_metrics_buffers_for_tests() -> None:
    """Descarta telemetria pendente entre transações isoladas de teste.

    Os buffers vivem no processo, enquanto ``TestCase`` reverte somente o
    banco. Sem esta fronteira, uma requisição revertida pode ser persistida no
    teste seguinte por um flush tardio.
    """
    with _BUFFER_LOCK:
        _BUFFER.clear()
        _TRAFFIC_BUFFER.clear()
        _TRAFFIC_USER_BUFFER.clear()


def flush_metrics_buffer() -> int:
    from django.db import connection, transaction

    from .models import ApiRequestMetric, ApiTrafficUserBucket

    with _BUFFER_LOCK:
        if not _BUFFER and not _TRAFFIC_BUFFER and not _TRAFFIC_USER_BUFFER:
            return 0
        batch = list(_BUFFER)
        traffic = dict(_TRAFFIC_BUFFER)
        traffic_users = set(_TRAFFIC_USER_BUFFER)
        _BUFFER.clear()
        _TRAFFIC_BUFFER.clear()
        _TRAFFIC_USER_BUFFER.clear()

    objects = [
        ApiRequestMetric(
            recorded_at=item["recorded_at"],
            method=item["method"],
            route=item["route"],
            status_code=item["status_code"],
            duration_ms=item["duration_ms"],
            user_id=item["user_id"],
            request_params=item.get("request_params"),
        )
        for item in batch
    ]
    traffic_sql = """
        INSERT INTO ops_api_traffic_bucket
            (bucket_start, method, route, request_count, total_duration_ms,
             max_duration_ms, status_2xx, status_3xx, status_4xx, status_5xx)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bucket_start, method, route) DO UPDATE SET
            request_count = ops_api_traffic_bucket.request_count + EXCLUDED.request_count,
            total_duration_ms = ops_api_traffic_bucket.total_duration_ms + EXCLUDED.total_duration_ms,
            max_duration_ms = GREATEST(ops_api_traffic_bucket.max_duration_ms, EXCLUDED.max_duration_ms),
            status_2xx = ops_api_traffic_bucket.status_2xx + EXCLUDED.status_2xx,
            status_3xx = ops_api_traffic_bucket.status_3xx + EXCLUDED.status_3xx,
            status_4xx = ops_api_traffic_bucket.status_4xx + EXCLUDED.status_4xx,
            status_5xx = ops_api_traffic_bucket.status_5xx + EXCLUDED.status_5xx
    """
    traffic_rows = [
        (
            bucket_start,
            method,
            route,
            values["request_count"],
            values["total_duration_ms"],
            values["max_duration_ms"],
            values["status_2xx"],
            values["status_3xx"],
            values["status_4xx"],
            values["status_5xx"],
        )
        for (bucket_start, method, route), values in traffic.items()
    ]
    user_objects = [
        ApiTrafficUserBucket(bucket_start=bucket_start, user_id=user_id)
        for bucket_start, user_id in traffic_users
    ]

    try:
        with transaction.atomic():
            if objects:
                ApiRequestMetric.objects.bulk_create(objects, batch_size=500)
            if traffic_rows:
                with connection.cursor() as cursor:
                    cursor.executemany(traffic_sql, traffic_rows)
            if user_objects:
                ApiTrafficUserBucket.objects.bulk_create(
                    user_objects, batch_size=500, ignore_conflicts=True
                )
    except Exception:
        _restore_buffers(batch, traffic, traffic_users)
        raise
    return len(objects) + len(traffic_rows) + len(user_objects)


def _flush_loop() -> None:
    while True:
        time.sleep(_FLUSH_INTERVAL_SEC)
        try:
            flush_metrics_buffer()
        except Exception:
            pass


def start_metrics_flush_thread() -> None:
    global _FLUSH_THREAD_STARTED
    if _FLUSH_THREAD_STARTED:
        return
    _FLUSH_THREAD_STARTED = True
    thread = threading.Thread(target=_flush_loop, name="ops-metrics-flush", daemon=True)
    thread.start()
    atexit.register(flush_metrics_buffer)


class RequestMetricsMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if should_skip_path(request.path):
            request._ops_metrics_skip = True
            return None
        request._ops_metrics_skip = False
        request._ops_metrics_start = time.perf_counter()
        return None

    def process_response(self, request, response):
        if getattr(request, "_ops_metrics_skip", True):
            return response
        start = getattr(request, "_ops_metrics_start", None)
        if start is None:
            return response

        duration_ms = max(0, int((time.perf_counter() - start) * 1000))
        status_code = int(getattr(response, "status_code", 500) or 500)
        route = normalize_route(request.path)
        method = (request.method or "GET").upper()
        user = getattr(request, "user", None)
        user_id = _metric_user_id(user)
        request_params = capture_request_params(request)

        try:
            _enqueue_traffic(
                method=method,
                route=route,
                status_code=status_code,
                duration_ms=duration_ms,
                user_id=user_id,
            )
        except Exception:
            logger.exception("Falha ao persistir metricas de API")

        # Sempre grava erros, lentas; amostra 10% das demais
        if status_code >= 500 or duration_ms >= _SLOW_REQUEST_MS:
            store = True
        elif status_code >= 400:
            store = True
        else:
            store = (int(time.time() * 1000) % 10) == 0

        if store:
            try:
                _enqueue_metric(
                    method=method,
                    route=route,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    user_id=user_id,
                    request_params=request_params,
                )
            except Exception:
                pass
        return response
