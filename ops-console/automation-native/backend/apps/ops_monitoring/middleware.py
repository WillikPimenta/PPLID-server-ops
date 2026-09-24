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
_INFLIGHT: dict[str, dict] = {}
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_SEQ = 0
_MAX_INFLIGHT = 500
_FLUSH_THREAD_STARTED = False
_FLUSH_INTERVAL_SEC = 5.0
_MAX_BUFFER = 200
_MAX_TRAFFIC_BUCKETS = 500
_SLOW_REQUEST_MS = 2000
_BACKPRESSURE_BUFFER_SOFT = 150
_BACKPRESSURE_INFLIGHT = 40
_BACKPRESSURE_FLUSH_SLOW_MS = 2000
_BACKPRESSURE_COOLDOWN_SEC = 30.0
_LAST_FLUSH_DURATION_MS = 0.0
_LAST_FLUSH_FAILED = False
_BACKPRESSURE_UNTIL = 0.0
_BACKPRESSURE_DROPPED = 0
_BACKPRESSURE_REASON = ""
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


def _backpressure_reasons() -> list[str]:
    reasons: list[str] = []
    with _BUFFER_LOCK:
        buffer_len = len(_BUFFER)
    with _INFLIGHT_LOCK:
        inflight_len = len(_INFLIGHT)
    if buffer_len >= _BACKPRESSURE_BUFFER_SOFT:
        reasons.append("sample_buffer_high")
    if buffer_len >= _MAX_BUFFER:
        reasons.append("sample_buffer_full")
    if inflight_len >= _BACKPRESSURE_INFLIGHT:
        reasons.append("inflight_high")
    if _LAST_FLUSH_DURATION_MS >= _BACKPRESSURE_FLUSH_SLOW_MS:
        reasons.append("flush_slow")
    if _LAST_FLUSH_FAILED:
        reasons.append("flush_failed")
    return reasons


def refresh_metrics_backpressure() -> bool:
    """Atualiza o estado de backpressure e retorna se amostras devem ser bloqueadas."""
    global _BACKPRESSURE_UNTIL, _BACKPRESSURE_REASON
    reasons = _backpressure_reasons()
    now = time.time()
    if reasons:
        _BACKPRESSURE_UNTIL = now + _BACKPRESSURE_COOLDOWN_SEC
        _BACKPRESSURE_REASON = ",".join(reasons)
        return True
    if now < _BACKPRESSURE_UNTIL:
        if not _BACKPRESSURE_REASON:
            _BACKPRESSURE_REASON = "cooldown"
        return True
    _BACKPRESSURE_REASON = ""
    return False


def is_metrics_backpressure_active() -> bool:
    return refresh_metrics_backpressure()


def get_metrics_backpressure_status() -> dict:
    active = refresh_metrics_backpressure()
    with _BUFFER_LOCK:
        buffer_len = len(_BUFFER)
    with _INFLIGHT_LOCK:
        inflight_len = len(_INFLIGHT)
    return {
        "active": active,
        "reason": _BACKPRESSURE_REASON or None,
        "droppedSamples": _BACKPRESSURE_DROPPED,
        "sampleBuffer": buffer_len,
        "sampleBufferLimit": _MAX_BUFFER,
        "inflight": inflight_len,
        "inflightLimit": _BACKPRESSURE_INFLIGHT,
        "lastFlushMs": round(_LAST_FLUSH_DURATION_MS, 1),
        "lastFlushFailed": _LAST_FLUSH_FAILED,
        "cooldownRemainingSec": max(0, round(_BACKPRESSURE_UNTIL - time.time(), 1)),
    }


def _should_skip_traffic_under_backpressure() -> bool:
    """Sob pressão forte no flush/buffer, não alimenta nem os agregados."""
    if not refresh_metrics_backpressure():
        return False
    reason = _BACKPRESSURE_REASON or ""
    return any(
        token in reason
        for token in ("sample_buffer_full", "flush_failed", "flush_slow")
    )


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


def capture_error_reason(response, status_code: int) -> str | None:
    """Extract a short, redacted reason from an HTTP error response."""
    if status_code < 400:
        return None
    value = None
    try:
        content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
        if "json" in content_type:
            raw = getattr(response, "content", b"")[:4096]
            parsed = json.loads(raw.decode("utf-8", errors="replace")) if raw else None
            if isinstance(parsed, dict):
                for key in ("detail", "message", "error"):
                    if parsed.get(key):
                        value = parsed[key]
                        break
            elif isinstance(parsed, list) and parsed:
                value = parsed[0]
        elif status_code < 500:
            value = getattr(response, "reason_phrase", None)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        value = None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(str(value or "").split())
    if not text:
        text = {
            400: "Requisição inválida",
            401: "Não autenticado",
            403: "Acesso negado",
            404: "Recurso não encontrado",
            409: "Conflito de dados",
            422: "Dados inválidos",
        }.get(status_code, "Erro interno" if status_code >= 500 else "Erro HTTP")
    sensitive = ("password", "token", "secret", "authorization", "api_key", "apikey", "bearer", "credential")
    if any(token in text.lower() for token in sensitive):
        return "Motivo ocultado por segurança"
    return text[:500]


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
    error_reason: str | None = None,
) -> None:
    global _BACKPRESSURE_DROPPED
    if refresh_metrics_backpressure():
        _BACKPRESSURE_DROPPED += 1
        return
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
                "error_reason": error_reason,
            }
        )
        should_flush = len(_BUFFER) >= _MAX_BUFFER

    if should_flush:
        flush_metrics_buffer()


