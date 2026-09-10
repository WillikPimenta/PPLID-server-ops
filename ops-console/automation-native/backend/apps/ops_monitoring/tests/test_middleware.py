from django.test import RequestFactory, TestCase, override_settings
from django.http import HttpResponse

from unittest.mock import patch
from types import SimpleNamespace

from apps.ops_monitoring.middleware import (
    RequestMetricsMiddleware,
    _enqueue_traffic,
    _metric_user_id,
    flush_metrics_buffer,
)
from apps.ops_monitoring.models import ApiRequestMetric, ApiTrafficBucket, ApiTrafficUserBucket
from apps.ops_monitoring.route_normalizer import normalize_route, should_skip_path


class RouteNormalizerTests(TestCase):
    def test_normalize_uuid(self):
        path = "/api/v1/escala/550e8400-e29b-41d4-a716-446655440000/"
        self.assertIn("{uuid}", normalize_route(path))

    def test_normalize_numeric_id(self):
        self.assertEqual(normalize_route("/api/v1/users/42/"), "/api/v1/users/{id}/")

    def test_skip_health(self):
        self.assertTrue(should_skip_path("/api/v1/health/"))


@override_settings(
    MIDDLEWARE=[
        "django.middleware.security.SecurityMiddleware",
        "apps.ops_monitoring.middleware.RequestMetricsMiddleware",
    ]
)
class RequestMetricsMiddlewareTests(TestCase):
    def test_metric_user_id_normalizes_uuid_without_exceeding_integer(self):
        uuid_user = SimpleNamespace(
            is_authenticated=True,
            pk="8adc4863-6b12-4bd9-8567-cbfbc8ed693d",
        )
        normalized = _metric_user_id(uuid_user)

        self.assertEqual(normalized, _metric_user_id(uuid_user))
        self.assertGreater(normalized, 0)
        self.assertLessEqual(normalized, 2_147_483_647)
        self.assertEqual(
            _metric_user_id(SimpleNamespace(is_authenticated=True, pk=7)),
            7,
        )
        self.assertIsNone(
            _metric_user_id(SimpleNamespace(is_authenticated=False, pk=7))
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RequestMetricsMiddleware(lambda r: HttpResponse("ok", status=200))

    def test_records_request(self):
        request = self.factory.get("/api/v1/dashboard/overview/")
        with patch("apps.ops_monitoring.middleware.time.time", return_value=1000.0):
            response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        flush_metrics_buffer()
        self.assertGreaterEqual(ApiRequestMetric.objects.count(), 1)
        row = ApiRequestMetric.objects.first()
        self.assertEqual(row.route, "/api/v1/dashboard/overview/")

    def test_aggregates_every_request_by_minute(self):
        for status in (200, 201, 404, 503):
            _enqueue_traffic(
                method="GET",
                route="/api/v1/dashboard/overview/",
                status_code=status,
                duration_ms=25,
                user_id=7,
            )
        flush_metrics_buffer()

        row = ApiTrafficBucket.objects.get(route="/api/v1/dashboard/overview/")
        self.assertEqual(row.request_count, 4)
        self.assertEqual(row.total_duration_ms, 100)
        self.assertEqual(row.status_2xx, 2)
        self.assertEqual(row.status_4xx, 1)
        self.assertEqual(row.status_5xx, 1)
        self.assertEqual(ApiTrafficUserBucket.objects.filter(user_id=7).count(), 1)

    def test_skips_health(self):
        before = ApiRequestMetric.objects.count()
        request = self.factory.get("/api/v1/health/")
        self.middleware(request)
        flush_metrics_buffer()
        self.assertEqual(ApiRequestMetric.objects.count(), before)
