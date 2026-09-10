from __future__ import annotations

import importlib
from datetime import date

from django.apps import apps as django_apps
from django.test import TestCase

from apps.replicacao_d1.models import ReplicacaoD1Run, ReplicacaoD1WorkflowDia
from apps.replicacao_d1.normalization import (
    RESULTADO_NAO_SALVO,
    RESULTADO_PULADO,
    RESULTADO_SEM_ALTERACAO,
    STATUS_FALHOU,
    STATUS_RECEBIDO,
    normalizar_resultado_workflow,
)
from apps.replicacao_d1.services.run_lifecycle import close_run
from apps.replicacao_d1.services.runs_operational import build_run_operational_summaries
from apps.replicacao_d1.services.workflow_operational import build_workflow_operational_defaults
from apps.replicacao_d1.services.excel_reader import ParsedWorkflow


class WorkflowOutcomeNormalizationTests(TestCase):
    def test_migration_commits_backfill_before_creating_indexes(self):
        migration = importlib.import_module(
            "apps.replicacao_d1.migrations.0025_workflow_execution_outcome"
        )

        self.assertFalse(migration.Migration.atomic)

    def test_sem_alteracao_is_success_with_warning(self):
        normalized = normalizar_resultado_workflow("SEM_ALTERACAO")
        self.assertEqual(normalized["resultado"], RESULTADO_SEM_ALTERACAO)
        self.assertEqual(normalized["status_operacional"], STATUS_RECEBIDO)
        self.assertEqual(normalized["severidade"], "aviso")
        self.assertFalse(normalized["falha_bloqueante"])

    def test_pulado_with_blocking_reason_preserves_result_and_becomes_failure(self):
        normalized = normalizar_resultado_workflow("PULADO", "CSV_AUSENTE")
        self.assertEqual(normalized["resultado"], RESULTADO_PULADO)
        self.assertEqual(normalized["status_operacional"], STATUS_FALHOU)
        self.assertEqual(normalized["severidade"], "erro")
        self.assertTrue(normalized["falha_bloqueante"])

        ausente = normalizar_resultado_workflow("PULADO", "WORKFLOW_AUSENTE_BRFLOW")
        self.assertEqual(ausente["resultado"], RESULTADO_PULADO)
        self.assertEqual(ausente["status_operacional"], STATUS_FALHOU)

    def test_nao_salvo_populates_error_fields(self):
        defaults = build_workflow_operational_defaults(
            ParsedWorkflow(workflow_config="WF", status_brflow="NAO_SALVO"),
            motivo_codigo="SALVAMENTO_NAO_CONFIRMADO",
            motivo_resumo="BRFlow não confirmou o salvamento",
            fase_execucao="confirmacao",
            quantidade_alvo=12,
            quantidade_encontrada=10,
        )
        self.assertEqual(defaults["resultado"], RESULTADO_NAO_SALVO)
        self.assertEqual(defaults["erro_codigo"], "SALVAMENTO_NAO_CONFIRMADO")
        self.assertEqual(defaults["erro_resumo"], "BRFlow não confirmou o salvamento")
        self.assertEqual(defaults["quantidade_alvo"], 12)
        self.assertEqual(defaults["quantidade_encontrada"], 10)

    def test_non_blocking_outcome_clears_legacy_error_fields(self):
        defaults = build_workflow_operational_defaults(
            ParsedWorkflow(workflow_config="WF", status_brflow="SEM_ALTERACAO"),
            motivo_codigo="QUANTIDADE_JA_CONFIGURADA",
            motivo_resumo="Quantidade já estava configurada",
            quantidade_alvo=10,
            quantidade_encontrada=10,
        )
        self.assertEqual(defaults["erro_codigo"], "")
        self.assertEqual(defaults["erro_resumo"], "")
        self.assertEqual(defaults["motivo_codigo"], "QUANTIDADE_JA_CONFIGURADA")
        self.assertEqual(defaults["protocolos_enviados"], 0)
        self.assertEqual(defaults["protocolos_aceitos"], 0)


