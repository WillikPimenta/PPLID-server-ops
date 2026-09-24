from django.urls import path

from .views import (
    ops_metrics_around,
    ops_metrics_in_flight,
    ops_metrics_recent_samples,
    ops_metrics_route_samples,
    ops_metrics_summary,
)

urlpatterns = [
    path("summary/", ops_metrics_summary, name="ops-metrics-summary"),
    path("around/", ops_metrics_around, name="ops-metrics-around"),
    path("route-samples/", ops_metrics_route_samples, name="ops-metrics-route-samples"),
    path("recent-samples/", ops_metrics_recent_samples, name="ops-metrics-recent-samples"),
    path("in-flight/", ops_metrics_in_flight, name="ops-metrics-in-flight"),
]
