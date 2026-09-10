# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.replicacao_d1.config_models import ReplicacaoD1ConfigSnapshot
from apps.replicacao_d1.models import ReplicacaoD1Run
from apps.replicacao_d1.services.run_lifecycle import ensure_planned_run, update_run_status


class RunLifecycleTests(TestCase):
    def test_ensure_planned_run_creates_with_snapshot(self):
        snap = ReplicacaoD1ConfigSnapshot.objects.create(
            run_id="20260810_120000",
            config_version=3,
            config_hash="abc123",
            snapshot_json={"persistent": {}},
        )
        run = ensure_planned_run(
            "20260810_120000",
            data_referencia_d1=date(2026, 8, 9),
            config_snapshot=snap,
            config_version=3,
            config_hash="abc123",
            parquet_referencia="brflow-detalhado-tratado_20260809.parquet",
        )
        self.assertEqual(run.status_canonical, ReplicacaoD1Run.STATUS_PLANNED)
        self.assertEqual(run.config_snapshot_id, snap.pk)
        self.assertEqual(run.parquet_referencia, "brflow-detalhado-tratado_20260809.parquet")

    def test_update_run_status(self):
        ReplicacaoD1Run.objects.create(
            run_id="r1",
            data_referencia_d1=date(2026, 8, 9),
            status_canonical=ReplicacaoD1Run.STATUS_PLANNED,
        )
        update_run_status(
            "r1",
            ReplicacaoD1Run.STATUS_PARTIAL,
            workflows_salvo_ok=2,
            workflows_total=3,
        )
        run = ReplicacaoD1Run.objects.get(run_id="r1")
        self.assertEqual(run.status_canonical, ReplicacaoD1Run.STATUS_PARTIAL)
        self.assertEqual(run.workflows_salvo_ok, 2)
