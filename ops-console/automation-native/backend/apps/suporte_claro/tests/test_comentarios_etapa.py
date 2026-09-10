# -*- coding: utf-8 -*-
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.suporte_claro.models import SuporteClaroComentarioEtapa, SuporteClaroRegistro

User = get_user_model()


@override_settings(
    MEDIA_ROOT="/tmp/pplid_suporte_claro_test_media",
    JIRA_BASE_URL="https://jira.test.local",
    SUPORTE_CLARO_COMENTARIOS_ETAPA=True,
)
class ComentariosEtapaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="agent001", password="pass12345", email="agent001@test.local"
        )
        self.other = User.objects.create_user(
            username="agent002", password="pass12345", email="agent002@test.local"
        )
        self.client.force_authenticate(user=self.user)
        self.registro = SuporteClaroRegistro.objects.create(
            titulo="Demanda COMENT",
            protocolo="COMENT-1",
            received_at=timezone.now(),
            origem="email",
            status=SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
            created_by=self.user,
        )
        self.base = f"/api/v1/suporte-claro/registros/{self.registro.id}/comentarios-etapa/"
        self._jira_patcher = patch(
            "apps.suporte_claro.views.create_and_link_jira_for_registro",
            return_value={"ok": True, "issue_key": "TEST-1", "skipped": False},
        )
        self._jira_patcher.start()
        self.addCleanup(self._jira_patcher.stop)

    def test_create_list_delete_comentario(self):
        with patch("apps.suporte_claro.services.jira_rest.add_issue_comment") as mock_comment:
            create = self.client.post(self.base, {"texto": "Aguardando evidência"}, format="json")
            self.assertEqual(create.status_code, 201)
            comentario = create.json()["comentario"]
            self.assertEqual(comentario["texto"], "Aguardando evidência")
            self.assertEqual(comentario["etapa_status"], "em_atendimento")
            self.assertTrue(comentario["can_delete"])
            mock_comment.assert_not_called()

        listed = self.client.get(self.base)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)

        cid = comentario["id"]
        deleted = self.client.delete(f"{self.base}{cid}/")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(SuporteClaroComentarioEtapa.objects.count(), 0)

    def test_flag_off_returns_404(self):
        with override_settings(SUPORTE_CLARO_COMENTARIOS_ETAPA=False):
            response = self.client.get(self.base)
            self.assertEqual(response.status_code, 404)
            create = self.client.post(self.base, {"texto": "x"}, format="json")
            self.assertEqual(create.status_code, 404)

    def test_delete_forbidden_for_other_without_change(self):
        comentario = SuporteClaroComentarioEtapa.objects.create(
            registro=self.registro,
            texto="Meu comentário",
            etapa_status=self.registro.status,
            created_by=self.user,
        )
        self.client.force_authenticate(user=self.other)
        response = self.client.delete(f"{self.base}{comentario.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(SuporteClaroComentarioEtapa.objects.filter(pk=comentario.id).exists())

    def test_meta_exposes_flag(self):
        response = self.client.get("/api/v1/suporte-claro/registros/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["meta"].get("comentarios_etapa_enabled"))
        detail = self.client.get(f"/api/v1/suporte-claro/registros/{self.registro.id}/")
        self.assertTrue(detail.json().get("comentarios_etapa_enabled"))
