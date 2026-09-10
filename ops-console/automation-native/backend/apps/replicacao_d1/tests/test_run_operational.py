# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.replicacao_d1.models import ReplicacaoD1Protocolo, ReplicacaoD1Run, ReplicacaoD1WorkflowDia
from apps.replicacao_d1.normalization import STATUS_FALHOU, STATUS_RECEBIDO, STATUS_REPLICADO
from apps.replicacao_d1.services.run_lifecycle import close_run, update_run_status
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db
from apps.replicacao_d1.tests.test_sync import _write_fixture_excel


class CloseRunTests(TestCase):
    run_id = "20260717_093000"

    def setUp(self):
        from django.conf import settings

        self.dir = Path(settings.BASE_DIR) / "tmp" / "replicacao_d1_close_run"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = _write_fixture_excel(self.dir / "replicacao_aud_d1_relatorio_20260717_093000.xlsx")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_close_run_aggregates_workflows_and_protocols(self):
        ok, _, _, rid, _ = sync_replicacao_d1_to_db(path=str(self.path), force=True)
        self.assertTrue(ok)
        run = ReplicacaoD1Run.objects.get(run_id=rid)
        self.assertEqual(run.workflows_total, 2)
        self.assertEqual(run.protocolos_total, 2)
        self.assertEqual(run.workflows_salvo_ok, 1)
        self.assertEqual(run.status_canonical, ReplicacaoD1Run.STATUS_PARTIAL)
        self.assertIsNotNone(run.finished_at)

        wf_ok = ReplicacaoD1WorkflowDia.objects.get(run_id=rid, workflow_config="WF G")
        self.assertEqual(wf_ok.status_operacional, STATUS_RECEBIDO)
        self.assertIsNotNone(wf_ok.upload_em)
        self.assertEqual(wf_ok.protocolos_planejados, 1)
        self.assertEqual(wf_ok.protocolos_aceitos, 2)

        wf_err = ReplicacaoD1WorkflowDia.objects.get(run_id=rid, workflow_config="WF Case")
        self.assertEqual(wf_err.status_operacional, STATUS_FALHOU)
        self.assertEqual(wf_err.erro_codigo, "BRFLOW_ERRO")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_status_does_not_regress_silently_from_completed(self):
        ReplicacaoD1Run.objects.create(
            run_id=self.run_id,
            data_referencia_d1=date(2026, 7, 16),
            status_canonical=ReplicacaoD1Run.STATUS_COMPLETED,
            workflows_total=2,
            workflows_salvo_ok=2,
        )
        update_run_status(
            self.run_id,
            ReplicacaoD1Run.STATUS_PLANNED,
            workflows_salvo_ok=1,
            workflows_total=2,
        )
        run = ReplicacaoD1Run.objects.get(run_id=self.run_id)
        self.assertEqual(run.status_canonical, ReplicacaoD1Run.STATUS_COMPLETED)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_update_status_persists_execution_timestamps_and_clears_stale_error(self):
        run = ReplicacaoD1Run.objects.create(
            run_id="database_only_close",
            data_referencia_d1=date(2026, 7, 16),
            status_canonical=ReplicacaoD1Run.STATUS_FAILED,
            erro_codigo="STALE",
            erro_resumo="Erro antigo",
        )
        started_at = datetime(2026, 7, 17, 9, 0, 0)
        finished_at = datetime(2026, 7, 17, 9, 5, 0)

        update_run_status(
            run.run_id,
            ReplicacaoD1Run.STATUS_COMPLETED,
            workflows_salvo_ok=1,
            workflows_total=1,
            started_at=started_at,
            finished_at=finished_at,
            allow_regress=True,
        )

        run.refresh_from_db()
        self.assertEqual(run.status_canonical, ReplicacaoD1Run.STATUS_COMPLETED)
        self.assertTrue(timezone.is_aware(run.started_at))
        self.assertTrue(timezone.is_aware(run.finished_at))
        self.assertEqual(run.duration_seconds, 300)
        self.assertEqual(run.erro_codigo, "")
        self.assertEqual(run.erro_resumo, "")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_partial_error_remains_after_resync(self):
        ok, _, _, rid, _ = sync_replicacao_d1_to_db(path=str(self.path), force=True)
        self.assertTrue(ok)
        wf_err = ReplicacaoD1WorkflowDia.objects.get(run_id=rid, workflow_config="WF Case")
        self.assertEqual(wf_err.status_operacional, STATUS_FALHOU)

        ok2, _, _, _, _ = sync_replicacao_d1_to_db(path=str(self.path), force=True)
        self.assertTrue(ok2)
        wf_err = ReplicacaoD1WorkflowDia.objects.get(run_id=rid, workflow_config="WF Case")
        self.assertEqual(wf_err.status_operacional, STATUS_FALHOU)
        self.assertEqual(wf_err.erro_codigo, "BRFLOW_ERRO")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_timestamps_are_timezone_aware(self):
        ok, _, _, rid, _ = sync_replicacao_d1_to_db(path=str(self.path), force=True)
        self.assertTrue(ok)
        run = ReplicacaoD1Run.objects.get(run_id=rid)
        wf = ReplicacaoD1WorkflowDia.objects.get(run_id=rid, workflow_config="WF G")
        self.assertTrue(timezone.is_aware(run.finished_at))
        self.assertTrue(timezone.is_aware(wf.upload_em))

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_close_run_totals_match_children(self):
        run = ReplicacaoD1Run.objects.create(
            run_id="manual_close",
            data_referencia_d1=date(2026, 7, 16),
            status_canonical=ReplicacaoD1Run.STATUS_RUNNING,
            started_at=timezone.now(),
        )
        ReplicacaoD1WorkflowDia.objects.create(
            run=run,
            data_referencia_d1=date(2026, 7, 16),
            workflow_config="WF X",
            status_brflow="SALVO_OK",
            status_operacional=STATUS_RECEBIDO,
            protocolos_planejados=3,
            protocolos_aceitos=3,
        )
        ReplicacaoD1Protocolo.objects.create(
            run=run,
            data_referencia_d1=date(2026, 7, 16),
            protocolo="PX1",
            workflow_config="WF X",
            status_operacional=STATUS_REPLICADO,
        )
        closed = close_run("manual_close", data_execucao=timezone.now())
        self.assertEqual(closed.workflows_total, 1)
        self.assertEqual(closed.workflows_salvo_ok, 1)
        self.assertEqual(closed.protocolos_total, 1)
        self.assertEqual(closed.status_canonical, ReplicacaoD1Run.STATUS_COMPLETED)
