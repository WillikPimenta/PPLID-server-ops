from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.controle_sla.models import SlaBreach, SlaBreachHistorico
from apps.dimensoes_processos.models import (
    DimCliente,
    DimNivelHierarquico,
    DimWorkflow,
    ProjecaoSla,
)

TZ = ZoneInfo("America/Sao_Paulo")


class ControleSlaHistoricoApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="sla.hist", password="x")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        now = timezone.now()
        SlaBreachHistorico.objects.create(
            cod_cliente=10,
            nom_cliente="Cliente Critico",
            nom_workflow="WF Critico",
            id_workflow=100,
            nom_fluxo="Etapa A",
            qtd_fila=2,
            dat_registro_antigo=now - timedelta(days=2),
            idade_segundos=200000,
            sla_limite_segundos=3600,
            excedente_segundos=196400,
            pct_sla=5555.0,
            criticidade=SlaBreach.CRIT_CRITICO,
            first_detected_at=now - timedelta(days=1),
            last_seen_at=now,
        )
        SlaBreachHistorico.objects.create(
            cod_cliente=20,
            nom_cliente="Cliente Alto",
            nom_workflow="WF Alto",
            id_workflow=200,
            nom_fluxo="Etapa B",
            qtd_fila=1,
            dat_registro_antigo=now - timedelta(hours=5),
            idade_segundos=18000,
            sla_limite_segundos=3600,
            excedente_segundos=14400,
            pct_sla=500.0,
            criticidade=SlaBreach.CRIT_ALTO,
            first_detected_at=now - timedelta(hours=4),
            last_seen_at=now,
        )

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_historico_defaults_to_critico(self, _mock_perm):
        r = self.client.get("/api/v1/controle-sla/historico/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.data["ok"])
        self.assertEqual(r.data["criticidade"], "critico")
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(len(r.data["results"]), 1)
        self.assertEqual(r.data["results"][0]["nom_cliente"], "Cliente Critico")
        summary = r.data["summary"]
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["clientes_distintos"], 1)
        self.assertEqual(summary["workflows_distintos"], 1)
        self.assertIsNotNone(summary["tempo_medio_estouro_segundos"])
        self.assertEqual(summary["by_cliente"][0]["label"], "Cliente Critico")
        self.assertEqual(summary["by_cliente"][0]["total"], 1)
        self.assertEqual(summary["by_workflow"][0]["label"], "WF Critico")
        self.assertEqual(summary["by_workflow"][0]["total"], 1)
        heatmap = summary["by_weekday_hour"]
        self.assertEqual(len(heatmap["days"]), 7)
        self.assertEqual(len(heatmap["hours"]), 24)
        self.assertEqual(len(heatmap["matrix"]), 7)
        self.assertEqual(len(heatmap["matrix"][0]), 24)
        self.assertGreaterEqual(heatmap["max"], 1)
        self.assertEqual(sum(sum(row) for row in heatmap["matrix"]), 1)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_heatmap_uses_oldest_plus_sla_not_detection(self, _mock_perm):
        """Sem janela Projeção SLA: fallback wall-clock (item + SLA)."""
        SlaBreachHistorico.objects.all().delete()
        oldest = timezone.make_aware(datetime(2026, 7, 22, 10, 0, 0), timezone=TZ)
        detected = timezone.make_aware(datetime(2026, 7, 24, 18, 0, 0), timezone=TZ)
        SlaBreachHistorico.objects.create(
            cod_cliente=99,
            nom_cliente="Cliente Matriz",
            nom_workflow="WF Matriz",
            id_workflow=999,
            nom_fluxo="Etapa",
            qtd_fila=1,
            dat_registro_antigo=oldest,
            idade_segundos=100000,
            sla_limite_segundos=2 * 3600,
            excedente_segundos=90000,
            pct_sla=200.0,
            criticidade=SlaBreach.CRIT_CRITICO,
            first_detected_at=detected,
            last_seen_at=detected,
        )

        r = self.client.get("/api/v1/controle-sla/historico/")
        self.assertEqual(r.status_code, 200, r.content)
        heatmap = r.data["summary"]["by_weekday_hour"]
        self.assertEqual(heatmap["matrix"][2][12], 1)
        self.assertEqual(heatmap["matrix"][4][18], 0)

        filtered = self.client.get(
            "/api/v1/controle-sla/historico/",
            {"weekday": 2, "hour": 12},
        )
        self.assertEqual(filtered.status_code, 200, filtered.content)
        self.assertEqual(filtered.data["count"], 1)
        self.assertEqual(filtered.data["weekday"], 2)
        self.assertEqual(filtered.data["hour"], 12)
        self.assertEqual(filtered.data["results"][0]["nom_cliente"], "Cliente Matriz")
        self.assertEqual(filtered.data["summary"]["total"], 1)

        other = self.client.get(
            "/api/v1/controle-sla/historico/",
            {"weekday": 4, "hour": 18},
        )
        self.assertEqual(other.status_code, 200, other.content)
        self.assertEqual(other.data["count"], 0)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_heatmap_counts_only_inside_projecao_sla_window(self, _mock_perm):
        """Item às 00:00 com janela 08–18 e SLA 4h → estouro às 12:00 (não às 04:00)."""
        SlaBreachHistorico.objects.all().delete()
        cliente = DimCliente.objects.create(id_cliente=501, nome="Cliente Janela")
        workflow = DimWorkflow.objects.create(
            id_workflow=501, nome="WF Janela", tipo_atendimento="Manual"
        )
        nh = DimNivelHierarquico.objects.create(id_nh=501, nome="NH501")
        ProjecaoSla.objects.create(
            cliente=cliente,
            workflow=workflow,
            nivel_hierarquico=nh,
            data_inicio=datetime(2026, 1, 1).date(),
            data_fim=None,
            dias_semana="{0..6}",
            hora_inicio=time(8, 0, 0),
            hora_fim=time(18, 0, 0),
            sla_segundos=4 * 3600,
        )
        oldest = timezone.make_aware(datetime(2026, 7, 22, 0, 0, 0), timezone=TZ)
        SlaBreachHistorico.objects.create(
            cod_cliente=501,
            nom_cliente="Cliente Janela",
            nom_workflow="WF Janela",
            id_workflow=501,
            nom_fluxo="Etapa",
            qtd_fila=1,
            dat_registro_antigo=oldest,
            idade_segundos=50000,
            sla_limite_segundos=4 * 3600,
            excedente_segundos=1000,
            pct_sla=120.0,
            criticidade=SlaBreach.CRIT_CRITICO,
            first_detected_at=oldest + timedelta(hours=20),
            last_seen_at=oldest + timedelta(hours=20),
        )

        r = self.client.get("/api/v1/controle-sla/historico/")
        self.assertEqual(r.status_code, 200, r.content)
        heatmap = r.data["summary"]["by_weekday_hour"]
        self.assertEqual(heatmap["matrix"][2][12], 1)
        self.assertEqual(heatmap["matrix"][2][4], 0)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_historico_filter_todos_and_cliente(self, _mock_perm):
        r = self.client.get("/api/v1/controle-sla/historico/", {"criticidade": "todos"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["count"], 2)

        r2 = self.client.get(
            "/api/v1/controle-sla/historico/",
            {"criticidade": "todos", "cliente": "Cliente Alto"},
        )
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(r2.data["count"], 1)
        self.assertEqual(r2.data["results"][0]["nom_cliente"], "Cliente Alto")
        # Indicadores permanecem no conjunto completo (sem filtro de concentração)
        self.assertEqual(r2.data["summary"]["total"], 2)
