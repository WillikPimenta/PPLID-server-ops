# Generated manually for exact API traffic aggregation

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops_monitoring", "0002_rename_ops_api_req_route_idx_ops_api_req_route_719e41_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiTrafficBucket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bucket_start", models.DateTimeField(db_index=True)),
                ("method", models.CharField(max_length=10)),
                ("route", models.CharField(max_length=255)),
                ("request_count", models.PositiveBigIntegerField(default=0)),
                ("total_duration_ms", models.PositiveBigIntegerField(default=0)),
                ("max_duration_ms", models.PositiveIntegerField(default=0)),
                ("status_2xx", models.PositiveBigIntegerField(default=0)),
                ("status_3xx", models.PositiveBigIntegerField(default=0)),
                ("status_4xx", models.PositiveBigIntegerField(default=0)),
                ("status_5xx", models.PositiveBigIntegerField(default=0)),
            ],
            options={
                "db_table": "ops_api_traffic_bucket",
                "indexes": [
                    models.Index(fields=["route", "-bucket_start"], name="ops_api_tra_route_ba5cc7_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("bucket_start", "method", "route"),
                        name="ops_traffic_bucket_route_uniq",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ApiTrafficUserBucket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bucket_start", models.DateTimeField(db_index=True)),
                ("user_id", models.IntegerField()),
            ],
            options={
                "db_table": "ops_api_traffic_user_bucket",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("bucket_start", "user_id"),
                        name="ops_traffic_user_bucket_uniq",
                    ),
                ],
            },
        ),
    ]

