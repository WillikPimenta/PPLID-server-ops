# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.replicacao_d1.exceptions import ConfigIncompletaError
from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1ConfigSnapshot,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1RetroativoConfig,
    ReplicacaoD1RetroativoWorkflow,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.services.config_dto import RunOptions
from apps.replicacao_d1.services.config_snapshot import (
    build_execution_snapshot,
    compute_config_hash,
    load_persistent_config,
    preview_execution_snapshot,
    validate_persistent_config,
)


class ConfigSnapshotTests(TestCase):
    def _seed_minimal_config(self):
        ReplicacaoD1ConfigGeral.get_solo()
        cli = ReplicacaoD1Cliente.objects.create(nome="Cliente A", meta_mensal=500)
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Alpha",
            nome_d1="WF Parquet",
            nome_selenium="WF Selenium",
            fila="G auditoria",
            cliente=cli,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 5),
            auditores_brflow=10,
            auditores_case=8,
        )

    def test_deterministic_hash(self):
        self._seed_minimal_config()
        cfg = load_persistent_config()
        h1 = compute_config_hash({"persistent": cfg.to_dict()})
        h2 = compute_config_hash({"persistent": cfg.to_dict()})
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_read_does_not_lock_config_rows(self):
        self._seed_minimal_config()
        with CaptureQueriesContext(connection) as queries:
            load_persistent_config()
        sql = " ".join(query["sql"].upper() for query in queries.captured_queries)
        self.assertNotIn("FOR UPDATE", sql)

    def test_snapshot_immutability(self):
        self._seed_minimal_config()
        snap = preview_execution_snapshot(run_options=RunOptions(run_id="test_run"))
        original_wf_count = len(snap.persistent.get("workflows", []))
        snap.persistent["workflows"].append({"nome_canonico": "Injected"})
        snap2 = preview_execution_snapshot(run_options=RunOptions(run_id="test_run"))
        self.assertEqual(len(snap2.persistent.get("workflows", [])), original_wf_count)

    def test_incomplete_config_raises(self):
        ReplicacaoD1ConfigGeral.get_solo()
        cfg = load_persistent_config()
        errors = validate_persistent_config(cfg)
        self.assertTrue(any("cliente" in e.lower() for e in errors))
        self.assertTrue(any("workflow" in e.lower() for e in errors))

    def test_build_snapshot_persists_for_run_id(self):
        self._seed_minimal_config()
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.fonte_banco_ativa = True
        geral.save(update_fields=["fonte_banco_ativa"])
        snap = build_execution_snapshot(run_id="20260805_120000", persist=True)
        self.assertTrue(ReplicacaoD1ConfigSnapshot.objects.filter(run_id="20260805_120000").exists())
        self.assertEqual(snap.config_hash, ReplicacaoD1ConfigSnapshot.objects.get(run_id="20260805_120000").config_hash)

    def test_build_snapshot_blocks_when_incomplete(self):
        self._seed_minimal_config()
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.fonte_banco_ativa = True
        geral.save(update_fields=["fonte_banco_ativa"])
        ReplicacaoD1Workflow.objects.all().delete()
        with self.assertRaises(ConfigIncompletaError):
            build_execution_snapshot(run_id="x", persist=False)

    def test_persistent_config_includes_retroativo(self):
        self._seed_minimal_config()
        from apps.replicacao_d1.models import ReplicacaoD1RetroativoConfig, ReplicacaoD1RetroativoWorkflow

        retro = ReplicacaoD1RetroativoConfig.get_solo()
        retro.retroativo_ativo = True
        retro.retroativo_data_inicio = date(2026, 8, 1)
        retro.retroativo_data_fim = date(2026, 8, 7)
        retro.save()
        workflow = ReplicacaoD1Workflow.objects.first()
        ReplicacaoD1RetroativoWorkflow.objects.create(config=retro, workflow=workflow, ativo=True)
        cfg = load_persistent_config()
        self.assertIsNotNone(cfg.retroativo)
        self.assertTrue(cfg.retroativo.retroativo_ativo)
        self.assertEqual(len(cfg.retroativo.workflows), 1)
        payload = cfg.to_dict()
        self.assertIn("retroativo", payload)
        h1 = compute_config_hash({"persistent": payload})
        retro.retroativo_data_fim = date(2026, 8, 8)
        retro.save()
        cfg2 = load_persistent_config()
        h2 = compute_config_hash({"persistent": cfg2.to_dict()})
        self.assertNotEqual(h1, h2)

    def test_fallback_parquet_dias_ausentes_affects_config_hash(self):
        self._seed_minimal_config()
        geral = ReplicacaoD1ConfigGeral.get_solo()
        cfg = load_persistent_config()
        h_off = compute_config_hash({"persistent": cfg.to_dict()})
        geral.fallback_parquet_dias_ausentes = True
        geral.save(update_fields=["fallback_parquet_dias_ausentes", "updated_at"])
        cfg_on = load_persistent_config()
        h_on = compute_config_hash({"persistent": cfg_on.to_dict()})
        self.assertNotEqual(h_off, h_on)
        self.assertTrue(cfg_on.fallback_parquet_dias_ausentes)
