# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.jira_link import (
    auto_link_jira_issue_to_registro,
    enrich_jira_job_with_auto_link,
)

User = get_user_model()


class JiraAutoLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="agent001",
            password="pass12345",
            email="agent001@test.local",
        )
        self.registro = SuporteClaroRegistro.objects.create(
            protocolo="PPL-001",
            irregularidade="Teste",
            received_at=timezone.now(),
            origem=SuporteClaroRegistro.ORIGEM_TEAMS,
            status=SuporteClaroRegistro.STATUS_ABERTO,
            created_by=self.user,
        )

    def test_auto_link_sets_jira_fields(self):
        result = auto_link_jira_issue_to_registro(
            registro_id=self.registro.id,
            issue_key="pplid-2149",
            user=self.user,
        )
        self.assertTrue(result["linked"])
        self.registro.refresh_from_db()
        self.assertEqual(self.registro.chamado_sistema, "jira")
        self.assertEqual(self.registro.chamado_codigo, "PPLID-2149")

    def test_auto_link_appends_jira_when_service_exists(self):
        self.registro.chamado_sistema = SuporteClaroRegistro.CHAMADO_SERVICE
        self.registro.chamado_codigo = "REQ-1"
        self.registro.save()
        from apps.suporte_claro.services.chamados_externos import save_chamados_externos

        save_chamados_externos(
            self.registro,
            [(SuporteClaroRegistro.CHAMADO_SERVICE, "REQ-1", "")],
            user=self.user,
        )
        result = auto_link_jira_issue_to_registro(
            registro_id=self.registro.id,
            issue_key="PPLID-999",
            user=self.user,
        )
        self.assertTrue(result["linked"])
        self.registro.refresh_from_db()
        self.assertEqual(self.registro.chamados_externos.count(), 2)
        codes = list(self.registro.chamados_externos.values_list("codigo", flat=True))
        self.assertIn("REQ-1", codes)
        self.assertIn("PPLID-999", codes)
        auto_link_jira_issue_to_registro(
            registro_id=self.registro.id,
            issue_key="PPLID-2149",
            user=self.user,
        )
        result = auto_link_jira_issue_to_registro(
            registro_id=self.registro.id,
            issue_key="PPLID-2149",
            user=self.user,
        )
        self.assertTrue(result["already_linked"])

    def test_enrich_job_links_on_success(self):
        job = {
            "running": False,
            "message": "Concluído",
            "kind": "registro",
            "registro_id": self.registro.id,
            "result": {"ok": True, "issue_key": "PPLID-2149"},
        }
        enriched = enrich_jira_job_with_auto_link(job, self.user)
        self.assertTrue(enriched["auto_link"]["linked"])
        self.assertIn("vinculado", enriched["message"].lower())
        self.registro.refresh_from_db()
        self.assertEqual(self.registro.chamado_codigo, "PPLID-2149")

    def test_enrich_job_batch_links_multiple(self):
        registro_b = SuporteClaroRegistro.objects.create(
            protocolo="PPL-002",
            irregularidade="Teste B",
            received_at=timezone.now(),
            origem=SuporteClaroRegistro.ORIGEM_TEAMS,
            status=SuporteClaroRegistro.STATUS_ABERTO,
            created_by=self.user,
        )
        job = {
            "running": False,
            "message": "Lote concluído",
            "kind": "batch",
            "result": {
                "ok": True,
                "batch": True,
                "items": [
                    {"ok": True, "registro_id": self.registro.id, "issue_key": "PPLID-100"},
                    {"ok": True, "registro_id": registro_b.id, "issue_key": "PPLID-101"},
                    {"ok": False, "registro_id": 999, "error": "falhou"},
                ],
            },
        }
        enriched = enrich_jira_job_with_auto_link(job, self.user)
        self.assertEqual(len(enriched.get("auto_links") or []), 2)
        self.registro.refresh_from_db()
        registro_b.refresh_from_db()
        self.assertEqual(self.registro.chamado_codigo, "PPLID-100")
        self.assertEqual(registro_b.chamado_codigo, "PPLID-101")
