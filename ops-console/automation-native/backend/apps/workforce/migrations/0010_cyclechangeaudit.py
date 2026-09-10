import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workforce", "0009_headcountcatalogitem"),
    ]

    operations = [
        migrations.CreateModel(
            name="CycleChangeAudit",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operation_id", models.UUIDField(db_index=True)),
                ("action", models.CharField(max_length=32)),
                ("success", models.BooleanField(default=True)),
                ("error_detail", models.TextField(blank=True, default="")),
                ("closed_history_id", models.UUIDField(blank=True, null=True)),
                ("created_history_id", models.UUIDField(blank=True, null=True)),
                ("movement_date", models.DateField(blank=True, null=True)),
                ("field_changes", models.JSONField(blank=True, default=list)),
                ("entities", models.JSONField(blank=True, default=list)),
                ("technical", models.JSONField(blank=True, default=list)),
                ("result_summary", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cycle_change_audits",
                        to="workforce.agent",
                    ),
                ),
                (
                    "performed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cycle_change_audits",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "cycle_change_audit",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="cyclechangeaudit",
            index=models.Index(fields=["agent", "-created_at"], name="cycle_chang_agent_i_7d2a1c_idx"),
        ),
    ]
