# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone

from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.services.projecao import gerar_projecao_mensal
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord


class ProjecaoMensalServiceTests(TestCase):
    def setUp(self):
        self.cliente = ReplicacaoD1Cliente.objects.create(nome="Cliente Projeção", meta_mensal=100)
        self.workflow = ReplicacaoD1Workflow.objects.create(
            nome_canonico="Workflow Projeção",
            cliente=self.cliente,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )

    def _ledger(self, *, run_id: str, day: int, protocolos: int) -> None:
        ReplicacaoD1LedgerConsumo.objects.create(
            competencia="2026-08",
            cliente=self.cliente,
            cliente_nome=self.cliente.nome,
            workflow=self.workflow,
            workflow_nome=self.workflow.nome_canonico,
            workflow_chave=self.workflow.chave_normalizada,
            run_id=run_id,
            data_execucao=timezone.make_aware(datetime(2026, 8, day, 12, 0)),
            protocolos=protocolos,
            origem="confirmado",
        )

    def _volumetria(self, *, volume: int, day: int = 10) -> None:
        RotinaDetalhadoBrutoRecord.objects.bulk_create(
            [
                RotinaDetalhadoBrutoRecord(
                    report_date=date(2026, 8, day),
                    protocolo=1_000_000 + index,
                    cliente=self.cliente.nome,
                    workflow=self.workflow.nome_d1 or self.workflow.nome_canonico,
                )
                for index in range(volume)
            ]
        )

    def test_uses_execution_days_without_exposing_balance_limits(self):
        self._ledger(run_id="run-1", day=3, protocolos=10)
        self._ledger(run_id="run-2", day=4, protocolos=20)
        self._volumetria(volume=100)
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 11),
            auditores_brflow=1,
            auditores_case=0,
        )
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.meta_produ_diaria = 10
        geral.save(update_fields=["meta_produ_diaria", "updated_at"])

        result = gerar_projecao_mensal("2026-08", hoje=date(2026, 8, 10))

        self.assertEqual(result["consumo_total"], 30)
        self.assertNotIn("meta_total", result)
        self.assertNotIn("headroom_total", result)
        self.assertNotIn("pct_meta", result)
        self.assertNotIn("gap_vs_meta", result)
        self.assertEqual(result["dias_com_execucao"], 2)
        self.assertEqual(result["dias_mes"], 31)
        self.assertEqual(result["dias_decorridos"], 10)
        self.assertEqual(result["dias_restantes"], 21)
        self.assertEqual(result["dias_com_escala"], 1)
        self.assertEqual(len(result["dias_sem_escala"]), 20)
        self.assertEqual(result["volumetria_total"], 100)
        self.assertEqual(result["capacidade_total_restante"], 10)
        self.assertEqual(result["capacidade_alocada_restante"], 10)
        self.assertEqual(result["projecao_fim_mes"], 40)
        self.assertEqual(result["workflows"][0]["workflow"], self.workflow.nome_canonico)
        self.assertNotIn("meta_mensal", result["workflows"][0])
        self.assertNotIn("headroom", result["workflows"][0])
        self.assertNotIn("gap_vs_meta", result["workflows"][0])

    def test_capacity_is_capped_by_workflow_volumetry(self):
        self._volumetria(volume=8)
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 11),
            auditores_brflow=5,
            auditores_case=0,
        )
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.meta_produ_diaria = 100
        geral.save(update_fields=["meta_produ_diaria", "updated_at"])

        result = gerar_projecao_mensal("2026-08", hoje=date(2026, 8, 10))

        self.assertEqual(result["capacidade_total_restante"], 500)
        self.assertEqual(result["capacidade_alocada_restante"], 8)
        self.assertEqual(result["ociosidade_capacidade"], 492)
        self.assertEqual(result["projecao_fim_mes"], 8)
        self.assertEqual(result["workflows"][0]["volumetria_d1"], 8)

    def test_distributes_queue_capacity_by_workflow_volumetry(self):
        self.workflow.amostra_100 = True
        self.workflow.save()
        cliente_b = ReplicacaoD1Cliente.objects.create(nome="Cliente Projeção B")
        workflow_b = ReplicacaoD1Workflow.objects.create(
            nome_canonico="Workflow Projeção B",
            cliente=cliente_b,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
            amostra_100=True,
        )
        registros = []
        protocolo = 2_000_000
        for workflow, cliente, volume in (
            (self.workflow, self.cliente, 100),
            (workflow_b, cliente_b, 300),
        ):
            for _ in range(volume):
                registros.append(
                    RotinaDetalhadoBrutoRecord(
                        report_date=date(2026, 8, 10),
                        protocolo=protocolo,
                        cliente=cliente.nome,
                        workflow=workflow.nome_d1 or workflow.nome_canonico,
                    )
                )
                protocolo += 1
        RotinaDetalhadoBrutoRecord.objects.bulk_create(registros)
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 11),
            auditores_brflow=1,
            auditores_case=0,
        )
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.meta_produ_diaria = 40
        geral.save(update_fields=["meta_produ_diaria", "updated_at"])

        result = gerar_projecao_mensal("2026-08", hoje=date(2026, 8, 10))
        por_workflow = {row["workflow_chave"]: row for row in result["workflows"]}

        self.assertEqual(result["capacidade_alocada_restante"], 40)
        self.assertEqual(por_workflow[self.workflow.chave_normalizada]["capacidade_alocada_mes"], 10)
        self.assertEqual(por_workflow[workflow_b.chave_normalizada]["capacidade_alocada_mes"], 30)

    def test_projection_never_regresses_existing_consumption(self):
        self._ledger(run_id="run-over", day=3, protocolos=150)

        result = gerar_projecao_mensal("2026-08", hoje=date(2026, 8, 31))

        self.assertEqual(result["consumo_total"], 150)
        self.assertEqual(result["projecao_fim_mes"], 150)

    def test_balance_limit_does_not_cap_analytical_projection(self):
        self.cliente.meta_mensal = 1
        self.cliente.save(update_fields=["meta_mensal", "updated_at"])
        self.workflow.amostra_100 = True
        self.workflow.save(update_fields=["amostra_100", "updated_at"])
        self._volumetria(volume=100)
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 11),
            auditores_brflow=1,
            auditores_case=0,
        )
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.meta_produ_diaria = 40
        geral.save(update_fields=["meta_produ_diaria", "updated_at"])

        result = gerar_projecao_mensal("2026-08", hoje=date(2026, 8, 10))

        self.assertEqual(result["capacidade_alocada_restante"], 40)
        self.assertEqual(result["projecao_fim_mes"], 40)

    def test_rejects_invalid_competencia(self):
        with self.assertRaisesRegex(ValueError, "Competência inválida"):
            gerar_projecao_mensal("2026-13")
