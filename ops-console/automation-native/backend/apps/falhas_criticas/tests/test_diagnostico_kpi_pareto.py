# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
import pandas as pd

from apps.falhas_criticas.constants import GROUP_GLOBAL
from apps.falhas_criticas.models import FalhasAgent, Failure
from apps.falhas_criticas.services.analytics import build_clientes_workflows
from apps.falhas_criticas.services.aggregators import build_diagnostico_summary

User = get_user_model()


class DiagnosticoKpiParetoTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=GROUP_GLOBAL)
        self.user = User.objects.create_user("diag_kpi", password="test123")
        self.user.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        self.agent = FalhasAgent.objects.create(
            matricula_norm="c90222a",
            name="Diag KPI",
            localidade="Brasília",
        )
        today = timezone.now().date()
        for i, (cli, wf, cen) in enumerate([
            ("Cliente A", "WF1", "CENARIO_A"),
            ("Cliente A", "WF1", "CENARIO_A"),
            ("Cliente B", "WF2", "CENARIO_B"),
            ("Cliente C", "WF1", "CENARIO_A"),
        ]):
            Failure.objects.create(
                protocolo=f"DK-{i}",
                data_analise=today,
                cenario=cen,
                cliente=cli,
                workflow=wf,
                localidade="Brasília",
                agent=self.agent,
            )

    def test_clientes_workflows_distintos(self):
        df = pd.DataFrame({
            'Cliente': ['A', 'A', 'B', ''],
            'Workflow': ['W1', 'W2', 'W1', 'W1'],
            'Módulo': ['M1', 'M1', 'M2', 'M1'],
            'Tipo de Falha': ['X', 'X', 'Y', 'X'],
        })
        out = build_clientes_workflows(df)
        self.assertEqual(out['clientes_distintos'], 3)  # A, B, Sem cliente
        self.assertEqual(out['workflows_distintos'], 2)
        self.assertEqual(out['top_cliente']['nome'], 'A')
        self.assertLessEqual(len(out['top_clientes']), 15)

    def test_diagnostico_summary_kpis_and_pareto(self):
        today = timezone.now().date().isoformat()
        params = {
            'start_date': '2020-01-01',
            'end_date': '2030-12-31',
            'localidade': 'Geral',
            'oficial': 'false',
        }
        # QueryDict-like via dict is fine for load_period_frames through scoped_query_params
        from django.http import QueryDict
        qd = QueryDict(mutable=True)
        for k, v in params.items():
            qd[k] = v
        summary = build_diagnostico_summary(self.user, qd)
        kpis = summary['kpis']
        self.assertEqual(kpis['clientes'], 3)
        self.assertEqual(kpis['workflows'], 2)
        self.assertGreaterEqual(kpis['falhas'], 4)
        self.assertIsNotNone(kpis.get('top_cliente'))
        pareto = summary.get('pareto') or []
        self.assertTrue(len(pareto) >= 1)
        cumul = [p['cumul_pct'] for p in pareto]
        for i in range(1, len(cumul)):
            self.assertGreaterEqual(cumul[i], cumul[i - 1])
        self.assertIsNotNone(summary.get('prioridade'))
        self.assertIn('title', summary['prioridade'])
