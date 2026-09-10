# Generated manually for the portal notification center.

import apps.portal_notifications.models
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PortalNotification",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=160)),
                ("message", models.TextField(blank=True, default="")),
                ("kind", models.CharField(choices=[("info", "Informação"), ("success", "Sucesso"), ("warning", "Atenção"), ("error", "Crítica")], default="info", max_length=16)),
                ("target_url", models.CharField(blank=True, default="", max_length=500)),
                ("source_type", models.CharField(blank=True, default="", max_length=80)),
                ("source_id", models.CharField(blank=True, default="", max_length=100)),
                ("dedupe_key", models.CharField(blank=True, max_length=220, null=True)),
                ("seen_at", models.DateTimeField(blank=True, null=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("expires_at", models.DateTimeField(db_index=True, default=apps.portal_notifications.models.default_notification_expiry)),
                ("recipient", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="portal_notifications", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "portal_notification",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="portalnotification",
            index=models.Index(fields=["recipient", "read_at", "-created_at"], name="portal_notif_user_read_idx"),
        ),
        migrations.AddIndex(
            model_name="portalnotification",
            index=models.Index(fields=["recipient", "seen_at", "-created_at"], name="portal_notif_user_seen_idx"),
        ),
        migrations.AddConstraint(
            model_name="portalnotification",
            constraint=models.UniqueConstraint(fields=("recipient", "dedupe_key"), name="portal_notif_user_dedupe_uniq"),
        ),
    ]

