# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from django.test import TestCase

from apps.replicacao_d1.models import ReplicacaoD1LedgerConsumo
from apps.replicacao_d1.services.ledger import (
    ORIGEM_AJUSTE_MANUAL,
    ORIGEM_CONFIRMADO,
    aplicar_ajuste_manual,
    carregar_consumo_meta_mensal,
    purge_ledger_por_runs,
    recalcular_consumo_competencia,
    registrar_consumo_meta_run,
)


class LedgerServiceTests(TestCase):
    competencia = "2026-08"
    run_id = "20260805_120000"
    wf_key = "wf alpha"

    def test_registrar_consumo_idempotent(self):
        registrar_consumo_meta_run(
            competencia=self.competencia,
            run_id=self.run_id,
            consumo_por_workflow={self.wf_key: 10},
            origem=ORIGEM_CONFIRMADO,
            data_execucao=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        )
        registrar_consumo_meta_run(
            competencia=self.competencia,
            run_id=self.run_id,
            consumo_por_workflow={self.wf_key: 25},
            origem=ORIGEM_CONFIRMADO,
            data_execucao=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            ReplicacaoD1LedgerConsumo.objects.filter(
                run_id=self.run_id,
                competencia=self.competencia,
                workflow_chave=self.wf_key,
                origem=ORIGEM_CONFIRMADO,
            ).count(),
            1,
        )
        self.assertEqual(carregar_consumo_meta_mensal(self.competencia).get(self.wf_key), 25)

    def test_ajuste_manual_preserved_on_recalcular(self):
        registrar_consumo_meta_run(
            competencia=self.competencia,
            run_id=self.run_id,
            consumo_por_workflow={self.wf_key: 10},
            origem=ORIGEM_CONFIRMADO,
        )
        aplicar_ajuste_manual(
            competencia=self.competencia,
            workflow_chave=self.wf_key,
            consumo_acumulado=42,
        )
        recalcular_consumo_competencia(self.competencia)
        manual = ReplicacaoD1LedgerConsumo.objects.get(
            competencia=self.competencia,
            workflow_chave=self.wf_key,
            origem=ORIGEM_AJUSTE_MANUAL,
        )
        self.assertEqual(manual.protocolos, 42)
        consumo = carregar_consumo_meta_mensal(self.competencia)
        self.assertEqual(consumo.get(self.wf_key), 52)

    def test_purge_ledger_por_runs(self):
        registrar_consumo_meta_run(
            competencia=self.competencia,
            run_id=self.run_id,
            consumo_por_workflow={self.wf_key: 10},
            origem=ORIGEM_CONFIRMADO,
        )
        other_run = "20260806_120000"
        registrar_consumo_meta_run(
            competencia=self.competencia,
            run_id=other_run,
            consumo_por_workflow={self.wf_key: 5},
            origem=ORIGEM_CONFIRMADO,
        )
        deleted = purge_ledger_por_runs([self.run_id])
        self.assertEqual(deleted, 1)
        self.assertFalse(
            ReplicacaoD1LedgerConsumo.objects.filter(run_id=self.run_id).exists()
        )
        self.assertTrue(
            ReplicacaoD1LedgerConsumo.objects.filter(run_id=other_run).exists()
        )
