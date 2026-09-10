from django.db import models


class ApiRequestMetric(models.Model):
    recorded_at = models.DateTimeField(db_index=True)
    method = models.CharField(max_length=10)
    route = models.CharField(max_length=255, db_index=True)
    status_code = models.PositiveSmallIntegerField()
    duration_ms = models.PositiveIntegerField()
    user_id = models.IntegerField(null=True, blank=True)
    request_params = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "ops_api_request_metric"
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["route", "-recorded_at"]),
            models.Index(fields=["status_code", "-recorded_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.method} {self.route} {self.status_code} {self.duration_ms}ms"


class ApiTrafficBucket(models.Model):
    """Contagem exata de tráfego agregada por minuto, método e rota."""

    bucket_start = models.DateTimeField(db_index=True)
    method = models.CharField(max_length=10)
    route = models.CharField(max_length=255)
    request_count = models.PositiveBigIntegerField(default=0)
    total_duration_ms = models.PositiveBigIntegerField(default=0)
    max_duration_ms = models.PositiveIntegerField(default=0)
    status_2xx = models.PositiveBigIntegerField(default=0)
    status_3xx = models.PositiveBigIntegerField(default=0)
    status_4xx = models.PositiveBigIntegerField(default=0)
    status_5xx = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "ops_api_traffic_bucket"
        constraints = [
            models.UniqueConstraint(
                fields=["bucket_start", "method", "route"],
                name="ops_traffic_bucket_route_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["route", "-bucket_start"],
                name="ops_api_tra_route_ba5cc7_idx",
            )
        ]


class ApiTrafficUserBucket(models.Model):
    """Presença por minuto para contar usuários autenticados sem expor identidades."""

    bucket_start = models.DateTimeField(db_index=True)
    user_id = models.IntegerField()

    class Meta:
        db_table = "ops_api_traffic_user_bucket"
        constraints = [
            models.UniqueConstraint(
                fields=["bucket_start", "user_id"],
                name="ops_traffic_user_bucket_uniq",
            )
        ]
