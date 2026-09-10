# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.access.models import PortalRoleDefinition
from apps.replicacao_d1.models import (
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1Protocolo,
    ReplicacaoD1Run,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.normalization import STATUS_REPLICADO, STATUS_FALHOU, STATUS_PENDENTE
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db
from apps.replicacao_d1.services.sync_replicados import sync_replicados_to_db
from apps.produtividade.models import SOURCE_BRFLOW, ProductivityRecord
from apps.rotina_bruto.models import RotinaGAuditoriaRecord, RotinaProdBrutoRecord
from apps.workforce.models import Agent


User = get_user_model()


def _write_fixture_excel(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df_resumo = pd.DataFrame(
        [
            {
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                "Workflow BRFlow": "G Auditoria Origem",
                "Canal Destino": "BRFlow",
                "Cliente": "CLARO",
                "Segmento": "SEG",
                "Categoria": "CAT",
                "Fila": "G auditoria",
                "Amostra Diaria": 10,
                "Amostra Solicitada": 10,
                "Amostra Efetiva": 2,
                "Protocolos Salvos": 2,
                "Pct Atingido": 100.0,
                "Status": "OK",
                "Status BRFlow": "SALVO_OK",
                "Data Hora Upload BRFlow": "17/07/2026 12:05",
                "Disponivel D1": 50,
                "Faixa Horaria": "08-12",
                "RunId": "20260717_093000",
                "Data Referencia D1": "16/07/2026",
                "Data Execucao": "17/07/2026 12:00",
                "Parquet Referencia": "brflow-detalhado-tratado_20260716.parquet",
                "Auditores Ativos": 12,
            },
        ]
    )
    df_plano = pd.DataFrame(
        [
            {
                "Protocolo": "P001",
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                "Data Analise": "16/07/2026 10:00",
                "Hora": 10,
                "Canal Destino": "BRFlow",
                "Status BRFlow": "SALVO_OK",
                "RunId": "20260717_093000",
                "Data Referencia D1": "16/07/2026",
            },
            {
                "Protocolo": "P002",
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                "Data Analise": "16/07/2026 11:00",
                "Hora": 11,
                "Canal Destino": "BRFlow",
                "Status BRFlow": "ERRO",
                "RunId": "20260717_093000",
                "Data Referencia D1": "16/07/2026",
            },
        ]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_resumo.to_excel(writer, sheet_name="Resumo", index=False)
        df_plano.to_excel(writer, sheet_name="Plano", index=False)
        pd.DataFrame([{"Metrica": "Total", "Valor": 2}]).to_excel(
            writer, sheet_name="Dashboard", index=False
        )
    return path


def _write_replicados_csv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Cliente Destino;Data de Cadastro Destino;Protocolo Destino;Workflow Destino;"
        "Protocolo Origem;Cliente Origem;Workflow Origem;Nível Hierárquico Origem;"
        "Data de Cadastro Origem;Tipo de Conclusão de Análise Origem\n"
        "GAQ;17/07/2026 10:00;D001;G Auditoria Destino;P001;CLARO;WF Origem;1;"
        "16/07/2026 09:00;OK\n",
        encoding="utf-8-sig",
    )
    return path


def _write_multi_destino_replicados_csv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Cliente Destino;Data de Cadastro Destino;Protocolo Destino;Workflow Destino;"
        "Protocolo Origem;Cliente Origem;Workflow Origem;Nível Hierárquico Origem;"
        "Data de Cadastro Origem;Tipo de Conclusão de Análise Origem\n"
        "GAQ;18/07/2026 10:00;D001;G Auditoria - G Auditoria;P001;CLARO;WF G;1;"
        "16/07/2026 09:00;OK\n"
        "GAQ;18/07/2026 10:05;D002;Auditoria Redoc - Auditoria Redoc;P002;CLARO;WF Redoc;1;"
        "16/07/2026 09:05;OK\n"
        "GAQ;18/07/2026 10:10;D003;Auditoria Biometria - Auditoria Biometria;P003;CLARO;WF Bio;1;"
        "16/07/2026 09:10;OK\n"
        "GAQ;19/07/2026 10:00;D004;Analise Direcionada;P004;CLARO;WF Case;1;"
        "17/07/2026 09:00;OK\n",
        encoding="utf-8-sig",
    )
    return path


@override_settings(REPLICACAO_D1_DASHBOARD_CACHE_TTL=0)
class DashboardApiTests(TestCase):
    def setUp(self):
        from django.conf import settings

        self.client = APIClient()
        self.user = User.objects.create_user(username="dash_d1_user", password="x")
        try:
            role, _ = PortalRoleDefinition.objects.get_or_create(
                code="test_automacao_view_dash",
                defaults={"name": "Test Automacao View Dash", "permissions": [R.PLANEJAMENTO_AUTOMACAO_VIEW]},
            )
            if hasattr(self.user, "portal_roles"):
                self.user.portal_roles.add(role)
        except Exception:
            pass
        self.client.force_authenticate(user=self.user)

        self.dir = Path(settings.BASE_DIR) / "tmp" / "replicacao_d1_dashboard_test"
        self.excel = _write_fixture_excel(
            self.dir / "replicacao_aud_d1_relatorio_20260717_093000.xlsx"
        )
        self.csv = _write_replicados_csv(self.dir / "brflow-replicadosd1-tratado_20260718.csv")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_kpis_match_protocol_list(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)

        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )
        self.assertEqual(resp.status_code, 200)
        indicators = resp.data["indicators"]
        protocolos = resp.data["protocolos"]
        self.assertEqual(indicators["planejados"], 2)
        self.assertEqual(indicators["replicados"], 1)
        self.assertEqual(indicators["falhos"], 1)
        self.assertEqual(protocolos["count"], 2)
        self.assertEqual(len(protocolos["results"]), 2)
        self.assertIn("ingestion_health", resp.data)
        self.assertIn("as_of", resp.data["meta"])

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_reuses_cached_payload_for_same_filter(self, _mock_perm):
        cache.clear()
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)
        params = {"data_de": "2026-07-17", "data_ate": "2026-07-17"}

        first = self.client.get("/api/v1/replicacao-d1/dashboard/", params)
        second = self.client.get("/api/v1/replicacao-d1/dashboard/", params)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first["X-Dashboard-Cache"], "miss")
        self.assertEqual(second["X-Dashboard-Cache"], "hit")
        self.assertIn('desc="cache-hit"', second["Server-Timing"])
        self.assertEqual(first.data, second.data)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_filter_by_status(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)

        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17", "status": STATUS_REPLICADO},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["indicators"]["planejados"], 1)
        self.assertEqual(resp.data["indicators"]["replicados"], 1)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_kpi_drilldown_pendentes(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)

        full = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )
        filtered = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17", "status": STATUS_PENDENTE},
        )
        self.assertEqual(full.status_code, 200)
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(full.data["indicators"]["pendentes"], 0)
        self.assertEqual(filtered.data["indicators"]["planejados"], 0)
        self.assertEqual(filtered.data["protocolos"]["count"], 0)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_protocolo_detail(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)

        resp = self.client.get("/api/v1/replicacao-d1/runs/20260717_093000/protocolos/P001/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["protocolo"]["status_operacional"], STATUS_REPLICADO)
        self.assertIsNotNone(resp.data["replicado"])

    def test_dashboard_denied_without_permission(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/replicacao-d1/dashboard/")
        self.assertIn(resp.status_code, (401, 403))

    @override_settings(REPLICACAO_D1_DASHBOARD_MAX_PERIOD_DAYS=31)
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_rejects_period_longer_than_configured_limit(self, _mock_perm):
        query = {"data_de": "2026-01-01", "data_ate": "2026-02-01"}

        response = self.client.get("/api/v1/replicacao-d1/dashboard/", query)
        exported = self.client.get("/api/v1/replicacao-d1/dashboard/export.csv", query)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(exported.status_code, 400)
        self.assertIn("no máximo 31 dias", str(response.data["detail"]))

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_drilldown_by_status(self, _mock_perm):
        from apps.replicacao_d1.models import ReplicacaoD1Replicado

        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)
        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 7, 18),
            protocolo_origem="ORFAO99",
            protocolo_origem_normalizado="orfa099",
            workflow_origem="WF X",
        )

        base = {"data_de": "2026-07-17", "data_ate": "2026-07-17"}
        cases = [
            (STATUS_REPLICADO, 1),
            (STATUS_FALHOU, 1),
            (STATUS_PENDENTE, 0),
        ]
        for status, expected_count in cases:
            resp = self.client.get(
                "/api/v1/replicacao-d1/dashboard/",
                {**base, "status": status},
            )
            self.assertEqual(resp.status_code, 200, msg=status)
            self.assertEqual(resp.data["indicators"]["planejados"], expected_count, msg=status)
            self.assertEqual(resp.data["protocolos"]["count"], expected_count, msg=status)

        full = self.client.get("/api/v1/replicacao-d1/dashboard/", base)
        self.assertEqual(full.status_code, 200)
        self.assertGreaterEqual(full.data["indicators"]["divergentes"], 1)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_build_latency_under_2s(self, _mock_perm):
        import time

        from apps.replicacao_d1.services.dashboard import build_replicacao_d1_dashboard, parse_dashboard_params

        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)
        params = parse_dashboard_params({"data_de": "2026-07-17", "data_ate": "2026-07-17"})
        t0 = time.perf_counter()
        build_replicacao_d1_dashboard(params)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 2.0, f"dashboard build took {elapsed:.2f}s")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_series_matches_kpi_totals(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)

        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )
        self.assertEqual(resp.status_code, 200)
        indicators = resp.data["indicators"]
        series = resp.data["time_series"]
        self.assertTrue(series)
        sum_planejados = sum(row["planejados"] for row in series)
        sum_replicados = sum(row["replicados"] for row in series)
        sum_falhos = sum(row["falhos"] for row in series)
        sum_pendentes = sum(row["pendentes"] for row in series)
        self.assertEqual(sum_planejados, indicators["planejados"])
        self.assertEqual(sum_replicados, indicators["replicados"])
        self.assertEqual(sum_falhos, indicators["falhos"])
        self.assertEqual(sum_pendentes, indicators["pendentes"])

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_breakdown_by_workflow_destino(self, _mock_perm):
        from apps.replicacao_d1.models import ReplicacaoD1Replicado

        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        multi_csv = _write_multi_destino_replicados_csv(
            self.dir / "brflow-replicadosd1-tratado_20260718.csv"
        )
        sync_replicados_to_db(path=str(multi_csv), force=True)
        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 7, 19),
            protocolo_origem="P005",
            protocolo_origem_normalizado="p005",
            workflow_origem="WF G",
            workflow_destino="G Auditoria - G Auditoria",
        )

        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-18"},
        )
        self.assertEqual(resp.status_code, 200)

        indicators = resp.data["indicators"]
        summary = resp.data["replication_summary"]
        por_destino = summary["por_destino"]
        self.assertEqual(indicators["replicados"], 5)
        self.assertEqual(sum(row["total"] for row in por_destino), indicators["replicados"])

        destino_map = {row["key"]: row["total"] for row in por_destino}
        self.assertEqual(destino_map.get("g_auditoria"), 2)
        self.assertEqual(destino_map.get("redoc"), 1)
        self.assertEqual(destino_map.get("bio"), 1)
        self.assertEqual(destino_map.get("doc_31"), 1)

        breakdown = resp.data["breakdown"]["by_workflow_destino"]
        self.assertEqual(sum(row["total"] for row in breakdown), indicators["replicados"])

        day_17 = next(row for row in resp.data["daily_series"] if row["data_execucao"] == "2026-07-17")
        day_18 = next(row for row in resp.data["daily_series"] if row["data_execucao"] == "2026-07-18")
        self.assertEqual(sum(row["total"] for row in day_17["por_destino"]), day_17["total_replicados"])
        self.assertEqual(sum(row["total"] for row in day_18["por_destino"]), day_18["total_replicados"])
        self.assertEqual(day_17["total_replicados"], 4)
        self.assertEqual(day_18["total_replicados"], 1)

        cal_17 = next(row for row in resp.data["calendario"] if row["data"] == "2026-07-17")
        self.assertEqual(cal_17["por_destino"], day_17["por_destino"])

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_filter_canal_by_workflow_destino(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        multi_csv = _write_multi_destino_replicados_csv(
            self.dir / "brflow-replicadosd1-tratado_20260718.csv"
        )
        sync_replicados_to_db(path=str(multi_csv), force=True)
        from apps.replicacao_d1.models import ReplicacaoD1Replicado

        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 7, 19),
            protocolo_origem="P005",
            protocolo_origem_normalizado="p005",
            workflow_origem="WF G",
            workflow_destino="G Auditoria",
        )
        base = {"data_de": "2026-07-17", "data_ate": "2026-07-18"}

        resp_g = self.client.get("/api/v1/replicacao-d1/dashboard/", {**base, "canal": "BRFlow"})
        resp_bio = self.client.get("/api/v1/replicacao-d1/dashboard/", {**base, "canal": "BRFlow Bio"})
        resp_redoc = self.client.get("/api/v1/replicacao-d1/dashboard/", {**base, "canal": "BRFlow Redoc"})
        resp_case = self.client.get("/api/v1/replicacao-d1/dashboard/", {**base, "canal": "Case Manager"})

        self.assertEqual(resp_g.status_code, 200)
        self.assertEqual(resp_g.data["indicators"]["replicados"], 2)
        self.assertEqual(resp_bio.data["indicators"]["replicados"], 1)
        self.assertEqual(resp_redoc.data["indicators"]["replicados"], 1)
        self.assertEqual(resp_case.data["indicators"]["replicados"], 1)
        self.assertEqual(
            resp_bio.data["replication_summary"]["por_destino"][0]["label"],
            "Auditoria Biometria",
        )

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_projection_destino_breakdown_matches_official_csv(self, _mock_perm):
        from apps.replicacao_d1.models import ReplicacaoD1Replicado
        from apps.replicacao_d1.services.dashboard import (
            WorkflowProjectionParams,
            build_projection_destino_breakdown,
        )

        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        multi_csv = _write_multi_destino_replicados_csv(
            self.dir / "brflow-replicadosd1-tratado_20260718.csv"
        )
        sync_replicados_to_db(path=str(multi_csv), force=True)
        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 7, 19),
            protocolo_origem="P005",
            protocolo_origem_normalizado="p005",
            workflow_origem="WF G",
            workflow_destino="G Auditoria",
        )

        with patch("apps.replicacao_d1.services.dashboard.timezone.localdate", return_value=date(2026, 7, 18)):
            breakdown = build_projection_destino_breakdown(WorkflowProjectionParams())
            bio = build_projection_destino_breakdown(WorkflowProjectionParams(canal="Bio"))

        self.assertEqual(breakdown["total_replicados"], 5)
        self.assertEqual(sum(row["total"] for row in breakdown["por_destino"]), 5)
        labels = {row["label"] for row in breakdown["por_destino"]}
        self.assertIn("G Auditoria", labels)
        self.assertIn("Auditoria Biometria", labels)
        self.assertIn("Auditoria Redoc", labels)
        self.assertIn("Analise Direcionada", labels)
        self.assertEqual(bio["total_replicados"], 1)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_tmdl_replication_contract_uses_execution_d_plus_one(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.meta_produ_diaria = 300
        geral.save(update_fields=["meta_produ_diaria"])
        ReplicacaoD1EscalaDia.objects.update_or_create(
            data=date(2026, 7, 17), defaults={"auditores_brflow": 2, "auditores_case": 0}
        )

        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["meta"]["contract_version"], 6)
        self.assertEqual(resp.data["meta"]["official_source"], "replicacao_d1_replicado")
        self.assertEqual(resp.data["meta"]["date_axis"], "execucao")
        self.assertEqual(resp.data["meta"]["confirmation_pairing"], "D+1_calendar")
        summary = resp.data["replication_summary"]
        self.assertEqual(summary["total_replicados"], 1)
        self.assertEqual(summary["escolhidos_bot"], 2)
        self.assertEqual(summary["escolhidos_replicados"], 1)
        self.assertEqual(summary["escolhidos_nao_replicados"], 1)
        self.assertEqual(summary["replicados_sem_escolha"], 0)
        self.assertEqual(summary["taxa_replicacao_bot"], 50.0)
        self.assertEqual(summary["meta_periodo"], 600)
        self.assertEqual(resp.data["daily_series"][0]["data_execucao"], "2026-07-17")
        self.assertEqual(resp.data["daily_series"][0]["data_confirmacao"], "2026-07-18")
        self.assertEqual(resp.data["calendario"][0]["replicados"], 1)
        self.assertEqual(resp.data["replicados_oficiais"]["count"], 1)
        self.assertEqual(
            resp.data["replicados_oficiais"]["results"][0]["protocolo_origem"],
            "P001",
        )
        self.assertEqual(len(resp.data["projection"]["series"]), 15)
        self.assertEqual(len(resp.data["excecoes_clientes"]["replicacao_parcial"]), 1)
        self.assertEqual(resp.data["replication_mix"]["nao_classificado"], 1)
        self.assertEqual(resp.data["workflow_performance"][0]["label"], "WF G")
        self.assertEqual(resp.data["workflow_performance"][0]["total_replicados"], 1)

        filtered = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17", "workflow": "WF G"},
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.data["replication_summary"]["total_replicados"], 1)

    @patch("apps.replicacao_d1.services.dashboard.timezone.localdate", return_value=date(2026, 7, 17))
    def test_dashboard_defaults_to_last_seven_days(self, _mock_today):
        from apps.replicacao_d1.services.dashboard import parse_dashboard_params

        params = parse_dashboard_params({})
        self.assertEqual(params.data_de, date(2026, 7, 11))
        self.assertEqual(params.data_ate, date(2026, 7, 17))

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_dashboard_export_contains_full_filtered_result(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        response = self.client.get(
            "/api/v1/replicacao-d1/dashboard/export.csv",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("replicacao-d1-20260717-20260717.csv", response["Content-Disposition"])
        content = response.content.decode("utf-8-sig")
        self.assertIn("Protocolo origem", content)
        self.assertNotIn("P001", content)
        self.assertNotIn("P002", content)

        sync_replicados_to_db(path=str(self.csv), force=True)
        filtered = self.client.get(
            "/api/v1/replicacao-d1/dashboard/export.csv",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17", "status": STATUS_REPLICADO},
        )
        filtered_content = filtered.content.decode("utf-8-sig")
        self.assertIn("P001", filtered_content)
        self.assertNotIn("P002", filtered_content)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_official_replication_does_not_require_bot_plan(self, _mock_perm):
        from apps.replicacao_d1.models import ReplicacaoD1Replicado

        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 7, 18),
            protocolo_origem="OFICIAL-1",
            protocolo_origem_normalizado="oficial1",
            cliente_origem="CLIENTE OFICIAL",
            workflow_origem="WF OFICIAL",
        )

        response = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["indicators"]["replicados"], 1)
        self.assertEqual(response.data["indicators"]["planejados"], 0)
        self.assertEqual(response.data["replicados_oficiais"]["count"], 1)
        self.assertEqual(response.data["calendario"][0]["replicados"], 1)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_calendar_aggregates_g_auditoria_from_rotina_only(self, _mock_perm):
        RotinaProdBrutoRecord.objects.bulk_create(
            [
                RotinaProdBrutoRecord(
                    report_date=date(2026, 7, 17),
                    dat_analise=date(2026, 7, 17),
                    des_matricula=f"rotina-{index}",
                    nom_etapa="Análise Visual - G Auditoria",
                )
                for index in range(17)
            ]
            + [
                RotinaProdBrutoRecord(
                    report_date=date(2026, 7, 17),
                    dat_analise=date(2026, 7, 17),
                    des_matricula="outra-etapa",
                    nom_etapa="Outra etapa BRFlow",
                ),
                RotinaProdBrutoRecord(
                    report_date=date(2026, 7, 16),
                    dat_analise=date(2026, 7, 16),
                    des_matricula="outro-dia",
                    nom_etapa="Análise Visual - G Auditoria",
                ),
            ]
        )
        # Produção H/H não deve participar do indicador da Replicação D-1.
        ProductivityRecord.objects.create(
            matricula_norm="hh-ignorado",
            etapa="Análise Visual - G Auditoria",
            analysis_seconds=100,
            analysis_count=500,
            recorded_at=timezone.make_aware(datetime(2026, 7, 17, 10)),
            source=SOURCE_BRFLOW,
        )

        response = self.client.get(
            "/api/v1/replicacao-d1/dashboard/",
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["calendario"][0]["produtividade"], 17)
        self.assertEqual(response.data["indicators"]["produtividade_gaq"], 17)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_projection_applies_recent_rate_to_next_fifteen_days(self, _mock_perm):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.meta_produ_diaria = 300
        geral.save(update_fields=["meta_produ_diaria"])
        ReplicacaoD1EscalaDia.objects.update_or_create(
            data=date(2026, 7, 17), defaults={"auditores_brflow": 2, "auditores_case": 0}
        )
        ReplicacaoD1EscalaDia.objects.update_or_create(
            data=date(2026, 7, 18), defaults={"auditores_brflow": 3, "auditores_case": 0}
        )

        with patch("apps.replicacao_d1.services.dashboard.timezone.localdate", return_value=date(2026, 7, 17)):
            response = self.client.get(
                "/api/v1/replicacao-d1/dashboard/",
                {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
            )

        projection = response.data["projection"]
        self.assertEqual(len(projection["series"]), 15)
        self.assertFalse(projection["fallback_120_usado"])
        self.assertEqual(projection["premissa_percentual_meta"], 0.2)
        self.assertEqual(projection["series"][0]["meta"], 900)
        self.assertEqual(projection["series"][0]["projetado"], 2)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_workflow_projection_reconciles_daily_totals_and_exports(self, _mock_perm):
        from apps.replicacao_d1.models import ReplicacaoD1Replicado

        for index, workflow in enumerate(("WF A", "WF A", "WF B"), start=1):
            ReplicacaoD1Replicado.objects.create(
                report_date=date(2026, 7, 18),
                protocolo_origem=f"PROJ-{index}",
                protocolo_origem_normalizado=f"proj{index}",
                cliente_origem="CLIENTE PROJECAO",
                workflow_origem=workflow,
            )
        ReplicacaoD1EscalaDia.objects.update_or_create(
            data=date(2026, 7, 18), defaults={"auditores_brflow": 1}
        )

        with patch("apps.replicacao_d1.services.dashboard.timezone.localdate", return_value=date(2026, 7, 17)):
            response = self.client.get("/api/v1/replicacao-d1/dashboard/projection/")
            exported = self.client.get("/api/v1/replicacao-d1/dashboard/projection/export.csv")
            exported_pdf = self.client.get(
                "/api/v1/replicacao-d1/dashboard/projection/export.pdf",
                {"workflow": "WF A"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["meta"]["history_days"], 7)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["summary"]["historical_series"]), 15)
        self.assertEqual(response.data["summary"]["global_actual_15"], 3)
        self.assertEqual(
            response.data["summary"]["global_actual_automatic_15"]
            + response.data["summary"]["global_actual_manual_15"]
            + response.data["summary"]["global_actual_unclassified_15"],
            response.data["summary"]["global_actual_15"],
        )
        self.assertEqual(len(response.data["results"][0]["historical_series"]), 15)
        for day_index, global_day in enumerate(response.data["summary"]["global_series"]):
            allocated = sum(row["series"][day_index]["projected"] for row in response.data["results"])
            self.assertEqual(allocated, global_day["projetado"])
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("WF A", exported.content.decode("utf-8-sig"))
        self.assertEqual(exported_pdf.status_code, 200)
        self.assertEqual(exported_pdf["Content-Type"], "application/pdf")
        self.assertTrue(exported_pdf.content.startswith(b"%PDF-"))
        self.assertIn("analise-executiva-projecao-d1", exported_pdf["Content-Disposition"])

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_workflow_projection_separates_capacity_by_channel_scale(self, _mock_perm):
        from apps.replicacao_d1.models import (
            ReplicacaoD1Cliente,
            ReplicacaoD1Replicado,
            ReplicacaoD1Workflow,
        )

        general = ReplicacaoD1ConfigGeral.get_solo()
        general.meta_produ_diaria = 10
        general.meta_produ_diaria_case = 5
        general.meta_produ_diaria_bio = 5
        general.meta_produ_diaria_redoc = 2
        general.replicacao_destino_brflow_ativo = False
        general.replicacao_destino_case31_ativo = False
        general.replicacao_destino_bio_ativo = False
        general.replicacao_destino_redoc_ativo = False
        general.save(
            update_fields=[
                "meta_produ_diaria",
                "meta_produ_diaria_case",
                "meta_produ_diaria_bio",
                "meta_produ_diaria_redoc",
                "replicacao_destino_brflow_ativo",
                "replicacao_destino_case31_ativo",
                "replicacao_destino_bio_ativo",
                "replicacao_destino_redoc_ativo",
            ]
        )
        client = ReplicacaoD1Cliente.objects.create(nome="Cliente canais", meta_mensal=1000)
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Auditoria",
            cliente=client,
            fila="G auditoria",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Case",
            cliente=client,
            fila="3.1",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Bio",
            cliente=client,
            fila="Biometria",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Redoc",
            cliente=client,
            fila="Redoc",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 7, 17),
            auditores_brflow=1,
            auditores_case=2,
            auditores_bio=2,
            auditores_redoc=5,
        )
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 7, 18),
            auditores_brflow=2,
            auditores_case=4,
            auditores_bio=4,
            auditores_redoc=10,
        )
        records = []
        for workflow in ("WF Auditoria", "WF Case", "WF Bio", "WF Redoc"):
            for index in range(10):
                protocol = f"{workflow}-{index}"
                records.append(
                    ReplicacaoD1Replicado(
                        report_date=date(2026, 7, 18),
                        protocolo_origem=protocol,
                        protocolo_origem_normalizado=protocol.casefold().replace(" ", ""),
                        cliente_origem=client.nome,
                        workflow_origem=workflow,
                    )
                )
        ReplicacaoD1Replicado.objects.bulk_create(records)

        with patch("apps.replicacao_d1.services.dashboard.timezone.localdate", return_value=date(2026, 7, 17)):
            response = self.client.get("/api/v1/replicacao-d1/dashboard/projection/")
            case_response = self.client.get(
                "/api/v1/replicacao-d1/dashboard/projection/",
                {"canal": "Case"},
            )
            exported_pdf = self.client.get(
                "/api/v1/replicacao-d1/dashboard/projection/export.pdf"
            )

        self.assertEqual(response.status_code, 200)
        channels = {item["canal"]: item for item in response.data["summary"]["channel_series"]}
        self.assertEqual(channels["G Auditoria"]["capacity_15"], 20)
        self.assertEqual(channels["Case"]["capacity_15"], 20)
        self.assertEqual(channels["Bio"]["capacity_15"], 20)
        self.assertEqual(channels["Redoc"]["capacity_15"], 20)
        self.assertEqual(channels["G Auditoria"]["projected_15"], 20)
        self.assertEqual(channels["Case"]["projected_15"], 20)
        self.assertEqual(channels["Bio"]["projected_15"], 20)
        self.assertEqual(channels["Redoc"]["projected_15"], 20)
        self.assertEqual(response.data["summary"]["global_series"][0]["meta"], 80)
        self.assertEqual(response.data["summary"]["global_series"][0]["projetado"], 80)
        self.assertEqual(response.data["meta"]["scale_days_covered"], 1)
        self.assertEqual(len(response.data["meta"]["missing_scale_dates"]), 14)
        self.assertEqual(case_response.data["count"], 1)
        self.assertEqual(case_response.data["summary"]["capacity_15"], 20)
        self.assertEqual(case_response.data["summary"]["global_projected_15"], 20)
        self.assertEqual(exported_pdf.status_code, 200)
        self.assertTrue(exported_pdf.content.startswith(b"%PDF-"))