class WorkflowOutcomeLifecycleTests(TestCase):
    def setUp(self):
        self.run = ReplicacaoD1Run.objects.create(
            run_id="outcome_contract",
            data_referencia_d1=date(2026, 8, 24),
            status_canonical=ReplicacaoD1Run.STATUS_RUNNING,
        )

    def _workflow(self, name: str, **kwargs):
        defaults = {
            "run": self.run,
            "data_referencia_d1": self.run.data_referencia_d1,
            "workflow_config": name,
            "protocolos_planejados": 1,
        }
        defaults.update(kwargs)
        return ReplicacaoD1WorkflowDia.objects.create(**defaults)

    def test_sem_alteracao_counts_as_success_and_warning(self):
        self._workflow(
            "WF QTD",
            status_brflow="SEM_ALTERACAO",
            resultado=RESULTADO_SEM_ALTERACAO,
            status_operacional=STATUS_RECEBIDO,
            severidade="aviso",
        )
        closed = close_run(self.run.run_id)
        summary = build_run_operational_summaries([closed])[self.run.run_id]
        self.assertEqual(closed.status_canonical, ReplicacaoD1Run.STATUS_COMPLETED)
        self.assertEqual(summary["sem_alteracao"], 1)
        self.assertEqual(summary["salvo_ok"], 1)
        self.assertEqual(summary["avisos_total"], 1)

    def test_saved_plus_blocking_skip_is_partial(self):
        self._workflow("WF OK", status_brflow="SALVO_OK", resultado="salvo", status_operacional="recebido")
        self._workflow(
            "WF SKIP",
            status_brflow="PULADO",
            resultado=RESULTADO_PULADO,
            status_operacional=STATUS_FALHOU,
            severidade="erro",
            motivo_codigo="CSV_AUSENTE",
        )
        closed = close_run(self.run.run_id)
        summary = build_run_operational_summaries([closed])[self.run.run_id]
        self.assertEqual(closed.status_canonical, ReplicacaoD1Run.STATUS_PARTIAL)
        self.assertEqual(summary["falhou"], 1)
        self.assertEqual(summary["erro"], 1)
        self.assertEqual(summary["avisos_total"], 1)

    def test_only_benign_skip_completes_with_warning(self):
        self._workflow(
            "WF SEM PROTOCOLOS",
            status_brflow="PULADO",
            resultado=RESULTADO_PULADO,
            status_operacional="ignorado",
            severidade="aviso",
            motivo_codigo="SEM_PROTOCOLOS",
        )

        closed = close_run(self.run.run_id)
        summary = build_run_operational_summaries([closed])[self.run.run_id]

        self.assertEqual(closed.status_canonical, ReplicacaoD1Run.STATUS_COMPLETED)
        self.assertEqual(summary["pulado"], 1)
        self.assertEqual(summary["avisos_total"], 1)

    def test_migration_backfill_maps_rows_in_a_single_pass(self):
        saved = self._workflow(
            "WF SAVED",
            status_brflow="salvo_ok",
            resultado="pendente",
            status_operacional="pendente",
        )
        failed = self._workflow(
            "WF FAILED",
            status_brflow="ERRO",
            resultado="pendente",
            status_operacional="pendente",
            erro_resumo="Falha legada",
        )
        quantity = self._workflow(
            "WF QTD BACKFILL",
            modo_replicacao="qtd",
            status_brflow="SEM_ALTERACAO",
            protocolos_planejados=17,
        )

        migration = importlib.import_module(
            "apps.replicacao_d1.migrations.0025_workflow_execution_outcome"
        )
        migration._backfill_outcomes(django_apps, None)

        saved.refresh_from_db()
        failed.refresh_from_db()
        quantity.refresh_from_db()
        self.assertEqual(
            (saved.resultado, saved.severidade, saved.status_operacional),
            ("salvo", "info", "recebido"),
        )
        self.assertEqual(failed.resultado, "falhou")
        self.assertEqual(failed.motivo_codigo, "BRFLOW_ERRO")
        self.assertEqual(failed.motivo_resumo, "Falha legada")
        self.assertEqual(quantity.quantidade_alvo, 17)
        self.assertEqual(quantity.quantidade_encontrada, 17)
