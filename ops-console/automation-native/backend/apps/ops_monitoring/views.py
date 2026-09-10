"""Endpoints internos de metricas (somente localhost)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db import DatabaseError
from django.db.models import Avg, Count, Max, Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from config.api_exceptions import PUBLIC_INTERNAL_ERROR, report_internal_error

from apps.falhas_criticas.services.user_display import resolve_user_display_name

from .models import ApiRequestMetric, ApiTrafficBucket, ApiTrafficUserBucket

logger = logging.getLogger(__name__)

_WINDOW_MAP = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


def _is_local_request(request) -> bool:
    ip = (request.META.get("REMOTE_ADDR") or "").strip()
    return ip in ("127.0.0.1", "::1", "localhost")


def _window_delta(window: str) -> timedelta:
    return _WINDOW_MAP.get(window, _WINDOW_MAP["24h"])


_SAMPLING_POLICY = {
    "mode": "priority_sample",
    "normalRatePct": 10,
    "capturesAll4xx": True,
    "capturesAll5xx": True,
    "capturesAllSlow": True,
    "slowRequestMs": 2000,
}


def _build_requester_map() -> dict[int, str]:
    from django.contrib.auth import get_user_model

    from .middleware import _metric_user_id

    requester_map: dict[int, str] = {}
    User = get_user_model()
    for user in User.objects.filter(is_active=True).iterator():
        metric_id = _metric_user_id(user)
        if metric_id is None:
            continue
        display = resolve_user_display_name(user)
        if display:
            requester_map[metric_id] = display
    return requester_map


def _resolve_requester(user_id: int | None, requester_map: dict[int, str]) -> str:
    if user_id is None:
        return "Anônimo"
    return requester_map.get(int(user_id), "Usuário desconhecido")


def _build_sampled_metrics(qs, *, route_limit: int = 50) -> tuple[dict, list[dict]]:
    """Resume a telemetria amostral sem misturar sucesso com respostas de erro."""
    success_filter = Q(status_code__gte=200, status_code__lt=400)
    client_error_filter = Q(status_code__gte=400, status_code__lt=500)
    server_error_filter = Q(status_code__gte=500)

    totals = qs.aggregate(
        sample_count=Count("id"),
        avg_ms=Avg("duration_ms"),
        max_ms=Max("duration_ms"),
        status_2xx=Count(
            "id", filter=Q(status_code__gte=200, status_code__lt=300)
        ),
        status_3xx=Count(
            "id", filter=Q(status_code__gte=300, status_code__lt=400)
        ),
        status_4xx=Count("id", filter=client_error_filter),
        status_5xx=Count("id", filter=server_error_filter),
        success_avg_ms=Avg("duration_ms", filter=success_filter),
        client_error_avg_ms=Avg("duration_ms", filter=client_error_filter),
        server_error_avg_ms=Avg("duration_ms", filter=server_error_filter),
    )
    status_2xx = int(totals["status_2xx"] or 0)
    status_3xx = int(totals["status_3xx"] or 0)
    status_4xx = int(totals["status_4xx"] or 0)
    status_5xx = int(totals["status_5xx"] or 0)
    sample_count = int(totals["sample_count"] or 0)
    totals_payload = {
        # requests e os aliases errors* permanecem por compatibilidade com o console antigo.
        "requests": sample_count,
        "sampleCount": sample_count,
        "avgMs": round(float(totals["avg_ms"] or 0), 1),
        "maxMs": int(totals["max_ms"] or 0),
        "status2xx": status_2xx,
        "status3xx": status_3xx,
        "status4xx": status_4xx,
        "status5xx": status_5xx,
        "successSamples": status_2xx + status_3xx,
        "errors4xx": status_4xx,
        "errors5xx": status_5xx,
        "successAvgMs": (
            round(float(totals["success_avg_ms"]), 1)
            if totals["success_avg_ms"] is not None
            else None
        ),
        "clientErrorAvgMs": (
            round(float(totals["client_error_avg_ms"]), 1)
            if totals["client_error_avg_ms"] is not None
            else None
        ),
        "serverErrorAvgMs": (
            round(float(totals["server_error_avg_ms"]), 1)
            if totals["server_error_avg_ms"] is not None
            else None
        ),
    }

    route_rows = (
        qs.values("method", "route")
        .annotate(
            sample_count=Count("id"),
            avg_ms=Avg("duration_ms"),
            max_ms=Max("duration_ms"),
            status_2xx=Count(
                "id", filter=Q(status_code__gte=200, status_code__lt=300)
            ),
            status_3xx=Count(
                "id", filter=Q(status_code__gte=300, status_code__lt=400)
            ),
            status_4xx=Count("id", filter=client_error_filter),
            status_5xx=Count("id", filter=server_error_filter),
            success_avg_ms=Avg("duration_ms", filter=success_filter),
            client_error_avg_ms=Avg("duration_ms", filter=client_error_filter),
            server_error_avg_ms=Avg("duration_ms", filter=server_error_filter),
        )
        .order_by("-sample_count", "method", "route")[:route_limit]
    )
    route_stats = []
    for row in route_rows:
        status_2xx = int(row["status_2xx"] or 0)
        status_3xx = int(row["status_3xx"] or 0)
        status_4xx = int(row["status_4xx"] or 0)
        status_5xx = int(row["status_5xx"] or 0)
        route_stats.append(
            {
                "method": row["method"],
                "route": row["route"],
                "count": int(row["sample_count"] or 0),
                "sampleCount": int(row["sample_count"] or 0),
                "avgMs": round(float(row["avg_ms"] or 0), 1),
                "maxMs": int(row["max_ms"] or 0),
                "status2xx": status_2xx,
                "status3xx": status_3xx,
                "status4xx": status_4xx,
                "status5xx": status_5xx,
                "successSamples": status_2xx + status_3xx,
                "errors4xx": status_4xx,
                "errors5xx": status_5xx,
                "successAvgMs": (
                    round(float(row["success_avg_ms"]), 1)
                    if row["success_avg_ms"] is not None
                    else None
                ),
                "clientErrorAvgMs": (
                    round(float(row["client_error_avg_ms"]), 1)
                    if row["client_error_avg_ms"] is not None
                    else None
                ),
                "serverErrorAvgMs": (
                    round(float(row["server_error_avg_ms"]), 1)
                    if row["server_error_avg_ms"] is not None
                    else None
                ),
            }
        )
    return totals_payload, route_stats


_TRAFFIC_RESOLUTION_MINUTES = {
    "1h": 1,
    "6h": 5,
    "24h": 15,
    "7d": 60,
}
_ACTIVE_USERS_WINDOW_MINUTES = 5


def _floor_bucket(value: datetime, minutes: int) -> datetime:
    if minutes >= 60:
        return value.replace(minute=0, second=0, microsecond=0)
    minute = (value.minute // minutes) * minutes
    return value.replace(minute=minute, second=0, microsecond=0)


def _build_api_traffic(window: str, since: datetime) -> dict:
    now = timezone.now()
    resolution = _TRAFFIC_RESOLUTION_MINUTES.get(window, 15)
    traffic_qs = ApiTrafficBucket.objects.filter(bucket_start__gte=since)

    totals = traffic_qs.aggregate(
        requests=Sum("request_count"),
        total_duration_ms=Sum("total_duration_ms"),
        max_ms=Max("max_duration_ms"),
        status_2xx=Sum("status_2xx"),
        status_3xx=Sum("status_3xx"),
        status_4xx=Sum("status_4xx"),
        status_5xx=Sum("status_5xx"),
    )
    requests = int(totals["requests"] or 0)
    status_2xx = int(totals["status_2xx"] or 0)
    status_3xx = int(totals["status_3xx"] or 0)
    status_4xx = int(totals["status_4xx"] or 0)
    status_5xx = int(totals["status_5xx"] or 0)

    minute_rows = (
        traffic_qs.values("bucket_start")
        .annotate(
            requests=Sum("request_count"),
            total_duration_ms=Sum("total_duration_ms"),
            max_ms=Max("max_duration_ms"),
            status_2xx=Sum("status_2xx"),
            status_3xx=Sum("status_3xx"),
            status_4xx=Sum("status_4xx"),
            status_5xx=Sum("status_5xx"),
        )
        .order_by("bucket_start")
    )
    combined: dict[datetime, dict[str, int]] = {}
    for row in minute_rows:
        slot = _floor_bucket(row["bucket_start"], resolution)
        target = combined.setdefault(
            slot,
            {
                "requests": 0,
                "total_duration_ms": 0,
                "max_ms": 0,
                "status_2xx": 0,
                "status_3xx": 0,
                "status_4xx": 0,
                "status_5xx": 0,
            },
        )
        target["requests"] += int(row["requests"] or 0)
        target["total_duration_ms"] += int(row["total_duration_ms"] or 0)
        target["max_ms"] = max(target["max_ms"], int(row["max_ms"] or 0))
        for key in ("status_2xx", "status_3xx", "status_4xx", "status_5xx"):
            target[key] += int(row[key] or 0)

    users_by_slot: dict[datetime, set[int]] = {}
    user_rows = ApiTrafficUserBucket.objects.filter(bucket_start__gte=since).values(
        "bucket_start", "user_id"
    )
    for row in user_rows.iterator():
        slot = _floor_bucket(row["bucket_start"], resolution)
        users_by_slot.setdefault(slot, set()).add(int(row["user_id"]))

    points = []
    cursor = _floor_bucket(since, resolution)
    end = _floor_bucket(now, resolution)
    step = timedelta(minutes=resolution)
    while cursor <= end:
        row = combined.get(cursor) or {
            "requests": 0,
            "total_duration_ms": 0,
            "max_ms": 0,
            "status_2xx": 0,
            "status_3xx": 0,
            "status_4xx": 0,
            "status_5xx": 0,
        }
        count = row["requests"]
        points.append(
            {
                "at": cursor.isoformat(),
                "requests": count,
                "rpm": round(count / resolution, 2),
                "uniqueUsers": len(users_by_slot.get(cursor, set())),
                "avgMs": round(row["total_duration_ms"] / count, 1) if count else 0,
                "maxMs": row["max_ms"],
                "status2xx": row["status_2xx"],
                "status3xx": row["status_3xx"],
                "status4xx": row["status_4xx"],
                "status5xx": row["status_5xx"],
            }
        )
        cursor += step

    route_rows = (
        traffic_qs.values("method", "route")
        .annotate(
            requests=Sum("request_count"),
            total_duration_ms=Sum("total_duration_ms"),
            max_ms=Max("max_duration_ms"),
            status_4xx=Sum("status_4xx"),
            status_5xx=Sum("status_5xx"),
        )
        .order_by("-requests")[:10]
    )
    top_routes = []
    for row in route_rows:
        route_requests = int(row["requests"] or 0)
        top_routes.append(
            {
                "method": row["method"],
                "route": row["route"],
                "requests": route_requests,
                "avgMs": (
                    round(int(row["total_duration_ms"] or 0) / route_requests, 1)
                    if route_requests
                    else 0
                ),
                "maxMs": int(row["max_ms"] or 0),
                "errors4xx": int(row["status_4xx"] or 0),
                "errors5xx": int(row["status_5xx"] or 0),
            }
        )

    unique_users = (
        ApiTrafficUserBucket.objects.filter(bucket_start__gte=since)
        .values("user_id")
        .distinct()
        .count()
    )
    active_users_since = _floor_bucket(
        now - timedelta(minutes=_ACTIVE_USERS_WINDOW_MINUTES),
        1,
    )
    active_users_now = (
        ApiTrafficUserBucket.objects.filter(bucket_start__gte=active_users_since)
        .values("user_id")
        .distinct()
        .count()
    )
    error_count = status_4xx + status_5xx
    return {
        "available": True,
        "source": "exact_aggregate",
        "resolutionMinutes": resolution,
        "since": since.isoformat(),
        "until": now.isoformat(),
        "activeUsers": {
            "count": active_users_now,
            "windowMinutes": _ACTIVE_USERS_WINDOW_MINUTES,
            "since": active_users_since.isoformat(),
            "until": now.isoformat(),
        },
        "points": points,
        "topRoutes": top_routes,
        "totals": {
            "requests": requests,
            "avgMs": (
                round(int(totals["total_duration_ms"] or 0) / requests, 1)
                if requests
                else 0
            ),
            "maxMs": int(totals["max_ms"] or 0),
            "peakRpm": max((point["rpm"] for point in points), default=0),
            "uniqueUsers": unique_users,
            "activeUsersNow": active_users_now,
            "status2xx": status_2xx,
            "status3xx": status_3xx,
            "status4xx": status_4xx,
            "status5xx": status_5xx,
            "errorRatePct": round((error_count / requests) * 100, 2) if requests else 0,
        },
    }



@api_view(["GET"])
@permission_classes([AllowAny])
def ops_metrics_summary(request):
    if not _is_local_request(request):
        return JsonResponse({"error": "Acesso restrito a localhost"}, status=403)

    window = (request.GET.get("window") or "24h").lower()
    since = timezone.now() - _window_delta(window)
    qs = ApiRequestMetric.objects.filter(recorded_at__gte=since)

    # A consulta interna também força o flush para manter o painel atualizado
    # mesmo se o thread periódico ainda não tiver executado neste processo.
    try:
        from .middleware import flush_metrics_buffer

        flush_metrics_buffer()
    except Exception:
        logger.exception("Falha ao sincronizar metricas pendentes antes do resumo")

    try:
        traffic = _build_api_traffic(window, since)
    except DatabaseError as exc:
        report_internal_error(exc, operation="ops_monitoring.api_traffic")
        traffic = {
            "available": False,
            "source": "unavailable",
            "error": PUBLIC_INTERNAL_ERROR,
            "points": [],
            "topRoutes": [],
            "totals": {"requests": 0},
        }

    totals_payload, route_stats = _build_sampled_metrics(qs)

    error_routes = (
        qs.filter(status_code__gte=500)
        .values("method", "route")
        .annotate(count=Count("id"))
        .order_by("-count")[:15]
    )

    return JsonResponse(
        {
            "window": window,
            "since": since.isoformat(),
            "sampling": dict(_SAMPLING_POLICY),
            "totals": totals_payload,
            "routeStats": route_stats,
            "slowRoutes": route_stats[:20],
            "errorRoutes": [
                {
                    "method": row["method"],
                    "route": row["route"],
                    "count": row["count"],
                }
                for row in error_routes
            ],
            "traffic": traffic,
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def ops_metrics_around(request):
    """Amostras individuais de API em torno de um instante (drill-down do gráfico)."""
    if not _is_local_request(request):
        return JsonResponse({"error": "Acesso restrito a localhost"}, status=403)

    at_raw = (request.GET.get("at") or "").strip()
    if not at_raw:
        return JsonResponse({"error": "Parametro at (ISO) obrigatorio"}, status=400)

    try:
        radius = max(1, min(60, int(request.GET.get("radiusMinutes") or 5)))
    except (TypeError, ValueError):
        radius = 5
    try:
        min_ms = max(0, int(request.GET.get("minMs") or 0))
    except (TypeError, ValueError):
        min_ms = 0

    try:
        center = parse_datetime(at_raw.replace("Z", "+00:00"))
        if center is None:
            center = datetime.fromisoformat(at_raw.replace("Z", "+00:00"))
        if timezone.is_naive(center):
            center = timezone.make_aware(center, timezone.utc)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Parametro at invalido"}, status=400)

    since = center - timedelta(minutes=radius)
    until = center + timedelta(minutes=radius)
    qs = ApiRequestMetric.objects.filter(recorded_at__gte=since, recorded_at__lte=until)
    if min_ms > 0:
        qs = qs.filter(duration_ms__gte=min_ms)

    totals_payload, route_stats = _build_sampled_metrics(qs, route_limit=20)

    samples = list(
        qs.order_by("-duration_ms", "-recorded_at").values(
            "recorded_at", "method", "route", "status_code", "duration_ms"
        )[:50]
    )

    return JsonResponse(
        {
            "at": center.isoformat(),
            "radiusMinutes": radius,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "minMs": min_ms,
            "sampling": dict(_SAMPLING_POLICY),
            "totals": totals_payload,
            "routeStats": route_stats,
            "slowRoutes": route_stats,
            "samples": [
                {
                    "recordedAt": row["recorded_at"].isoformat(),
                    "method": row["method"],
                    "route": row["route"],
                    "statusCode": row["status_code"],
                    "durationMs": row["duration_ms"],
                }
                for row in samples
            ],
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def ops_metrics_route_samples(request):
    """Amostras individuais de uma rota dentro da janela selecionada."""
    if not _is_local_request(request):
        return JsonResponse({"error": "Acesso restrito a localhost"}, status=403)

    window = (request.GET.get("window") or "24h").lower()
    method = (request.GET.get("method") or "").strip().upper()
    route = (request.GET.get("route") or "").strip()
    if not method or not route:
        return JsonResponse({"error": "Parametros method e route obrigatorios"}, status=400)

    try:
        limit = max(1, min(100, int(request.GET.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50

    since = timezone.now() - _window_delta(window)

    try:
        from .middleware import flush_metrics_buffer

        flush_metrics_buffer()
    except Exception:
        logger.exception("Falha ao sincronizar metricas pendentes antes das amostras")

    qs = ApiRequestMetric.objects.filter(
        recorded_at__gte=since,
        method=method,
        route=route,
    )
    sample_count = qs.count()
    requester_map = _build_requester_map()
    rows = list(
        qs.order_by("-recorded_at").values(
            "recorded_at", "status_code", "duration_ms", "user_id", "request_params"
        )[:limit]
    )

    return JsonResponse(
        {
            "window": window,
            "method": method,
            "route": route,
            "since": since.isoformat(),
            "sampleCount": sample_count,
            "sampling": dict(_SAMPLING_POLICY),
            "samples": [
                {
                    "recordedAt": row["recorded_at"].isoformat(),
                    "statusCode": row["status_code"],
                    "durationMs": row["duration_ms"],
                    "requester": _resolve_requester(row["user_id"], requester_map),
                    "requestParams": row["request_params"],
                }
                for row in rows
            ],
        }
    )