def _g_auditoria_record(**updates):
    payload = {
        "report_date": date(2026, 8, 10),
        "source_file": "brflow-gauditoria_tratado_20260810.parquet",
        "source_key": "a" * 64,
        "source_record_key": "origem-1",
        "origin_transaction_id": "tx-1",
        "origin_transaction_code": "code-1",
        "protocolo_origem": "000123",
        "protocolo_normalizado": "000123",
        "protocolo_destino": "aud-1",
        "cliente_origem": "Cliente A",
        "workflow_origem": "Workflow A",
        "workflow_destino": "G Auditoria",
        "matricula_agente": "c100a",
        "matricula_auditor": "c200a",
        "etapa": "Análise Visual",
        "etapa_normalizada": "analise visual",
        "data_analise": date(2026, 8, 9),
        "data_auditoria": date(2026, 8, 10),
        "resultado_origem": "OK",
        "resultado_destino": "Risco",
        "status_destino": "Concluído",
        "tipo_conclusao_destino": "Manual",
        "dias_prazo": 1,
        "prazo_status": RotinaGAuditoriaRecord.PRAZO_DENTRO,
        "content_hash": "b" * 64,
        "is_active": True,
    }
    payload.update(updates)
    return RotinaGAuditoriaRecord.objects.create(**payload)


