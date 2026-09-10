# -*- coding: utf-8 -*-
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.suporte_claro.models import SuporteClaroChamadoExterno, SuporteClaroRegistro
from apps.suporte_claro.services.chamados_externos import (
    append_jira_chamado_if_missing,
    serialize_chamado_interno,
    serialize_chamados_externos,
)
from apps.suporte_claro.services.serialization import serialize_registro
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


@override_settings(
    MEDIA_ROOT="/tmp/pplid_suporte_claro_natureza_media",
    JIRA_BASE_URL="https://jira.test.local",
)
class ChamadoNaturezaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="c93123a", password="x")
        self.agent = Agent.objects.create(
            full_name="Tester",
            user_lan_id="c93123a",
            jira_api_token="secret-pat",
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client.force_authenticate(user=self.user)
        self.url = "/api/v1/suporte-claro/registros/"
        self.received_at = timezone.now().replace(microsecond=0).isoformat()

    @patch("apps.suporte_claro.services.jira_auto.create_issue")
    def test_auto_create_marks_interno(self, mock_create):
        mock_create.return_value = {
            "ok": True,
            "issue_key": "PPLID-301",
            "error": None,
            "status_code": 201,
        }
        response = self.client.post(
            self.url,
            {
                "titulo": "Interno auto",
                "protocolo": "NAT-1",
                "received_at": self.received_at,
                "origem": "teams",
                "status": "aberto",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["registro"]
        self.assertIsNotNone(data["chamado_interno"])
        self.assertEqual(data["chamado_interno"]["codigo"].upper(), "PPLID-301")
        self.assertEqual(data["chamado_interno"]["natureza"], "interno")
        self.assertEqual(data["chamados_externos"], [])
        row = SuporteClaroChamadoExterno.objects.get(codigo__iexact="PPLID-301")
        self.assertEqual(row.natureza, SuporteClaroChamadoExterno.NATUREZA_INTERNO)

    @patch("apps.suporte_claro.services.jira_auto.create_issue")
    def test_service_manual_is_externo_plus_interno_jira(self, mock_create):
        mock_create.return_value = {
            "ok": True,
            "issue_key": "PPLID-302",
            "error": None,
            "status_code": 201,
        }
        response = self.client.post(
            self.url,
            {
                "titulo": "Com service",
                "protocolo": "NAT-2",
                "received_at": self.received_at,
                "origem": "teams",
                "status": "aberto",
                "chamado_sistema": "service",
                "chamado_codigo": "REQ999",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["registro"]
        self.assertEqual(data["chamado_interno"]["codigo"].upper(), "PPLID-302")
        self.assertEqual(len(data["chamados_externos"]), 1)
        self.assertEqual(data["chamados_externos"][0]["sistema"], "service")
        self.assertEqual(data["chamados_externos"][0]["natureza"], "externo")

    @patch("apps.suporte_claro.services.jira_auto.create_issue")
    def test_manual_jira_externo_plus_interno_auto(self, mock_create):
        """Jira externo no cadastro não impede criação do chamado interno."""
        mock_create.return_value = {
            "ok": True,
            "issue_key": "PPLID-303",
            "error": None,
            "status_code": 201,
        }
        response = self.client.post(
            self.url,
            {
                "titulo": "Jira manual + interno",
                "protocolo": "NAT-3",
                "received_at": self.received_at,
                "origem": "email",
                "status": "aberto",
                "chamado_sistema": "jira",
                "chamado_codigo": "OPS-88",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(mock_create.called)
        data = response.json()["registro"]
        self.assertIsNotNone(data.get("chamado_interno"))
        self.assertEqual(data["chamado_interno"]["codigo"].upper(), "PPLID-303")
        self.assertEqual(data["chamado_interno"]["natureza"], "interno")
        self.assertEqual(len(data["chamados_externos"]), 1)
        self.assertEqual(data["chamados_externos"][0]["codigo"].upper(), "OPS-88")
        self.assertEqual(data["chamados_externos"][0]["natureza"], "externo")
        self.assertEqual(data["chamados_externos"][0]["sistema"], "jira")

    def test_append_preserves_externos(self):
        registro = SuporteClaroRegistro.objects.create(
            titulo="Mix",
            protocolo="NAT-4",
            received_at=timezone.now(),
            origem="teams",
            status="aberto",
            created_by=self.user,
            chamado_sistema="service",
            chamado_codigo="REQ1",
        )
        SuporteClaroChamadoExterno.objects.create(
            registro=registro,
            sistema="service",
            codigo="REQ1",
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
            ordem=0,
        )
        ok = append_jira_chamado_if_missing(registro, "PPLID-400", user=self.user)
        self.assertTrue(ok)
        registro.refresh_from_db()
        self.assertEqual(serialize_chamado_interno(registro)["codigo"].upper(), "PPLID-400")
        externos = serialize_chamados_externos(registro)
        self.assertEqual(len(externos), 1)
        self.assertEqual(externos[0]["sistema"], "service")
        payload = serialize_registro(registro)
        self.assertEqual(payload["chamado_interno"]["natureza"], "interno")
        self.assertEqual(len(payload["chamados_externos"]), 1)
