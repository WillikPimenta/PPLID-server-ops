from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.dimensoes_processos.models import (
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DimCliente,
    DimEtapa,
    DimProduto,
    DimWorkflow,
    MetaEtapa,
)
from apps.dimensoes_processos.services.capacity_volume_planejamento import (
    apply_planejamento_volume_guard,
)
from apps.monitoramento_sla.models import SlaUtilConsolidado


class PlanejamentoVolumeGuardTests(TestCase):
    def setUp(self):
        self.on_date = date(2026, 8, 20)
        self.client = DimCliente.objects.create(id_cliente=813, nome="BANCO AGIBANK S A")
        self.produto = DimProduto.objects.create(id_produto=99, tipo_produto="Documentoscopia")
        self.workflow = DimWorkflow.objects.create(
            id_workflow=813,
            nome="Agibank - Documentoscopia Especializada",
            produto=self.produto,
        )
        self.stage = DimEtapa.objects.create(id_etapa=813, nome="Análise Visual")
        scan = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 6, 1),
            period_to=date(2026, 8, 31),
            finished_at=timezone.now(),
        )
        self.import_run = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 6, 1),
            period_to=date(2026, 8, 31),
            finished_at=timezone.now(),
            reviewed_scan=scan,
        )
        DerivacaoEtapaDiaria.objects.create(
            data=date(2026, 8, 10),
            cliente=self.client,
            workflow=self.workflow,
            etapa=self.stage,
            percentual=Decimal("100"),
            import_run=self.import_run,
        )
        MetaEtapa.objects.create(
            data_inicio=date(2026, 8, 1),
            etapa=self.stage,
            meta_dia=Decimal("20"),
        )
        for offset in (4, 8, 12, 16, 18):
            SlaUtilConsolidado.objects.create(
                data_cadastro=self.on_date - timedelta(days=offset),
                id_cliente=self.client.pk,
                id_workflow=self.workflow.pk,
                quantidade=2,
                date_key_cadastro=int((self.on_date - timedelta(days=offset)).strftime("%Y%m%d")),
            )

    def test_caps_projection_when_far_above_received_median(self):
        key = (self.client.pk, self.workflow.pk)
        totals = {key: Decimal("29545")}
        meta = {
            key: {
                "id_cliente": self.client.pk,
                "cliente_nome": self.client.nome,
                "id_workflow": self.workflow.pk,
                "workflow_nome": self.workflow.nome,
            }
        }
        adjusted_totals, adjustments = apply_planejamento_volume_guard(totals, meta, self.on_date)
        self.assertEqual(len(adjustments), 1)
        self.assertEqual(adjusted_totals[key], Decimal("2"))
        self.assertEqual(adjustments[0]["reason"], "projecao_sla_acima_mediana_recebido")

    def test_keeps_projection_when_aligned_with_received(self):
        key = (self.client.pk, self.workflow.pk)
        totals = {key: Decimal("15")}
        meta = {key: {"cliente_nome": self.client.nome, "workflow_nome": self.workflow.nome}}
        adjusted_totals, adjustments = apply_planejamento_volume_guard(totals, meta, self.on_date)
        self.assertEqual(adjustments, [])
        self.assertEqual(adjusted_totals[key], Decimal("15"))