@override_settings(REPLICACAO_D1_DASHBOARD_CACHE_TTL=0)
class DashboardAgentesApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="dash_d1_agentes", password="x")
        try:
            role, _ = PortalRoleDefinition.objects.get_or_create(
                code="test_automacao_view_agentes",
                defaults={"name": "Test Automacao View Agentes", "permissions": [R.PLANEJAMENTO_AUTOMACAO_VIEW]},
            )
            if hasattr(self.user, "portal_roles"):
                self.user.portal_roles.add(role)
        except Exception:
            pass
        self.client.force_authenticate(user=self.user)

        Agent.objects.create(full_name="Agente Um", user_lan_id="c100a")
        Agent.objects.create(full_name="Agente Dois", user_lan_id="c200a")

        _g_auditoria_record(source_key="1" * 64, matricula_agente="c100a", data_auditoria=date(2026, 8, 10))
        _g_auditoria_record(
            source_key="2" * 64,
            matricula_agente="c100a",
            protocolo_normalizado="000124",
            protocolo_origem="000124",
            data_auditoria=date(2026, 8, 11),
        )
        _g_auditoria_record(
            source_key="3" * 64,
            matricula_agente="c200a",
            data_auditoria=date(2026, 8, 10),
        )
        _g_auditoria_record(
            source_key="4" * 64,
            matricula_agente="c300a",
            data_auditoria=date(2026, 8, 10),
            is_quarantined=True,
        )
        _g_auditoria_record(
            source_key="5" * 64,
            matricula_agente="c400a",
            data_auditoria=None,
            is_active=True,
        )
        _g_auditoria_record(
            source_key="6" * 64,
            matricula_agente="c500a",
            data_auditoria=date(2026, 7, 1),
            is_active=False,
        )
        _g_auditoria_record(
            source_key="7" * 64,
            matricula_agente="c12345a",
            protocolo_normalizado="000125",
            protocolo_origem="000125",
            data_auditoria=date(2026, 8, 10),
        )
        _g_auditoria_record(
            source_key="8" * 64,
            matricula_agente="robot-service",
            protocolo_normalizado="000126",
            protocolo_origem="000126",
            data_auditoria=date(2026, 8, 11),
        )

        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 10),
            auditores_brflow=5,
            auditores_case=2,
        )
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 11),
            auditores_brflow=7,
            auditores_case=2,
        )
        ReplicacaoD1ConfigGeral.objects.create(meta_produ_diaria=100)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_agentes_dashboard_aggregates_by_matricula(self, _mock_perm):
        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/agentes/",
            {"data_de": "2026-08-10", "data_ate": "2026-08-11"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["meta"]["date_axis"], "data_auditoria")
        self.assertEqual(resp.data["meta"]["agent_field"], "matricula_agente")
        self.assertEqual(resp.data["summary"]["total_auditorias"], 5)
        self.assertEqual(resp.data["summary"]["total_protocolos_unicos"], 4)
        self.assertEqual(resp.data["summary"]["agentes_com_volume"], 4)

        results = resp.data["results"]
        self.assertEqual(results[0]["matricula"], "c100a")
        self.assertEqual(results[0]["label"], "Agente Um")
        self.assertEqual(results[0]["auditorias"], 2)
        self.assertEqual(results[0]["protocolos"], 2)
        self.assertEqual(results[0]["dias_ativos"], 2)
        self.assertEqual(results[0]["primeira_auditoria"], "2026-08-10")
        self.assertEqual(results[0]["ultima_auditoria"], "2026-08-11")
        self.assertEqual(
            results[0]["daily_series"],
            [
                {"data": "2026-08-10", "auditorias": 1},
                {"data": "2026-08-11", "auditorias": 1},
            ],
        )
        self.assertEqual(results[0]["matricula_tipo"], "automatico")
        self.assertEqual(results[0]["matricula_tipo_label"], "Automático")
        manual_row = next(row for row in results if row["matricula"] == "c12345a")
        self.assertEqual(manual_row["matricula_tipo"], "manual")

        self.assertEqual(resp.data["capacity"]["auditores_escala_media"], 6.0)
        self.assertEqual(resp.data["capacity"]["meta_produ_diaria"], 100.0)
        self.assertEqual(
            resp.data["daily_series"],
            [
                {"data": "2026-08-10", "auditorias": 3},
                {"data": "2026-08-11", "auditorias": 2},
            ],
        )
        self.assertEqual(len(resp.data["top_chart"]), 4)
        self.assertEqual(len(resp.data["filter_options"]["matricula_tipo"]), 2)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_agentes_dashboard_filters_by_matricula_tipo(self, _mock_perm):
        manual = self.client.get(
            "/api/v1/replicacao-d1/dashboard/agentes/",
            {"data_de": "2026-08-10", "data_ate": "2026-08-11", "matricula_tipo": "manual"},
        )
        automatico = self.client.get(
            "/api/v1/replicacao-d1/dashboard/agentes/",
            {"data_de": "2026-08-10", "data_ate": "2026-08-11", "matricula_tipo": "automatico"},
        )
        self.assertEqual(manual.status_code, 200)
        self.assertEqual(automatico.status_code, 200)
        self.assertEqual(manual.data["summary"]["total_auditorias"], 1)
        self.assertEqual(manual.data["results"][0]["matricula"], "c12345a")
        self.assertEqual(automatico.data["summary"]["total_auditorias"], 4)
        self.assertTrue(all(row["matricula_tipo"] == "automatico" for row in automatico.data["results"]))

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_agentes_manual_filter_matches_displayed_classification(self, _mock_perm):
        for index, matricula in enumerate(
            ("C54321A", "c54321a@br.experian.com.br"),
            start=20,
        ):
            _g_auditoria_record(
                source_key=f"{index:064d}",
                source_record_key=f"origem-{index}",
                matricula_agente=matricula,
                protocolo_normalizado=f"000{index}",
                protocolo_origem=f"000{index}",
                data_auditoria=date(2026, 8, 12),
            )

        params = {"data_de": "2026-08-12", "data_ate": "2026-08-12"}
        unfiltered = self.client.get("/api/v1/replicacao-d1/dashboard/agentes/", params)
        manual = self.client.get(
            "/api/v1/replicacao-d1/dashboard/agentes/",
            {**params, "matricula_tipo": "manual"},
        )

        self.assertEqual(unfiltered.status_code, 200)
        self.assertEqual(manual.status_code, 200)
        displayed_manual = {
            row["matricula"]
            for row in unfiltered.data["results"]
            if row["matricula_tipo"] == "manual"
        }
        filtered_manual = {row["matricula"] for row in manual.data["results"]}
        self.assertEqual(filtered_manual, displayed_manual)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_agentes_dashboard_respects_period_filter(self, _mock_perm):
        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/agentes/",
            {"data_de": "2026-08-11", "data_ate": "2026-08-11"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary"]["total_auditorias"], 2)
        mats = {row["matricula"] for row in resp.data["results"]}
        self.assertEqual(mats, {"c100a", "robot-service"})

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_agentes_export_csv(self, _mock_perm):
        resp = self.client.get(
            "/api/v1/replicacao-d1/dashboard/agentes/export.csv",
            {"data_de": "2026-08-10", "data_ate": "2026-08-11"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn(
            "matricula,nome,padrao_matricula,auditorias,protocolos,share_pct,media_dia,"
            "dias_ativos,primeira_auditoria,ultima_auditoria",
            body,
        )
        self.assertIn("c100a,Agente Um,Automático,2,2", body)