def _enqueue_traffic(
    *, method: str, route: str, status_code: int, duration_ms: int, user_id: int | None
) -> None:
    if _should_skip_traffic_under_backpressure():
        return
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
    global _LAST_FLUSH_DURATION_MS, _LAST_FLUSH_FAILED, _BACKPRESSURE_UNTIL
    global _BACKPRESSURE_DROPPED, _BACKPRESSURE_REASON
    with _BUFFER_LOCK:
        _BUFFER.clear()
        _TRAFFIC_BUFFER.clear()
        _TRAFFIC_USER_BUFFER.clear()
    with _INFLIGHT_LOCK:
        _INFLIGHT.clear()
    _LAST_FLUSH_DURATION_MS = 0.0
    _LAST_FLUSH_FAILED = False
    _BACKPRESSURE_UNTIL = 0.0
    _BACKPRESSURE_DROPPED = 0
    _BACKPRESSURE_REASON = ""


def flush_metrics_buffer() -> int:
    global _LAST_FLUSH_DURATION_MS, _LAST_FLUSH_FAILED
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
            error_reason=item.get("error_reason"),
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

    started = time.perf_counter()
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
        _LAST_FLUSH_FAILED = True
        _LAST_FLUSH_DURATION_MS = (time.perf_counter() - started) * 1000
        _restore_buffers(batch, traffic, traffic_users)
        raise
    _LAST_FLUSH_FAILED = False
    _LAST_FLUSH_DURATION_MS = (time.perf_counter() - started) * 1000
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


def _next_inflight_id() -> str:
    global _INFLIGHT_SEQ
    with _INFLIGHT_LOCK:
        _INFLIGHT_SEQ += 1
        seq = _INFLIGHT_SEQ
    return f"{time.time_ns()}-{seq}-{threading.get_ident()}"


def track_inflight_start(request) -> None:
    """Registra uma requisição em andamento (memória do processo)."""
    inflight_id = _next_inflight_id()
    request._ops_inflight_id = inflight_id
    user = getattr(request, "user", None)
    entry = {
        "id": inflight_id,
        "startedAt": _utc_now(),
        "startedPerf": time.perf_counter(),
        "method": (request.method or "GET").upper(),
        "route": normalize_route(request.path),
        "user_id": _metric_user_id(user),
    }
    with _INFLIGHT_LOCK:
        if len(_INFLIGHT) >= _MAX_INFLIGHT:
            oldest = sorted(_INFLIGHT.items(), key=lambda item: item[1]["startedPerf"])[
                : max(1, len(_INFLIGHT) - _MAX_INFLIGHT + 1)
            ]
            for key, _ in oldest:
                _INFLIGHT.pop(key, None)
        _INFLIGHT[inflight_id] = entry


def track_inflight_end(request) -> None:
    inflight_id = getattr(request, "_ops_inflight_id", None)
    if not inflight_id:
        return
    with _INFLIGHT_LOCK:
        _INFLIGHT.pop(inflight_id, None)


def list_inflight_requests() -> list[dict]:
    """Snapshot das requisições ainda em execução neste processo."""
    now_perf = time.perf_counter()
    with _INFLIGHT_LOCK:
        rows = list(_INFLIGHT.values())
    payload = []
    for row in rows:
        started_perf = float(row.get("startedPerf") or now_perf)
        elapsed_ms = max(0, int((now_perf - started_perf) * 1000))
        started_at = row.get("startedAt")
        payload.append(
            {
                "id": row.get("id"),
                "startedAt": started_at.isoformat() if hasattr(started_at, "isoformat") else str(started_at or ""),
                "elapsedMs": elapsed_ms,
                "method": row.get("method") or "GET",
                "route": row.get("route") or "",
                "user_id": row.get("user_id"),
            }
        )
    payload.sort(key=lambda item: (-int(item.get("elapsedMs") or 0), str(item.get("startedAt") or "")))
    return payload


class RequestMetricsMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if should_skip_path(request.path):
            request._ops_metrics_skip = True
            return None
        request._ops_metrics_skip = False
        request._ops_metrics_start = time.perf_counter()
        try:
            track_inflight_start(request)
        except Exception:
            logger.exception("Falha ao registrar requisicao em andamento")
        return None

    def process_exception(self, request, exception):
        if not getattr(request, "_ops_metrics_skip", True):
            try:
                track_inflight_end(request)
            except Exception:
                pass
        return None

    def process_response(self, request, response):
        if getattr(request, "_ops_metrics_skip", True):
            return response
        try:
            track_inflight_end(request)
        except Exception:
            pass
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
        error_reason = capture_error_reason(response, status_code)

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
                    error_reason=error_reason,
                )
            except Exception:
                pass
        return response
