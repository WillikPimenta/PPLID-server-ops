# -*- coding: utf-8 -*-
"""Paridade snapshot/banco → DataFrames esperados pelo planejamento D-1."""
from __future__ import annotations

from datetime import date

import pandas as pd
from django.test import TestCase

from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.services.config_dto import RunOptions
from apps.replicacao_d1.services.config_snapshot import build_execution_snapshot
from apps.replicacao_d1.services.planning_adapter import (
    COL_CLIENTE,
    COL_ESCALA_BR,
    COL_ESCALA_CASE,
    COL_ESCALA_DATA,
    COL_FILA,
    COL_META_CLIENTE,
    COL_WORKFLOW,
    COL_WORKFLOW_D1,
    COL_WORKFLOW_SEL,
    snapshot_to_dataframes,
)


class PlanningAdapterParityTests(TestCase):
    def _seed(self):
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.fonte_banco_ativa = True
        geral.calculadora_params = {
            "dias_uteis": 22,
            "meta_produ": 280,
            "confianca": 0.99,
            "margin_high": 0.0115,
            "margin_low": 0.0178,
        }
        geral.save(update_fields=["fonte_banco_ativa", "calculadora_params"])

        cli = ReplicacaoD1Cliente.objects.create(
            nome="Cliente Parity",
            segmento_nome="Seg P",
            categoria_nome="Cat P",
            meta_mensal=1200,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Tela",
            nome_d1="WF Parquet",
            nome_selenium="WF Selenium",
            fila="G auditoria",
            cliente=cli,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Pendente",
            nome_d1="WF Unknown",
            status=ReplicacaoD1Workflow.STATUS_PENDENTE,
            ativo=False,
        )
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 6),
            auditores_brflow=12,
            auditores_case=4,
        )

    def test_snapshot_dataframes_expected_columns(self):
        self._seed()
        snap = build_execution_snapshot(
            run_id="parity_run",
            run_options=RunOptions(run_id="parity_run"),
            persist=False,
        )
        frames = snapshot_to_dataframes(snap)

        mapa = frames["mapa_workflow_d1"]
        self.assertFalse(mapa.empty)
        for col in (COL_WORKFLOW, COL_CLIENTE, COL_WORKFLOW_D1, COL_WORKFLOW_SEL, COL_FILA, "_wf_key"):
            self.assertIn(col, mapa.columns)
        self.assertEqual(len(mapa), 1)
        self.assertEqual(str(mapa.iloc[0][COL_WORKFLOW_D1]), "WF Parquet")

        cat = frames["categoria_clientes"]
        self.assertIn(COL_META_CLIENTE, cat.columns)
        self.assertEqual(int(cat.iloc[0][COL_META_CLIENTE]), 1200)

        escala = frames["escala_auditores"]
        self.assertIn(COL_ESCALA_DATA, escala.columns)
        self.assertIn(COL_ESCALA_BR, escala.columns)
        self.assertIn(COL_ESCALA_CASE, escala.columns)
        self.assertEqual(int(escala.iloc[0][COL_ESCALA_BR]), 12)

    def test_pending_workflows_excluded_from_mapa(self):
        self._seed()
        snap = build_execution_snapshot(persist=False)
        mapa = snapshot_to_dataframes(snap)["mapa_workflow_d1"]
        nomes = set(mapa[COL_WORKFLOW].astype(str).tolist())
        self.assertIn("WF Tela", nomes)
        self.assertNotIn("WF Pendente", nomes)

    def test_mapa_merge_keys_compatible_with_planning(self):
        """Garante chaves usadas no merge parquet ↔ mapa."""
        from apps.replicacao_d1.normalization import normalize_key

        self._seed()
        snap = build_execution_snapshot(persist=False)
        mapa = snapshot_to_dataframes(snap)["mapa_workflow_d1"].copy()
        mapa["_wf_d1_key"] = mapa[COL_WORKFLOW_D1].map(normalize_key)

        vol = pd.DataFrame({"_wf_d1_key": [normalize_key("WF Parquet")], "Total": [100]})
        merged = vol.merge(mapa, on="_wf_d1_key", how="inner")
        self.assertEqual(len(merged), 1)
