import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("escala_flex", "0022_delete_productionrecord"),
        ("workforce", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalAlertEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operational_date", models.DateField()),
                ("alert_type", models.CharField(max_length=64)),
                ("fingerprint", models.CharField(max_length=64)),
                ("source_type", models.CharField(blank=True, max_length=32)),
                ("source_id", models.CharField(blank=True, max_length=64)),
                ("status", models.CharField(choices=[("active", "Ativo"), ("resolved", "Resolvido")], default="active", max_length=16)),
                ("priority_initial", models.CharField(blank=True, max_length=16)),
                ("priority_current", models.CharField(blank=True, max_length=16)),
                ("state", models.CharField(blank=True, max_length=255)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("recommended_action", models.TextField(blank=True)),
                ("first_seen_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField()),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("occurrence_count", models.PositiveIntegerField(default=1)),
                ("leader_lan_id", models.CharField(blank=True, max_length=64)),
                ("leader_name", models.CharField(blank=True, max_length=255)),
                ("location", models.CharField(blank=True, max_length=255)),
                ("sector", models.CharField(blank=True, max_length=255)),
                ("job_activity", models.CharField(blank=True, max_length=255)),
                ("context", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operational_alert_events", to="workforce.agent")),
            ],
            options={
                "db_table": "ef_operational_alert_event",
                "ordering": ("-first_seen_at", "-created_at"),
            },
        ),
        migrations.AddIndex(
            model_name="operationalalertevent",
            index=models.Index(fields=["operational_date", "status"], name="ef_alert_date_status_idx"),
        ),
        migrations.AddIndex(
            model_name="operationalalertevent",
            index=models.Index(fields=["agent", "first_seen_at"], name="ef_alert_agent_seen_idx"),
        ),
        migrations.AddIndex(
            model_name="operationalalertevent",
            index=models.Index(fields=["alert_type", "status"], name="ef_alert_type_status_idx"),
        ),
        migrations.AddIndex(
            model_name="operationalalertevent",
            index=models.Index(fields=["leader_lan_id", "operational_date"], name="ef_alert_leader_date_idx"),
        ),
        migrations.AddConstraint(
            model_name="operationalalertevent",
            constraint=models.UniqueConstraint(condition=Q(("status", "active")), fields=("fingerprint",), name="ef_unique_active_alert_fp"),
        ),
    ]
