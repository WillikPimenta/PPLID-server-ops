from rest_framework import serializers

from .models import PortalNotification, PortalNotificationRule


class PortalNotificationSerializer(serializers.ModelSerializer):
    is_seen = serializers.SerializerMethodField()
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = PortalNotification
        fields = [
            "id",
            "title",
            "message",
            "kind",
            "target_url",
            "source_type",
            "source_id",
            "is_seen",
            "is_read",
            "created_at",
            "expires_at",
        ]

    def get_is_seen(self, obj) -> bool:
        return obj.seen_at is not None

    def get_is_read(self, obj) -> bool:
        return obj.read_at is not None


class PortalNotificationRuleSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.SerializerMethodField()
    priority_label = serializers.CharField(source="get_priority_display", read_only=True)
    recommendation_label = serializers.CharField(
        source="get_recommendation_display",
        read_only=True,
    )
    decision_label = serializers.CharField(source="get_decision_display", read_only=True)
    lifecycle_label = serializers.CharField(source="get_lifecycle_display", read_only=True)
    integration_status_label = serializers.CharField(source="get_integration_status_display", read_only=True)
    frequency_label = serializers.CharField(source="get_frequency_display", read_only=True)
    published_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PortalNotificationRule
        fields = [
            "id",
            "event_key",
            "section",
            "functionality",
            "route",
            "event_label",
            "trigger_description",
            "primary_recipient_rule",
            "escalation_recipient_rule",
            "channel",
            "priority",
            "priority_label",
            "action_description",
            "current_coverage",
            "recommendation",
            "recommendation_label",
            "frequency_rule",
            "decision",
            "decision_label",
            "enabled",
            "notes",
            "lifecycle",
            "lifecycle_label",
            "integration_status",
            "integration_status_label",
            "recipient_types",
            "escalation_recipient_types",
            "channels",
            "frequency",
            "frequency_label",
            "frequency_window_minutes",
            "notification_title",
            "notification_message",
            "target_url",
            "version",
            "published_at",
            "published_by_name",
            "catalog_managed",
            "updated_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "event_key",
            "section",
            "functionality",
            "route",
            "event_label",
            "trigger_description",
            "catalog_managed",
            "updated_by_name",
            "created_at",
            "updated_at",
            "version",
            "published_at",
            "published_by_name",
            "lifecycle",
            "integration_status",
        ]

    def validate(self, attrs):
        """Valida os campos estruturados do rascunho operacional."""
        errors = {}
        allowed_recipients = {
            "requester", "affected_user", "participants", "current_leader",
            "author", "responsible_user", "route_permission_users",
            "planning_approvers", "quality_team", "support_team",
        }
        for field in ("recipient_types", "escalation_recipient_types"):
            values = attrs.get(field, getattr(self.instance, field, []))
            invalid = sorted(set(values) - allowed_recipients)
            if invalid:
                errors[field] = f"Destinatários inválidos: {', '.join(invalid)}."
        channels = attrs.get("channels", getattr(self.instance, "channels", []))
        invalid_channels = sorted(set(channels) - {"portal"})
        if invalid_channels:
            errors["channels"] = "Somente o canal Portal está disponível no momento."
        target_url = attrs.get("target_url", getattr(self.instance, "target_url", ""))
        if target_url and (not target_url.startswith("/") or target_url.startswith("//")):
            errors["target_url"] = "Informe uma rota interna do portal."
        frequency = attrs.get("frequency", getattr(self.instance, "frequency", ""))
        window = attrs.get("frequency_window_minutes", getattr(self.instance, "frequency_window_minutes", None))
        if frequency == PortalNotificationRule.Frequency.RATE_LIMITED and not window:
            errors["frequency_window_minutes"] = "Informe o intervalo da limitação."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def validate_for_publish(self):
        rule = self.instance
        errors = {}
        if not rule.recipient_types:
            errors["recipient_types"] = "Selecione ao menos um destinatário principal."
        if rule.channels != ["portal"]:
            errors["channels"] = "A regra publicada deve utilizar o canal Portal."
        if not rule.notification_title.strip():
            errors["notification_title"] = "Informe o título da notificação."
        if rule.integration_status != PortalNotificationRule.IntegrationStatus.INTEGRATED:
            errors["integration_status"] = "Somente eventos integrados podem ser publicados."
        if errors:
            raise serializers.ValidationError(errors)

    def get_updated_by_name(self, obj) -> str:
        if not obj.updated_by:
            return ""
        return obj.updated_by.get_full_name() or obj.updated_by.get_username()

    def get_published_by_name(self, obj) -> str:
        if not obj.published_by:
            return ""
        return obj.published_by.get_full_name() or obj.published_by.get_username()
