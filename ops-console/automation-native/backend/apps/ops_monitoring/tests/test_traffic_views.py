from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.ops_monitoring.middleware import _metric_user_id
from apps.ops_monitoring.models import (
    ApiRequestMetric,
    ApiTrafficBucket,
    ApiTrafficUserBucket,
)
from apps.ops_monitoring.views import _build_api_traffic, _build_sampled_metrics


class ApiTrafficSummaryTests(TestCase):
    def setUp(self):
        # O middleware instrumenta inclusive as requisições feitas por outros
        # testes; cada cenário abaixo precisa partir de agregados determinísticos.
        ApiRequestMetric.objects.all().delete()
        ApiTrafficBucket.objects.all().delete()
        ApiTrafficUserBucket.objects.all().delete()

    def test_sampled_metrics_separate_success_and_http_errors(self):
        now = timezone.now()
        samples = [
            ApiRequestMetric(
                recorded_at=now,
                method="GET",
                route="/api/v1/dashboard/overview/",
                status_code=200,
                duration_ms=120,
            ),
            ApiRequestMetric(
                recorded_at=now,
                method="GET",
                route="/api/v1/dashboard/overview/",
                status_code=403,
                duration_ms=10,
            ),
            ApiRequestMetric(
                recorded_at=now,
                method="GET",
                route="/api/v1/dashboard/overview/",
                status_code=403,
                duration_ms=20,
            ),
            ApiRequestMetric(
                recorded_at=now,
                method="POST",
                route="/api/v1/report/",
                status_code=503,
                duration_ms=500,
            ),
        ]
        ApiRequestMetric.objects.bulk_create(samples)

        totals, routes = _build_sampled_metrics(ApiRequestMetric.objects.all())

        self.assertEqual(totals["sampleCount"], 4)
        self.assertEqual(totals["successSamples"], 1)
        self.assertEqual(totals["errors4xx"], 2)
        self.assertEqual(totals["errors5xx"], 1)
        self.assertEqual(totals["successAvgMs"], 120.0)
        self.assertEqual(totals["clientErrorAvgMs"], 15.0)
        self.assertEqual(totals["serverErrorAvgMs"], 500.0)

        overview = next(row for row in routes if row["route"].endswith("overview/"))
        self.assertEqual(overview["status2xx"], 1)
        self.assertEqual(overview["status4xx"], 2)
        self.assertEqual(overview["successAvgMs"], 120.0)
        self.assertEqual(overview["clientErrorAvgMs"], 15.0)

    def test_builds_exact_series_totals_and_route_ranking(self):
        bucket_start = (timezone.now() - timedelta(minutes=5)).replace(second=0, microsecond=0)
        ApiTrafficBucket.objects.create(
            bucket_start=bucket_start,
            method="GET",
            route="/api/v1/dashboard/overview/",
            request_count=20,
            total_duration_ms=1000,
            max_duration_ms=120,
            status_2xx=18,
            status_4xx=1,
            status_5xx=1,
        )
        ApiTrafficBucket.objects.create(
            bucket_start=bucket_start,
            method="POST",
            route="/api/v1/report/",
            request_count=5,
            total_duration_ms=500,
            max_duration_ms=180,
            status_2xx=5,
        )
        ApiTrafficUserBucket.objects.create(bucket_start=bucket_start, user_id=10)
        ApiTrafficUserBucket.objects.create(bucket_start=bucket_start, user_id=11)

        traffic = _build_api_traffic("1h", timezone.now() - timedelta(hours=1))

        self.assertTrue(traffic["available"])
        self.assertEqual(traffic["source"], "exact_aggregate")
        self.assertEqual(traffic["totals"]["requests"], 25)
        self.assertEqual(traffic["totals"]["status4xx"], 1)
        self.assertEqual(traffic["totals"]["status5xx"], 1)
        self.assertEqual(traffic["totals"]["uniqueUsers"], 2)
        self.assertEqual(traffic["activeUsers"]["count"], 2)
        self.assertEqual(traffic["activeUsers"]["windowMinutes"], 5)
        self.assertEqual(traffic["totals"]["activeUsersNow"], 2)
        self.assertTrue(any(point["uniqueUsers"] == 2 for point in traffic["points"]))
        self.assertEqual(traffic["topRoutes"][0]["route"], "/api/v1/dashboard/overview/")
        self.assertTrue(any(point["requests"] == 25 for point in traffic["points"]))

    def test_active_users_excludes_activity_older_than_five_minutes(self):
        now = timezone.now().replace(second=0, microsecond=0)
        ApiTrafficUserBucket.objects.create(bucket_start=now, user_id=10)
        ApiTrafficUserBucket.objects.create(
            bucket_start=now - timedelta(minutes=6),
            user_id=11,
        )

        traffic = _build_api_traffic("1h", now - timedelta(hours=1))

        self.assertEqual(traffic["totals"]["uniqueUsers"], 2)
        self.assertEqual(traffic["activeUsers"]["count"], 1)


class ApiRouteSamplesViewTests(TestCase):
    def setUp(self):
        ApiRequestMetric.objects.all().delete()
        ApiTrafficBucket.objects.all().delete()
        ApiTrafficUserBucket.objects.all().delete()

    def test_route_samples_filters_by_method_route_and_resolves_requester(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="123456",
            email="123456@test.local",
            password="test-pass",
        )
        metric_user_id = _metric_user_id(user)
        now = timezone.now()
        route = "/api/v1/dashboard/overview/"
        ApiRequestMetric.objects.bulk_create(
            [
                ApiRequestMetric(
                    recorded_at=now,
                    method="GET",
                    route=route,
                    status_code=200,
                    duration_ms=120,
                    user_id=metric_user_id,
                    request_params={"query": {"periodo": "7d", "equipe": "nh"}},
                ),
                ApiRequestMetric(
                    recorded_at=now,
                    method="GET",
                    route=route,
                    status_code=403,
                    duration_ms=10,
                    user_id=None,
                ),
                ApiRequestMetric(
                    recorded_at=now,
                    method="POST",
                    route="/api/v1/report/",
                    status_code=503,
                    duration_ms=500,
                    user_id=metric_user_id,
                ),
            ]
        )

        response = self.client.get(
            "/api/v1/ops-metrics/route-samples/",
            {
                "window": "6h",
                "method": "GET",
                "route": route,
                "limit": 10,
            },
            REMOTE_ADDR="127.0.0.1",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["route"], route)
        self.assertEqual(payload["sampleCount"], 2)
        self.assertEqual(len(payload["samples"]), 2)
        requesters = {row["requester"] for row in payload["samples"]}
        self.assertIn("Anônimo", requesters)
        self.assertTrue(any(name != "Anônimo" for name in requesters))
        with_params = next(row for row in payload["samples"] if row.get("requestParams"))
        self.assertEqual(with_params["requestParams"]["query"]["periodo"], "7d")

    def test_route_samples_requires_method_and_route(self):
        response = self.client.get(
            "/api/v1/ops-metrics/route-samples/",
            {"window": "6h"},
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("method", response.json()["error"])
