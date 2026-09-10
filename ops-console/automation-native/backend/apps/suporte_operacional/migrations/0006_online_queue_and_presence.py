from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0005_request_type_and_offline_path"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="cliente_id",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="queue_origin",
            field=models.CharField(blank=True, db_index=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="queue_slot",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="queue_sla_started_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="workflow_id",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="OperationalSupportAgentPresence",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("online", "Online"),
                            ("offline", "Offline"),
                            ("ausente", "Ausente"),
                        ],
                        db_index=True,
                        default="offline",
                        max_length=16,
                    ),
                ),
                ("status_changed_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("last_assigned_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operational_support_presence",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "operational_support_agent_presence",
                "ordering": ["user__username"],
            },
        ),
    ]
