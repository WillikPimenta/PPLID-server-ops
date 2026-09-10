from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("portal_notifications", "0002_notification_rules"),
    ]

    operations = [
        migrations.AddField(model_name="portalnotificationrule", name="lifecycle", field=models.CharField(choices=[("unconfigured", "Não configurada"), ("draft", "Rascunho"), ("review", "Em revisão"), ("published", "Publicada"), ("suspended", "Suspensa"), ("discarded", "Descartada")], db_index=True, default="unconfigured", max_length=20)),
        migrations.AddField(model_name="portalnotificationrule", name="integration_status", field=models.CharField(choices=[("not_integrated", "Não integrada"), ("partial", "Parcialmente integrada"), ("integrated", "Integrada"), ("error", "Integração com erro")], db_index=True, default="not_integrated", max_length=20)),
        migrations.AddField(model_name="portalnotificationrule", name="recipient_types", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="portalnotificationrule", name="escalation_recipient_types", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="portalnotificationrule", name="channels", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="portalnotificationrule", name="frequency", field=models.CharField(choices=[("immediate", "Imediatamente"), ("per_occurrence", "Uma vez por ocorrência"), ("per_state_change", "Uma vez por mudança de estado"), ("rate_limited", "No máximo uma vez por período"), ("daily_digest", "No máximo uma vez por dia"), ("weekly_digest", "No máximo uma vez por semana")], default="per_occurrence", max_length=24)),
        migrations.AddField(model_name="portalnotificationrule", name="frequency_window_minutes", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="portalnotificationrule", name="notification_title", field=models.CharField(blank=True, default="", max_length=160)),
        migrations.AddField(model_name="portalnotificationrule", name="notification_message", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="portalnotificationrule", name="target_url", field=models.CharField(blank=True, default="", max_length=500)),
        migrations.AddField(model_name="portalnotificationrule", name="version", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="portalnotificationrule", name="published_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="portalnotificationrule", name="published_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_notification_rules", to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(name="PortalNotificationRuleVersion", fields=[("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("version", models.PositiveIntegerField()), ("snapshot", models.JSONField(default=dict)), ("published_at", models.DateTimeField(auto_now_add=True)), ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="notification_rule_versions", to=settings.AUTH_USER_MODEL)), ("rule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="portal_notifications.portalnotificationrule"))], options={"db_table": "portal_notification_rule_version", "ordering": ["-version"], "constraints": [models.UniqueConstraint(fields=("rule", "version"), name="portal_notif_rule_version_uniq")]}),
        migrations.CreateModel(name="PortalNotificationEventLog", fields=[("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("event_key", models.CharField(db_index=True, max_length=160)), ("source_type", models.CharField(blank=True, default="", max_length=80)), ("source_id", models.CharField(blank=True, default="", max_length=100)), ("outcome", models.CharField(db_index=True, max_length=32)), ("recipient_count", models.PositiveIntegerField(default=0)), ("detail", models.TextField(blank=True, default="")), ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)), ("rule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="event_logs", to="portal_notifications.portalnotificationrule"))], options={"db_table": "portal_notification_event_log", "ordering": ["-created_at"]}),
    ]
