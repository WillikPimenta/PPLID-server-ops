# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.access.models import PortalRoleDefinition
from apps.replicacao_d1.models import ReplicacaoD1Run, ReplicacaoD1WorkflowDia
from apps.replicacao_d1.services.runs_operational import (
    build_run_operational_summaries,
    serialize_run_operational,
)


User = get_user_model()


class RunsOperationalServiceTests(TestCase):
    run_id = "20260805_120000"

    def setUp(self):
        self.run = ReplicacaoD1Run.objects.create(
            run_id=self.run_id,
            data_referencia_d1="2026-08-04",
            protocolos_total=4,
            workflows_total=3,
            workflows_salvo_ok=1,
        )

    def test_build_run_operational_summaries(self):
        ReplicacaoD1WorkflowDia.objects.create(
            run=self.run,
            data_referencia_d1="2026-08-04",
            workflow_config="WF A",
            status_brflow="SALVO_OK",
            protocolos_planejados=1,
        )
        ReplicacaoD1WorkflowDia.objects.create(
            run=self.run,
            data_referencia_d1="2026-08-04",
            workflow_config="WF B",
            status_brflow="ERRO",
            protocolos_planejados=1,
        )
        ReplicacaoD1WorkflowDia.objects.create(
            run=self.run,
            data_referencia_d1="2026-08-04",
            workflow_config="WF C",
            status_brflow="PENDENTE",
            protocolos_planejados=1,
        )
        summaries = build_run_operational_summaries([self.run])
        self.assertEqual(summaries[self.run_id]["salvo_ok"], 1)
        self.assertEqual(summaries[self.run_id]["erro"], 1)
        self.assertEqual(summaries[self.run_id]["pendente"], 1)
        self.assertEqual(summaries[self.run_id]["salvo"], 1)
        self.assertEqual(summaries[self.run_id]["falhou"], 1)
        self.assertEqual(summaries[self.run_id]["avisos_total"], 2)
        self.assertEqual(summaries[self.run_id]["total_executavel"], 3)

    def test_ignores_workflows_without_protocols_or_non_actionable_status(self):
        ReplicacaoD1WorkflowDia.objects.create(
            run=self.run,
            data_referencia_d1="2026-08-04",
            workflow_config="WF SEM PROTOCOLO",
            status_brflow="PENDENTE",
            protocolos_planejados=0,
        )
        ReplicacaoD1WorkflowDia.objects.create(
            run=self.run,
            data_referencia_d1="2026-08-04",
            workflow_config="WF PULADO",
            status_brflow="PULADO",
            protocolos_planejados=10,
        )
        summaries = build_run_operational_summaries([self.run])
        self.assertEqual(summaries[self.run_id]["pendente"], 0)

    def test_serialize_run_operational_fallback_workflows_salvo_ok(self):
        payload = serialize_run_operational(self.run)
        self.assertEqual(payload["run_id"], self.run_id)
        self.assertFalse(payload["has_plan"])
        self.assertEqual(payload["validation_status"], ReplicacaoD1Run.VALIDATION_PENDING)
        self.assertEqual(payload["status_canonical"], ReplicacaoD1Run.STATUS_LEGACY)
        self.assertEqual(payload["salvo_ok"], 1)
        self.assertEqual(payload["pendente"], 0)
        self.assertEqual(payload["erro"], 0)


class RunsOperationalApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="rep_d1_ops", password="x")
        try:
            role, _ = PortalRoleDefinition.objects.get_or_create(
                code="test_automacao_view_ops",
                defaults={"name": "Test Automacao View Ops", "permissions": [R.PLANEJAMENTO_AUTOMACAO_VIEW]},
            )
            if hasattr(self.user, "portal_roles"):
                self.user.portal_roles.add(role)
        except Exception:
            pass
        self.client.force_authenticate(user=self.user)
        self.run_id = "20260805_120000"
        self.run = ReplicacaoD1Run.objects.create(
            run_id=self.run_id,
            data_referencia_d1="2026-08-04",
            protocolos_total=2,
            workflows_total=2,
            workflows_salvo_ok=0,
        )
        ReplicacaoD1WorkflowDia.objects.create(
            run=self.run,
            data_referencia_d1="2026-08-04",
            workflow_config="WF A",
            status_brflow="UPLOAD_OK",
            protocolos_planejados=1,
        )
        ReplicacaoD1WorkflowDia.objects.create(
            run=self.run,
            data_referencia_d1="2026-08-04",
            workflow_config="WF B",
            status_brflow="ERRO",
            protocolos_planejados=1,
        )

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_list_runs_operacional(self, _mock_perm):
        resp = self.client.get("/api/v1/replicacao-d1/runs/", {"operacional": "1"})
        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.data["results"] if r["run_id"] == self.run_id)
        self.assertEqual(row["salvo_ok"], 1)
        self.assertEqual(row["erro"], 1)
        self.assertEqual(row["pendente"], 0)
