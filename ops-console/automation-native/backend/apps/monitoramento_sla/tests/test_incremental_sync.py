from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.dimensoes_processos.models import (
    DimCliente,
    DimNivelHierarquico,
    DimWorkflow,
    ProjecaoSla,
)
from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilDetalhe
from apps.monitoramento_sla.services.consolidado import rebuild_consolidado
from apps.monitoramento_sla.services.projecao_lookup import (
    find_projecao_dia,
    load_projecao_rows_for_range,
)
from apps.monitoramento_sla.services.projection_calendar import ProjectionCalendar
from apps.monitoramento_sla.services.sla_util import calcular_sla_util_segundos
from apps.monitoramento_sla.services.sync_incremental import sync_monitoramento_sla
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

TZ = ZoneInfo("America/Sao_Paulo")


class ProjectionCalendarTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=1, nome="Cliente A")
        DimWorkflow.objects.create(id_workflow=10, nome="Workflow A")
        DimNivelHierarquico.objects.create(id_nh=100, nome="NH A")
        ProjecaoSla.objects.create(
            cliente_id=1,
            workflow_id=10,
            nivel_hierarquico_id=100,
            data_inicio=date(2026, 1, 1),
            dias_semana="{0..4}",
            hora_inicio=time(8),
            hora_fim=time(18),
            duracao_atendimento=36000,
            sla_segundos=3600,
        )

    def test_calendar_preserves_legacy_result(self):
        rows = load_projecao_rows_for_range(
            date_min=date(2026, 7, 1),
            date_max=date(2026, 7, 15),
        )
        calendar = ProjectionCalendar(
            rows,
            date_min=date(2026, 7, 1),
            date_max=date(2026, 7, 15),
        )
        kwargs = {
            "id_cliente": 1,
            "id_workflow": 10,
            "id_nh": 100,
            "data_cadastro": date(2026, 7, 1),
            "hora_cadastro": time(9),
            "data_fim": date(2026, 7, 8),
            "hora_fim": time(10),
        }
        legacy = calcular_sla_util_segundos(**kwargs, proj_rows=rows)
        optimized = calcular_sla_util_segundos(**kwargs, proj_rows=calendar)
        self.assertEqual(optimized, legacy)
        self.assertEqual(
            find_projecao_dia(
                id_cliente=1,
                id_workflow=10,
                id_nh=100,
                on_date=date(2026, 7, 4),
                rows=calendar,
            ),
            find_projecao_dia(
                id_cliente=1,
                id_workflow=10,
                id_nh=100,
                on_date=date(2026, 7, 4),
                rows=rows,
            ),
        )


class IncrementalSyncTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=1, nome="Cliente A")
        DimWorkflow.objects.create(id_workflow=10, nome="Workflow A")
        DimNivelHierarquico.objects.create(id_nh=100, nome="NH A")
        ProjecaoSla.objects.create(
            cliente_id=1,
            workflow_id=10,
            nivel_hierarquico_id=100,
            data_inicio=date(2026, 1, 1),
            dias_semana="{0..6}",
            hora_inicio=time(8),
            hora_fim=time(18),
            duracao_atendimento=36000,
            sla_segundos=3600,
        )
        RotinaDetalhadoBrutoRecord.objects.create(
            report_date=date(2026, 7, 3),
            protocolo=1,
            cliente="Cliente A",
            workflow="Workflow A",
            nivel_hierarquico="NH A",
            data_cadastro=date(2026, 7, 1),
            data_conclusao=date(2026, 7, 1),
            resultado="OK",
        )
        RotinaDetalhadoBrutoRecord.objects.create(
            report_date=date(2026, 7, 3),
            protocolo=2,
            cliente="Cliente A",
            workflow="Workflow A",
            nivel_hierarquico="NH A",
            data_cadastro=date(2026, 7, 2),
            data_conclusao=None,
            resultado="",
        )

    def test_second_run_only_recalculates_open_group(self):
        first = sync_monitoramento_sla(
            cutover_at=datetime(2026, 7, 3, 12, tzinfo=TZ)
        )
        self.assertEqual(first.rows_detalhe, 2)
        self.assertTrue(first.metrics["counts"]["full_refresh"])

        second = sync_monitoramento_sla(
            cutover_at=datetime(2026, 7, 3, 13, tzinfo=TZ)
        )
        self.assertEqual(second.rows_detalhe, 1)
        self.assertFalse(second.metrics["counts"]["full_refresh"])
        self.assertEqual(SlaUtilDetalhe.objects.count(), 2)
        self.assertEqual(
            set(SlaUtilConsolidado.objects.values_list("data_cadastro", flat=True)),
            {date(2026, 7, 1), date(2026, 7, 2)},
        )
        self.assertFalse(
            SlaUtilDetalhe.objects.exclude(hora_cadastro__isnull=True).exists()
        )
        self.assertFalse(
            SlaUtilConsolidado.objects.exclude(hora_cadastro__isnull=True).exists()
        )
        self.assertEqual(
            set(
                SlaUtilConsolidado.objects.values_list(
                    "hora_cadastro_fonte", flat=True
                )
            ),
            {SlaUtilConsolidado.HORA_FONTE_INDISPONIVEL},
        )

    def test_changed_closed_row_is_recalculated(self):
        sync_monitoramento_sla(cutover_at=datetime(2026, 7, 3, 12, tzinfo=TZ))
        source = RotinaDetalhadoBrutoRecord.objects.get(protocolo=1)
        source.resultado = "ALTERADO"
        source.save(update_fields=["resultado"])
        run = sync_monitoramento_sla(
            cutover_at=datetime(2026, 7, 3, 13, tzinfo=TZ)
        )
        self.assertEqual(run.rows_detalhe, 2)
        self.assertEqual(
            SlaUtilDetalhe.objects.get(protocolo="1").resultado,
            "ALTERADO",
        )


class IncrementalConsolidatedTests(TestCase):
    def test_preserves_unaffected_dates(self):
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 1, 1),
            quantidade=99,
            date_key_cadastro=20260101,
        )
        rebuild_consolidado(affected_dates=[date(2026, 7, 1)])
        self.assertTrue(
            SlaUtilConsolidado.objects.filter(
                data_cadastro=date(2026, 1, 1),
                quantidade=99,
            ).exists()
        )
