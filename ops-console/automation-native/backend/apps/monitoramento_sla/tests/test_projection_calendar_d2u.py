from datetime import date, time

from django.test import TestCase

from apps.dimensoes_processos.models import (
    DimCliente,
    DimNivelHierarquico,
    DimWorkflow,
    ProjecaoSla,
)
from apps.monitoramento_sla.services.d2u_wf450 import calcular_vencimento_d2u
from apps.monitoramento_sla.services.projecao_lookup import load_projecao_rows_for_range
from apps.monitoramento_sla.services.projection_calendar import ProjectionCalendar


class ProjectionCalendarD2UTests(TestCase):
    def test_cached_deadline_matches_legacy(self):
        DimCliente.objects.create(id_cliente=2, nome="Cliente")
        DimWorkflow.objects.create(id_workflow=450, nome="WF450")
        DimNivelHierarquico.objects.create(id_nh=2, nome="NH")
        ProjecaoSla.objects.create(
            cliente_id=2,
            workflow_id=450,
            nivel_hierarquico_id=2,
            data_inicio=date(2026, 1, 1),
            dias_semana="{0..4}",
            hora_inicio=time(8),
            hora_fim=time(17),
            duracao_atendimento=32400,
        )
        rows = load_projecao_rows_for_range(
            date_min=date(2026, 5, 1),
            date_max=date(2026, 7, 1),
        )
        calendar = ProjectionCalendar(
            rows,
            date_min=date(2026, 5, 1),
            date_max=date(2026, 7, 1),
        )
        kwargs = {
            "id_cliente": 2,
            "id_workflow": 450,
            "id_nh": 2,
            "data_cadastro": date(2026, 5, 1),
        }
        self.assertEqual(
            calcular_vencimento_d2u(**kwargs, proj_rows=calendar),
            calcular_vencimento_d2u(**kwargs, proj_rows=rows),
        )
        self.assertEqual(
            calcular_vencimento_d2u(**kwargs, proj_rows=calendar),
            calcular_vencimento_d2u(**kwargs, proj_rows=calendar),
        )
