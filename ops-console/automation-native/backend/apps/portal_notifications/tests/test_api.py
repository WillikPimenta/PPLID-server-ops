from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.portal_notifications.models import PortalNotification, PortalNotificationEventLog, PortalNotificationRule
from apps.portal_notifications.services import (
    create_notification,
    emit_notification_event,
    load_default_rule_catalog,
    publish_notification_rule,
    sync_notification_rule_catalog,
)


class PortalNotificationApiTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="notified.user",
            email="notified.user@test.local",
            password="test-pass",
        )
        self.other = User.objects.create_user(
            username="other.user",
            email="other.user@test.local",
            password="test-pass",
        )
        self.client.force_authenticate(self.user)

    def test_list_and_read_state_are_scoped_to_recipient(self):
        notification = create_notification(
            recipient=self.user,
            title="Solicitação respondida",
            message="A Qualidade enviou uma devolutiva.",
            target_url="/operacao/suporte-operacional",
        )
        create_notification(recipient=self.other, title="Privada")

        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["unread_count"], 1)
        self.assertEqual(response.data["unseen_count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(notification.id))

        response = self.client.post("/api/v1/notifications/mark-seen/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unseen_count"], 0)
        self.assertEqual(response.data["unread_count"], 1)

        response = self.client.post(f"/api/v1/notifications/{notification.id}/read/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unread_count"], 0)

    def test_cannot_mark_another_users_notification_as_read(self):
        notification = create_notification(recipient=self.other, title="Privada")
        response = self.client.post(f"/api/v1/notifications/{notification.id}/read/")
        self.assertEqual(response.status_code, 404)

    def test_expired_notifications_are_hidden_and_purged(self):
        notification = create_notification(recipient=self.user, title="Expirada")
        PortalNotification.objects.filter(pk=notification.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertFalse(PortalNotification.objects.filter(pk=notification.pk).exists())

    def test_dedupe_key_prevents_duplicate_notifications(self):
        first = create_notification(
            recipient=self.user,
            title="Troca aprovada",
            dedupe_key="swap:123:approved",
        )
        second = create_notification(
            recipient=self.user,
            title="Troca aprovada novamente",
            dedupe_key="swap:123:approved",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PortalNotification.objects.count(), 1)


    def test_event_engine_uses_published_snapshot_and_records_delivery(self):
        rule = PortalNotificationRule.objects.create(
            event_key="test.operational", section="Teste", functionality="Teste",
            event_label="Evento", recipient_types=["participants"], channels=["portal"],
            integration_status=PortalNotificationRule.IntegrationStatus.INTEGRATED,
            notification_title="Título publicado", notification_message="Olá {nome}",
            target_url="/teste", lifecycle=PortalNotificationRule.Lifecycle.REVIEW,
        )
        publish_notification_rule(rule, user=self.user)
        rule.notification_title = "Rascunho ainda não publicado"
        rule.save(update_fields=["notification_title"])

        created = emit_notification_event(
            event_key="test.operational", recipients_by_type={"participants": [self.other]},
            context={"nome": "Maria"}, source_type="test", source_id="1",
            dedupe_key="test-operational-1",
        )

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].title, "Título publicado")
        self.assertEqual(created[0].message, "Olá Maria")
        self.assertTrue(PortalNotificationEventLog.objects.filter(outcome="sent").exists())


class PortalNotificationRuleApiTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="notification.admin",
            email="notification.admin@test.local",
            password="test-pass",
        )
        self.client.force_authenticate(self.admin)

    def test_catalog_has_unique_keys_and_failure_assignment_rule(self):
        rows = load_default_rule_catalog()
        keys = [row["event_key"] for row in rows]
        self.assertEqual(len(rows), 92)
        self.assertEqual(len(keys), len(set(keys)))
        failure = next(row for row in rows if row["event_label"] == "Falha atribuída ao agente")
        self.assertEqual(failure["primary_recipient_rule"], "Agente que recebeu a falha")
        self.assertEqual(failure["escalation_recipient_rule"], "Líder atual do agente")

    def test_sync_inserts_new_rules_without_overwriting_adjustments(self):
        sync_notification_rule_catalog()
        rule = PortalNotificationRule.objects.get(event_key="matrix.n040")
        rule.channel = "Portal + Teams"
        rule.decision = PortalNotificationRule.Decision.APPROVED
        rule.save(update_fields=["channel", "decision"])

        created = sync_notification_rule_catalog()

        rule.refresh_from_db()
        self.assertEqual(created, 0)
        self.assertEqual(rule.channel, "Portal + Teams")
        self.assertEqual(rule.decision, PortalNotificationRule.Decision.APPROVED)

    def test_list_and_patch_notification_rule(self):
        sync_notification_rule_catalog()
        response = self.client.get("/api/v1/notifications/rules/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreaterEqual(response.data["count"], 92)
        rule = next(
            row
            for row in response.data["results"]
            if row["event_label"] == "Falha atribuída ao agente"
        )

        response = self.client.patch(
            f"/api/v1/notifications/rules/{rule['id']}/",
            {
                "channel": "Portal + navegador",
                "decision": "approved",
                "notes": "Validado pela operação.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["channel"], "Portal + navegador")
        self.assertEqual(response.data["decision"], "approved")
        self.assertEqual(response.data["updated_by_name"], "notification.admin")

    def test_list_does_not_implicitly_sync_catalog(self):
        PortalNotificationRule.objects.all().delete()

        with patch(
            "apps.portal_notifications.views.sync_notification_rule_catalog"
        ) as sync_mock:
            response = self.client.get("/api/v1/notifications/rules/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["synced_created"], 0)
        sync_mock.assert_not_called()

    def test_patch_only_updates_sent_operational_field(self):
        sync_notification_rule_catalog()
        rule = PortalNotificationRule.objects.get(event_key="matrix.n040")
        original_decision = rule.decision

        response = self.client.patch(
            f"/api/v1/notifications/rules/{rule.id}/",
            {"channel": "Portal + Teams"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        rule.refresh_from_db()
        self.assertEqual(rule.channel, "Portal + Teams")
        self.assertEqual(rule.decision, original_decision)

    def test_catalog_metadata_is_read_only_on_patch(self):
        sync_notification_rule_catalog()
        rule = PortalNotificationRule.objects.get(event_key="matrix.n040")
        original = {
            "event_key": rule.event_key,
            "section": rule.section,
            "functionality": rule.functionality,
            "route": rule.route,
            "event_label": rule.event_label,
            "trigger_description": rule.trigger_description,
            "catalog_managed": rule.catalog_managed,
        }

        response = self.client.patch(
            f"/api/v1/notifications/rules/{rule.id}/",
            {
                "event_key": "changed.key",
                "section": "Outra",
                "functionality": "Outra funcionalidade",
                "route": "/outra",
                "event_label": "Outro evento",
                "trigger_description": "Outro gatilho",
                "catalog_managed": False,
                "notes": "Alteração permitida",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        rule.refresh_from_db()
        for field, value in original.items():
            self.assertEqual(getattr(rule, field), value)
        self.assertEqual(rule.notes, "Alteração permitida")

    def test_publish_requires_structured_operational_configuration(self):
        rule = PortalNotificationRule.objects.create(
            event_key="legacy.disabled",
            section="Legado",
            functionality="Regra legada",
            event_label="Evento legado",
            primary_recipient_rule="",
            channel="",
            enabled=False,
            catalog_managed=False,
        )

        response = self.client.post(
            f"/api/v1/notifications/rules/{rule.id}/publish/",
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("recipient_types", response.data)
        self.assertIn("channels", response.data)

    def test_publish_creates_version_and_records_publisher(self):
        rule = PortalNotificationRule.objects.create(
            event_key="legacy.enabled",
            section="Legado",
            functionality="Regra legada",
            event_label="Evento legado",
            recipient_types=["participants"],
            channels=["portal"],
            integration_status=PortalNotificationRule.IntegrationStatus.INTEGRATED,
            notification_title="Evento operacional",
            lifecycle=PortalNotificationRule.Lifecycle.REVIEW,
            catalog_managed=False,
        )

        response = self.client.post(
            f"/api/v1/notifications/rules/{rule.id}/publish/",
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["lifecycle"], "published")
        self.assertEqual(response.data["version"], 1)
        self.assertEqual(response.data["published_by_name"], "notification.admin")
        self.assertEqual(rule.versions.count(), 1)

    def test_rule_endpoints_reject_unauthenticated_request(self):
        self.client.force_authenticate(user=None)
        list_response = self.client.get("/api/v1/notifications/rules/")
        sync_response = self.client.post("/api/v1/notifications/rules/sync/")

        # SessionAuthentication usa 403 quando não há credencial autenticada.
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(sync_response.status_code, 403)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_rule_endpoint_denies_user_without_permission(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="notification.no-permission",
            password="test-pass",
        )
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/notifications/rules/")

        self.assertEqual(response.status_code, 403)

    def test_explicit_sync_endpoint_still_synchronizes_catalog(self):
        PortalNotificationRule.objects.all().delete()

        response = self.client.post("/api/v1/notifications/rules/sync/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreater(response.data["created"], 0)
        self.assertEqual(
            response.data["count"],
            PortalNotificationRule.objects.count(),
        )

    def test_sync_creates_review_row_for_route_without_explicit_rule(self):
        sync_notification_rule_catalog()
        rule = PortalNotificationRule.objects.get(
            event_key="route.configuracoes-notificacoes.notification-review"
        )
        self.assertEqual(rule.route, "/configuracoes/notificacoes")
        self.assertFalse(rule.enabled)
        self.assertEqual(rule.decision, PortalNotificationRule.Decision.PENDING)
