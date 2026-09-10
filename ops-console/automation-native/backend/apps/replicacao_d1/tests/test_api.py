# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.access.models import PortalRoleDefinition
from apps.replicacao_d1.models import ReplicacaoD1Run, ReplicacaoD1WorkflowDia


User = get_user_model()


class ReplicacaoD1ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="rep_d1_user", password="x")
        # Concede VIEW de automação se o projeto usar role definitions em testes.
        try:
            role, _ = PortalRoleDefinition.objects.get_or_create(
                code="test_automacao_view",
                defaults={"name": "Test Automacao View", "permissions": [R.PLANEJAMENTO_AUTOMACAO_VIEW]},
            )
            if hasattr(self.user, "portal_roles"):
                self.user.portal_roles.add(role)
        except Exception:
            pass
        self.client.force_authenticate(user=self.user)
        ReplicacaoD1Run.objects.create(
            run_id="20260717_093000",
            data_referencia_d1="2026-07-16",
            protocolos_total=2,
            workflows_total=2,
            workflows_salvo_ok=1,
        )

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_list_runs(self, _mock_perm):
        resp = self.client.get("/api/v1/replicacao-d1/runs/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.data["count"], 1)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_run_detail_exposes_operational_summary_and_workflow_contract(self, _mock_perm):
        run = ReplicacaoD1Run.objects.get(run_id="20260717_093000")
        ReplicacaoD1WorkflowDia.objects.create(
            run=run,
            data_referencia_d1=run.data_referencia_d1,
            workflow_config="WF QTD",
            protocolos_planejados=10,
            status_brflow="SEM_ALTERACAO",
            resultado="sem_alteracao",
            severidade="aviso",
            motivo_codigo="QUANTIDADE_JA_CONFIGURADA",
            motivo_resumo="Quantidade já estava configurada",
            fase_execucao="validacao_pos_save",
            quantidade_alvo=10,
            quantidade_encontrada=10,
            status_operacional="recebido",
        )

        detail = self.client.get(f"/api/v1/replicacao-d1/runs/{run.run_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["resumo_operacional"]["sem_alteracao"], 1)
        self.assertEqual(detail.data["resumo_operacional"]["salvo_ok"], 1)

        workflows = self.client.get("/api/v1/replicacao-d1/workflows/", {"run_id": run.run_id})
        row = workflows.data["results"][0]
        self.assertEqual(row["resultado"], "sem_alteracao")
        self.assertEqual(row["motivo_codigo"], "QUANTIDADE_JA_CONFIGURADA")
        self.assertEqual(row["quantidade_encontrada"], 10)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    @patch("apps.replicacao_d1.views.api.get_source_file")
    @patch("apps.replicacao_d1.views.api.enqueue_bot_db_sync")
    def test_sync_enqueues_202(self, mock_enqueue, mock_source, _mock_perm):
        from apps.common.models import BotDbSyncJob

        mock_source.return_value = MagicMock(path="/tmp/x.xlsx", run_id="20260717_093000")
        job = MagicMock()
        job.pk = 42
        job.status = BotDbSyncJob.STATUS_PENDING
        mock_enqueue.return_value = (job, True)
        resp = self.client.post(
            "/api/v1/replicacao-d1/sync/",
            {"run_id": "20260717_093000", "force": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(resp.data["queued"])
        self.assertEqual(resp.data["job_id"], 42)
