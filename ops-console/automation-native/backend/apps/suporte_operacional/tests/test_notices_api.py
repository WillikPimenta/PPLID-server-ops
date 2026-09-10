from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_OP_LIDER, ROLE_QUAL_CAPACITACAO
from apps.communication.models import News
from apps.suporte_operacional.models import OperationalSupportNotice
from apps.suporte_operacional.services.notices import set_notice_active
from apps.suporte_operacional.tests.test_workflow_api import _user_with_role


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OperationalSupportNoticeApiTests(TestCase):
    def setUp(self):
        self.agent = _user_with_role("notice_agent", ROLE_OP_AGENTE, full_name="Agente Mural")
        self.leader = _user_with_role("notice_leader", ROLE_OP_LIDER, full_name="Líder Mural")
        self.client = APIClient()

    def test_notice_is_created_and_listed_outside_portal_news(self):
        self.client.force_authenticate(self.leader)
        created = self.client.post(
            "/api/v1/operational-support/notices/",
            {"title": "Atenção ao procedimento", "message": "Utilize o novo roteiro."},
            format="json",
        )

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["author_name"], "notice_leader")
        self.assertTrue(created.data["active"])
        self.assertEqual(created.data["status"], "active")
        self.assertNotIn("is_critical", created.data)
        self.assertNotIn("acknowledged_by_me", created.data)
        self.assertEqual(OperationalSupportNotice.objects.count(), 1)
        self.assertEqual(News.objects.count(), 0)

        self.client.force_authenticate(self.agent)
        listed = self.client.get("/api/v1/operational-support/notices/")

        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["results"][0]["title"], "Atenção ao procedimento")

    def test_capacitacao_can_create_notice(self):
        capacitacao = _user_with_role(
            "notice_cap",
            ROLE_QUAL_CAPACITACAO,
            full_name="Capacitação Mural",
        )
        self.client.force_authenticate(capacitacao)
        created = self.client.post(
            "/api/v1/operational-support/notices/",
            {"title": "Novo fluxo offline", "message": "Priorizar fila offline."},
            format="json",
        )

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["author_name"], "notice_cap")

    def test_common_agent_cannot_manage_notices(self):
        notice = OperationalSupportNotice.objects.create(
            title="Recado restrito",
            message="Conteudo",
            created_by=self.leader,
            active=False,
        )
        self.client.force_authenticate(self.agent)

        created = self.client.post(
            "/api/v1/operational-support/notices/",
            {"title": "Sem permissao", "message": "Nao publicar"},
            format="json",
        )
        patched = self.client.patch(
            f"/api/v1/operational-support/notices/{notice.id}/",
            {"active": True},
            format="json",
        )
        managed = self.client.get("/api/v1/operational-support/notices/?scope=manage")

        self.assertEqual(created.status_code, 403)
        self.assertEqual(patched.status_code, 403)
        self.assertEqual(managed.status_code, 200)
        self.assertEqual(managed.data["count"], 0)

    def test_notice_activation_helper_sets_inactive_timestamp(self):
        notice = OperationalSupportNotice.objects.create(
            title="Recado para inativar",
            message="Conteudo",
            created_by=self.leader,
        )

        set_notice_active(notice, active=False)

        notice.refresh_from_db()
        self.assertFalse(notice.active)
        self.assertIsNotNone(notice.inactive_at)

    def test_inactive_notice_leaves_public_mural_but_stays_in_manage_history(self):
        self.client.force_authenticate(self.leader)
        notice = OperationalSupportNotice.objects.create(
            title="Recado antigo",
            message="Conteúdo",
            created_by=self.leader,
            active=False,
        )

        listed = self.client.get("/api/v1/operational-support/notices/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["count"], 0)

        managed = self.client.get("/api/v1/operational-support/notices/?scope=manage")
        self.assertEqual(managed.status_code, 200)
        self.assertEqual(managed.data["count"], 1)
        self.assertEqual(managed.data["results"][0]["id"], str(notice.id))
        self.assertFalse(managed.data["results"][0]["active"])

    def test_notice_html_message_is_preserved_on_create_and_list(self):
        html = '<div class="news-block news-block--info"><p>Alerta <strong>importante</strong></p></div>'
        self.client.force_authenticate(self.leader)
        created = self.client.post(
            "/api/v1/operational-support/notices/",
            {"title": "Recado HTML", "message": html},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertIn("news-block--info", created.data["message"])
        self.assertIn("<strong>importante</strong>", created.data["message"])

        listed = self.client.get("/api/v1/operational-support/notices/?scope=manage")
        self.assertEqual(listed.status_code, 200)
        self.assertIn("news-block--info", listed.data["results"][0]["message"])

    def test_manage_scope_lists_inactive_and_patch_updates_status(self):
        capacitacao = _user_with_role(
            "notice_cap_manage",
            ROLE_QUAL_CAPACITACAO,
            full_name="Capacitação Manage",
        )
        notice = OperationalSupportNotice.objects.create(
            title="Recado editável",
            message="<p>Conteúdo <strong>HTML</strong></p>",
            created_by=capacitacao,
            active=True,
        )

        self.client.force_authenticate(capacitacao)
        listed = self.client.get("/api/v1/operational-support/notices/?scope=manage")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["count"], 1)

        patched = self.client.patch(
            f"/api/v1/operational-support/notices/{notice.id}/",
            {"active": False, "title": "Recado atualizado"},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.data)
        self.assertFalse(patched.data["active"])
        self.assertEqual(patched.data["status"], "inactive")
        self.assertEqual(patched.data["title"], "Recado atualizado")
        self.assertIsNotNone(patched.data["inactive_at"])

        notice.refresh_from_db()
        self.assertFalse(notice.active)
        self.assertIsNotNone(notice.inactive_at)

        public_after_patch = self.client.get("/api/v1/operational-support/notices/")
        self.assertEqual(public_after_patch.status_code, 200)
        self.assertEqual(public_after_patch.data["count"], 0)

        history_after_patch = self.client.get(
            "/api/v1/operational-support/notices/?scope=manage"
        )
        self.assertEqual(history_after_patch.status_code, 200)
        self.assertEqual(history_after_patch.data["count"], 1)
        self.assertEqual(history_after_patch.data["results"][0]["id"], str(notice.id))
