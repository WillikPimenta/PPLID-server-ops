# -*- coding: utf-8 -*-
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.suporte_claro.models import SuporteClaroChamadoExterno, SuporteClaroRegistro
from apps.suporte_claro.services.jira_link import get_registro_jira_key, list_jira_externos
from apps.suporte_claro.services.jira_rest import (
    formalizar_texto_em_externos,
    sync_portal_status_to_jira,
)
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


@override_settings(JIRA_BASE_URL="https://jira.test.local")
class FormalizarExternosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="c92928a", password="x")
        self.agent = Agent.objects.create(
            full_name="Tester",
            user_lan_id="c92928a",
            jira_api_token="pat-token",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.registro = SuporteClaroRegistro.objects.create(
            titulo="Demanda EXT",
            protocolo="EXT-1",
            received_at=timezone.now(),
            origem="email",
            status=SuporteClaroRegistro.STATUS_ABERTO,
            created_by=self.user,
            avaliacao="Retorno ok",
        )

    def _add_externo_jira(self, codigo="OPS-1"):
        return SuporteClaroChamadoExterno.objects.create(
            registro=self.registro,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
            codigo=codigo,
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
        )

    def _add_interno_jira(self, codigo="PPLID-1"):
        return SuporteClaroChamadoExterno.objects.create(
            registro=self.registro,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
            codigo=codigo,
            natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO,
        )

    def test_get_registro_jira_key_ignores_externo(self):
        self._add_externo_jira("EXT-99")
        self.assertIsNone(get_registro_jira_key(self.registro))
        self._add_interno_jira("PPLID-10")
        self.assertEqual(get_registro_jira_key(self.registro), "PPLID-10")

    def test_sync_status_external_only_skips_without_transition(self):
        self._add_externo_jira("EXT-1")
        with patch("apps.suporte_claro.services.jira_rest._apply_portal_transition") as mock_tr:
            result = sync_portal_status_to_jira(
                self.registro, SuporteClaroRegistro.STATUS_EM_ATENDIMENTO, self.user
            )
            mock_tr.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "external_only")

    @patch("apps.suporte_claro.services.jira_rest.add_issue_comment")
    def test_formalizar_selective_posts_only_selected(self, mock_comment):
        a = self._add_externo_jira("EXT-A")
        b = self._add_externo_jira("EXT-B")
        mock_comment.return_value = {"ok": True, "comment_id": "1", "error": None}
        result = formalizar_texto_em_externos(
            self.registro,
            self.user,
            chamado_ids=[a.id],
            texto="Nota só no A",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 1)
        mock_comment.assert_called_once()
        self.assertEqual(mock_comment.call_args[0][0], "EXT-A")
        self.assertEqual(list_jira_externos(self.registro)[0].id, a.id)
        self.assertEqual(list_jira_externos(self.registro)[1].id, b.id)

    def test_formalizar_servicenow_fails_item(self):
        sn = SuporteClaroChamadoExterno.objects.create(
            registro=self.registro,
            sistema=SuporteClaroRegistro.CHAMADO_SERVICE,
            codigo="REQ-1",
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
        )
        result = formalizar_texto_em_externos(
            self.registro,
            self.user,
            chamado_ids=[sn.id],
            texto="x",
        )
        self.assertFalse(result["ok"])
        self.assertIn("ServiceNow", result["items"][0]["error"])

    @patch("apps.suporte_claro.services.jira_rest.add_issue_comment")
    def test_status_patch_requires_texto_when_formalizar(self, mock_comment):
        ext = self._add_externo_jira("EXT-Z")
        response = self.client.patch(
            f"/api/v1/suporte-claro/registros/{self.registro.id}/status/",
            {
                "status": "em_atendimento",
                "formalizar_externos": {
                    "enabled": True,
                    "chamado_ids": [ext.id],
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        mock_comment.assert_not_called()

    @patch("apps.suporte_claro.views.sync_registro_to_jira")
    @patch("apps.suporte_claro.services.jira_rest.add_issue_comment")
    def test_status_patch_formaliza_with_texto(self, mock_comment, mock_sync):
        mock_sync.return_value = {"ok": True, "skipped": True, "reason": "external_only"}
        mock_comment.return_value = {"ok": True, "comment_id": "9", "error": None}
        ext = self._add_externo_jira("EXT-Z")
        response = self.client.patch(
            f"/api/v1/suporte-claro/registros/{self.registro.id}/status/",
            {
                "status": "em_atendimento",
                "formalizar_externos": {
                    "enabled": True,
                    "chamado_ids": [ext.id],
                    "texto": "Andamento no externo",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("formalizacao_externos", data)
        self.assertTrue(data["formalizacao_externos"]["ok"])
        mock_comment.assert_called_once()


@override_settings(
    JIRA_BASE_URL="https://jira.test.local",
    SUPORTE_CLARO_COMENTARIOS_ETAPA=True,
)
class FormalizarComentarioEtapaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent001", password="x")
        self.agent = Agent.objects.create(
            full_name="Tester",
            user_lan_id="agent001",
            jira_api_token="pat",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.registro = SuporteClaroRegistro.objects.create(
            titulo="Coment",
            protocolo="COM-1",
            received_at=timezone.now(),
            origem="email",
            status=SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
            created_by=self.user,
        )
        self.ext = SuporteClaroChamadoExterno.objects.create(
            registro=self.registro,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
            codigo="EXT-C",
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
        )
        self.url = f"/api/v1/suporte-claro/registros/{self.registro.id}/comentarios-etapa/"

    @patch("apps.suporte_claro.services.jira_rest.add_issue_comment")
    def test_comentario_formaliza(self, mock_comment):
        mock_comment.return_value = {"ok": True, "comment_id": "2", "error": None}
        response = self.client.post(
            self.url,
            {
                "texto": "Passo intermediário",
                "visibilidade": "externo",
                "formalizar_externos": {
                    "enabled": True,
                    "chamado_ids": [self.ext.id],
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["formalizacao_externos"]["ok"])
        comentario = response.json()["comentario"]
        self.assertTrue(comentario["formalizado_externo"])
        self.assertEqual(comentario["visibilidade"], "externo")
        self.assertIn("EXT-C", comentario["formalizado_issue_keys"])
        self.assertEqual(
            comentario["formalizado_jira_refs"],
            [{"issue_key": "EXT-C", "comment_id": "2"}],
        )
        mock_comment.assert_called_once_with("EXT-C", "Passo intermediário", user=self.user)

    @patch("apps.suporte_claro.services.jira_rest.delete_issue_comment")
    @patch("apps.suporte_claro.services.jira_rest.add_issue_comment")
    def test_delete_comentario_also_deletes_jira(self, mock_add, mock_delete):
        mock_add.return_value = {"ok": True, "comment_id": "99", "error": None}
        mock_delete.return_value = {
            "ok": True,
            "error": None,
            "issue_key": "EXT-C",
            "comment_id": "99",
        }
        created = self.client.post(
            self.url,
            {
                "texto": "Para apagar",
                "visibilidade": "externo",
                "formalizar_externos": {
                    "enabled": True,
                    "chamado_ids": [self.ext.id],
                },
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        cid = created.json()["comentario"]["id"]
        response = self.client.delete(f"{self.url}{cid}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["jira_removal"]["ok"])
        mock_delete.assert_called_once_with("EXT-C", "99", user=self.user)

    def test_comentario_externo_requires_formalizar(self):
        response = self.client.post(
            self.url,
            {"texto": "Sem seleção", "visibilidade": "externo"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_comentario_interno_default_no_jira(self):
        with patch("apps.suporte_claro.services.jira_rest.add_issue_comment") as mock_comment:
            response = self.client.post(
                self.url,
                {"texto": "Só portal"},
                format="json",
            )
            self.assertEqual(response.status_code, 201)
            data = response.json()["comentario"]
            self.assertEqual(data["visibilidade"], "interno")
            self.assertFalse(data["formalizado_externo"])
            mock_comment.assert_not_called()
