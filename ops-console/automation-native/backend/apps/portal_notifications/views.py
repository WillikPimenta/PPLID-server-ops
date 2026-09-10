from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permissions import HasPortalPermission
from apps.access.registry import ADM_CONFIG_HUB

from .models import PortalNotificationRule
from .serializers import PortalNotificationRuleSerializer, PortalNotificationSerializer
from .services import (
    active_notifications_for,
    purge_expired_notifications,
    sync_notification_rule_catalog,
    publish_notification_rule,
    simulate_notification_rule,
)


def _counts(user, *, now=None) -> dict:
    qs = active_notifications_for(user, now=now)
    return {
        "unread_count": qs.filter(read_at__isnull=True).count(),
        "unseen_count": qs.filter(seen_at__isnull=True).count(),
    }


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        purge_expired_notifications(now=now)
        qs = active_notifications_for(request.user, now=now)
        if (request.query_params.get("filter") or "").strip() == "unread":
            qs = qs.filter(read_at__isnull=True)

        try:
            page = max(int(request.query_params.get("page") or 1), 1)
            page_size = min(max(int(request.query_params.get("page_size") or 20), 1), 50)
        except ValueError:
            page, page_size = 1, 20

        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs[start : start + page_size])
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                **_counts(request.user, now=now),
                "results": PortalNotificationSerializer(rows, many=True).data,
            }
        )


class MarkAllSeenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        updated = active_notifications_for(request.user, now=now).filter(
            seen_at__isnull=True
        ).update(seen_at=now)
        return Response({"updated": updated, **_counts(request.user, now=now)})


class MarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        updated = active_notifications_for(request.user, now=now).filter(
            read_at__isnull=True
        ).update(seen_at=now, read_at=now)
        return Response({"updated": updated, **_counts(request.user, now=now)})


class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, notification_id):
        now = timezone.now()
        notification = get_object_or_404(
            active_notifications_for(request.user, now=now),
            pk=notification_id,
        )
        update_fields = []
        if notification.seen_at is None:
            notification.seen_at = now
            update_fields.append("seen_at")
        if notification.read_at is None:
            notification.read_at = now
            update_fields.append("read_at")
        if update_fields:
            notification.save(update_fields=update_fields)
        return Response(
            {
                "notification": PortalNotificationSerializer(notification).data,
                **_counts(request.user, now=now),
            }
        )


class NotificationRuleListView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CONFIG_HUB

    def get(self, request):
        rows = PortalNotificationRule.objects.select_related("updated_by").all()
        return Response(
            {
                "count": rows.count(),
                # Mantido por compatibilidade com clientes anteriores. A leitura
                # não sincroniza nem escreve no banco; use o endpoint /sync/.
                "synced_created": 0,
                "choices": {
                    "priority": [
                        {"value": value, "label": label}
                        for value, label in PortalNotificationRule.Priority.choices
                    ],
                    "recommendation": [
                        {"value": value, "label": label}
                        for value, label in PortalNotificationRule.Recommendation.choices
                    ],
                    "decision": [
                        {"value": value, "label": label}
                        for value, label in PortalNotificationRule.Decision.choices
                    ],
                    "lifecycle": [
                        {"value": value, "label": label}
                        for value, label in PortalNotificationRule.Lifecycle.choices
                    ],
                    "integration_status": [
                        {"value": value, "label": label}
                        for value, label in PortalNotificationRule.IntegrationStatus.choices
                    ],
                    "frequency": [
                        {"value": value, "label": label}
                        for value, label in PortalNotificationRule.Frequency.choices
                    ],
                },
                "results": PortalNotificationRuleSerializer(rows, many=True).data,
            }
        )


class NotificationRuleDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CONFIG_HUB

    def patch(self, request, rule_id):
        rule = get_object_or_404(PortalNotificationRule, pk=rule_id)
        serializer = PortalNotificationRuleSerializer(rule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        if serializer.validated_data and rule.lifecycle in {
            PortalNotificationRule.Lifecycle.UNCONFIGURED,
            PortalNotificationRule.Lifecycle.PUBLISHED,
            PortalNotificationRule.Lifecycle.REVIEW,
        }:
            rule.lifecycle = PortalNotificationRule.Lifecycle.DRAFT
            rule.save(update_fields=["lifecycle", "updated_at"])
        return Response(PortalNotificationRuleSerializer(rule).data)


class NotificationRuleSyncView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CONFIG_HUB

    def post(self, request):
        created = sync_notification_rule_catalog()
        return Response(
            {
                "created": created,
                "count": PortalNotificationRule.objects.count(),
            }
        )


class NotificationRuleActionView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_CONFIG_HUB

    def post(self, request, rule_id, action):
        rule = get_object_or_404(PortalNotificationRule, pk=rule_id)
        serializer = PortalNotificationRuleSerializer(rule)
        if action == "simulate":
            return Response(simulate_notification_rule(rule))
        if action == "review":
            rule.lifecycle = PortalNotificationRule.Lifecycle.REVIEW
            rule.updated_by = request.user
            rule.save(update_fields=["lifecycle", "updated_by", "updated_at"])
        elif action == "publish":
            serializer.validate_for_publish()
            publish_notification_rule(rule, user=request.user)
        elif action == "suspend":
            rule.lifecycle = PortalNotificationRule.Lifecycle.SUSPENDED
            rule.enabled = False
            rule.updated_by = request.user
            rule.save(update_fields=["lifecycle", "enabled", "updated_by", "updated_at"])
        elif action == "discard":
            rule.lifecycle = PortalNotificationRule.Lifecycle.DISCARDED
            rule.enabled = False
            rule.updated_by = request.user
            rule.save(update_fields=["lifecycle", "enabled", "updated_by", "updated_at"])
        else:
            return Response({"detail": "Ação inválida."}, status=400)
        return Response(PortalNotificationRuleSerializer(rule).data)
