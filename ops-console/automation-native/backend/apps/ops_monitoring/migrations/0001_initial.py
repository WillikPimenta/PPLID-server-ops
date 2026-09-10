# Generated manually for ops_monitoring

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ApiRequestMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recorded_at", models.DateTimeField(db_index=True)),
                ("method", models.CharField(max_length=10)),
                ("route", models.CharField(db_index=True, max_length=255)),
                ("status_code", models.PositiveSmallIntegerField()),
                ("duration_ms", models.PositiveIntegerField()),
                ("user_id", models.IntegerField(blank=True, null=True)),
            ],
            options={
                "db_table": "ops_api_request_metric",
                "ordering": ["-recorded_at"],
                "indexes": [
                    models.Index(fields=["route", "-recorded_at"], name="ops_api_req_route_idx"),
                    models.Index(fields=["status_code", "-recorded_at"], name="ops_api_req_status_idx"),
                ],
            },
        ),
    ]
