# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.falhas_criticas.constants import GROUP_BRASILIA, GROUP_GLOBAL, GROUP_SAO_CARLOS
from apps.falhas_criticas.models import SyncAuditLog

User = get_user_model()


class FalhasAPITests(TestCase):
    def setUp(self):
        for name in (GROUP_GLOBAL, GROUP_BRASILIA, role_group_name(ROLE_PLAN_ANALISTA)):
            Group.objects.get_or_create(name=name)
        self.global_user = User.objects.create_user("global_user", password="test123")
        self.global_user.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        self.global_user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = Client()

    def test_api_requires_auth(self):
        response = self.client.get("/api/v1/falhas/me/")
        self.assertEqual(response.status_code, 403)

    def test_me_endpoint(self):
        self.client.force_login(self.global_user)
        response = self.client.get("/api/v1/falhas/me/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["username"], "global_user")
        self.assertEqual(data["scope"], "global")
        self.assertTrue(data["can_sync"])

    @override_settings(PPLID_FRONTEND_URL="http://localhost:5173")
    def test_portal_redirects_to_pplid_login(self):
        response = self.client.get("/falhas/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("redirect=/falhas/", response.url)
        self.assertIn("localhost:5173", response.url)

    @override_settings(PPLID_FRONTEND_URL="http://localhost:5173")
    def test_portal_redirects_to_vue_when_authenticated(self):
        self.client.force_login(self.global_user)
        response = self.client.get("/falhas/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "http://localhost:5173/secao/indicadores/falhas")

    def test_health_is_public(self):
        response = self.client.get(reverse("falhas-health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_alerts_returns_200(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/alerts/?start_date=2026-06-01&end_date=2026-06-25&oficial=true",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("alerts", data)
        self.assertIsInstance(data["alerts"], list)

    def test_reincidence_returns_reincidente_field(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/reincidence/?start_date=2026-06-01&end_date=2026-06-25&oficial=true",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("results", data)
        if data["results"]:
            self.assertIn("reincidente", data["results"][0])

    def test_reincidence_filtro_reincidentes(self):
        self.client.force_login(self.global_user)
        todos = self.client.get(
            "/api/v1/falhas/reincidence/?start_date=2026-06-01&end_date=2026-06-25&oficial=true",
            HTTP_HOST="localhost",
        ).json()
        filtrado = self.client.get(
            "/api/v1/falhas/reincidence/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&filtro=reincidentes",
            HTTP_HOST="localhost",
        ).json()
        self.assertLessEqual(filtrado["count"], todos["count"])
        for row in filtrado.get("results", []):
            self.assertEqual((row.get("reincidente") or "").lower(), "sim")

    def test_base_overview_returns_totals(self):
        self.client.force_login(self.global_user)
        response = self.client.get("/api/v1/falhas/base-overview/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("db_totals", data)
        self.assertIn("failures", data["db_totals"])
        self.assertIn("import_note", data)
        self.assertIn("scoped_totals", data)
        self.assertIn("scope_label", data)
        self.assertIn("is_global_scope", data)
        self.assertTrue(data["is_global_scope"])
        self.assertEqual(data["scope_label"], "Geral")
        self.assertIsNone(data["scoped_totals"])

    def test_base_overview_scoped_by_localidade_query(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/base-overview/?localidade=Bras%C3%ADlia",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["scope_label"], "Brasília")
        self.assertIsNotNone(data["scoped_totals"])
        for key in ("failures", "support", "training", "contestations", "agents"):
            self.assertIn(key, data["scoped_totals"])
            self.assertLessEqual(data["scoped_totals"][key], data["db_totals"][key])

    def test_base_overview_local_user_forbidden(self):
        Group.objects.get_or_create(name=GROUP_SAO_CARLOS)
        local_user = User.objects.create_user("sc_user", password="test123", email="sc_user@test.local")
        local_user.groups.add(Group.objects.get(name=GROUP_SAO_CARLOS))
        self.client.force_login(local_user)
        response = self.client.get(
            "/api/v1/falhas/base-overview/?localidade=Bras%C3%ADlia",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 403)

    def test_base_overview_last_sync_includes_stats(self):
        SyncAuditLog.objects.create(
            path=r"C:\test.xlsx",
            success=True,
            message="Sincronização concluída.",
            duration_seconds=5.0,
            finished_at=timezone.now(),
            stats={
                "sheets": {
                    "base": {
                        "rows_read": 100,
                        "rows_valid": 95,
                        "rows_persisted": 95,
                        "rows_removed_by_contestation": 3,
                        "rows_removed_by_removed_failures": 2,
                        "warnings": [],
                    }
                },
                "totals": {
                    "rows_read": 100,
                    "rows_valid": 95,
                    "rows_removed": 5,
                    "rows_persisted": 95,
                },
            },
        )
        self.client.force_login(self.global_user)
        response = self.client.get("/api/v1/falhas/base-overview/", HTTP_HOST="localhost")
        data = response.json()
        self.assertIn("last_sync", data)
        self.assertIn("stats", data["last_sync"])
        self.assertEqual(data["last_sync"]["stats"]["totals"]["rows_persisted"], 95)

    def test_support_includes_bridge_falhas(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/support/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total", data)
        self.assertIn("bridge_falhas", data)
        bridge = data["bridge_falhas"]
        self.assertIn("summary", bridge)
        self.assertIn("agentes_risco", bridge)
        self.assertIn("agentes_regra3", bridge)
        self.assertIn("agentes_nc_apenas", bridge)
        self.assertIn("clientes_workflows", bridge)
        self.assertIn("pre_diagnostico", bridge)
        self.assertIn("agentes_com_regra3_e_falha", bridge["summary"])

    def test_support_bridge_only(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/support/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&bridge_only=true",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("bridge_falhas", data)
        self.assertNotIn("total", data)

    def test_support_bridge_empty_period(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/support/?start_date=2099-01-01&end_date=2099-01-31&oficial=true",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        bridge = response.json()["bridge_falhas"]
        self.assertEqual(bridge["summary"]["agentes_com_regra3_e_falha"], 0)
        self.assertEqual(bridge["agentes_risco"], [])

    def test_support_bridge_respects_localidade(self):
        self.client.force_login(self.global_user)
        geral = self.client.get(
            "/api/v1/falhas/support/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral",
            HTTP_HOST="localhost",
        ).json()["bridge_falhas"]["summary"]
        sc = self.client.get(
            "/api/v1/falhas/support/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=S%C3%A3o%20Carlos",
            HTTP_HOST="localhost",
        ).json()["bridge_falhas"]["summary"]
        self.assertLessEqual(
            sc["agentes_com_regra3_e_falha"],
            geral["agentes_com_regra3_e_falha"],
        )

    def test_treinamentos_includes_bridge_reincidencia(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/treinamentos/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total", data)
        self.assertIn("bridge_reincidencia", data)
        bridge = data["bridge_reincidencia"]
        self.assertIn("summary", bridge)
        self.assertIn("agentes_criticos", bridge)
        self.assertIn("lideres", bridge)
        self.assertIn("cenarios", bridge)
        self.assertIn("pre_diagnostico", bridge)
        self.assertIn("agentes_criticos_com_pendencia", bridge["summary"])

    def test_treinamentos_bridge_only(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/treinamentos/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&bridge_only=true",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("bridge_reincidencia", data)
        self.assertNotIn("total", data)

    def test_treinamentos_bridge_empty_period(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            "/api/v1/falhas/treinamentos/?start_date=2099-01-01&end_date=2099-01-31&oficial=true",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        bridge = response.json()["bridge_reincidencia"]
        self.assertEqual(bridge["summary"]["agentes_criticos_com_pendencia"], 0)
        self.assertEqual(bridge["agentes_criticos"], [])

    def test_treinamentos_bridge_respects_localidade(self):
        self.client.force_login(self.global_user)
        geral = self.client.get(
            "/api/v1/falhas/treinamentos/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral",
            HTTP_HOST="localhost",
        ).json()["bridge_reincidencia"]["summary"]
        sc = self.client.get(
            "/api/v1/falhas/treinamentos/?start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=S%C3%A3o%20Carlos",
            HTTP_HOST="localhost",
        ).json()["bridge_reincidencia"]["summary"]
        self.assertLessEqual(
            sc["agentes_criticos_com_pendencia"],
            geral["agentes_criticos_com_pendencia"],
        )

    _SUMMARY_QS = "start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral"

    def test_dashboard_summary_returns_200(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            f"/api/v1/falhas/dashboard-summary/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for key in (
            "kpis", "executive", "charts_preview", "top_reincidentes",
            "alerts", "comparativo_preview", "meta",
        ):
            self.assertIn(key, data)
        self.assertIn("total_falhas", data["kpis"])
        self.assertIn("evolucao_mensal", data["charts_preview"])
        self.assertIn("qualidade_turno_pct", data["charts_preview"])
        self.assertIsInstance(data["top_reincidentes"], list)
        self.assertIsInstance(data["alerts"], list)
        self.assertIsNotNone(data["comparativo_preview"])
        self.assertIn("generated_at", data["meta"])

    def test_dashboard_summary_comparativo_null_for_local_user(self):
        Group.objects.get_or_create(name=GROUP_SAO_CARLOS)
        local_user = User.objects.create_user("sc_dash", password="test123", email="sc_dash@test.local")
        local_user.groups.add(Group.objects.get(name=GROUP_SAO_CARLOS))
        self.client.force_login(local_user)
        data = self.client.get(
            f"/api/v1/falhas/dashboard-summary/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        ).json()
        self.assertIsNone(data["comparativo_preview"])

    def test_comparativo_forbidden_for_local_user(self):
        Group.objects.get_or_create(name=GROUP_BRASILIA)
        local_user = User.objects.create_user("bsb_comp", password="test123", email="bsb_comp@test.local")
        local_user.groups.add(Group.objects.get(name=GROUP_BRASILIA))
        self.client.force_login(local_user)
        response = self.client.get(
            f"/api/v1/falhas/comparativo-bsb-sc/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 403)

    def test_comparativo_ok_for_global_user(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            f"/api/v1/falhas/comparativo-bsb-sc/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("brasilia", data)
        self.assertIn("sao_carlos", data)

    def test_dashboard_summary_respects_localidade(self):
        self.client.force_login(self.global_user)
        geral = self.client.get(
            f"/api/v1/falhas/dashboard-summary/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        ).json()["kpis"]["total_falhas"]
        sc = self.client.get(
            "/api/v1/falhas/dashboard-summary/?start_date=2026-06-01&end_date=2026-06-25"
            "&oficial=true&localidade=S%C3%A3o%20Carlos",
            HTTP_HOST="localhost",
        ).json()["kpis"]["total_falhas"]
        self.assertLessEqual(sc, geral)

    def test_dashboard_summary_alerts_empty_on_internal_error(self):
        from unittest.mock import patch
        self.client.force_login(self.global_user)
        with patch(
            "apps.falhas_criticas.services.aggregators.build_dashboard_alerts",
            side_effect=RuntimeError("alerts down"),
        ):
            data = self.client.get(
                f"/api/v1/falhas/dashboard-summary/?{self._SUMMARY_QS}",
                HTTP_HOST="localhost",
            ).json()
        self.assertEqual(data["alerts"], [])
        self.assertIn("alerts_unavailable", data["meta"].get("warnings", []))

    def test_diagnostico_summary_returns_200(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            f"/api/v1/falhas/diagnostico/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for key in (
            "rank_delta", "matrix", "clientes_workflows", "consolidado",
            "support_bridge_preview", "training_bridge_preview", "meta",
        ):
            self.assertIn(key, data)
        self.assertIn("cenario", data["rank_delta"])
        self.assertIn("summary", data["support_bridge_preview"])
        self.assertIn("summary", data["training_bridge_preview"])

    def test_diagnostico_summary_respects_localidade(self):
        self.client.force_login(self.global_user)
        geral = self.client.get(
            f"/api/v1/falhas/diagnostico/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        ).json()["consolidado"]["tiles"]
        sc = self.client.get(
            "/api/v1/falhas/diagnostico/?start_date=2026-06-01&end_date=2026-06-25"
            "&oficial=true&localidade=S%C3%A3o%20Carlos",
            HTTP_HOST="localhost",
        ).json()["consolidado"]["tiles"]
        self.assertLessEqual(sc.get("falhas", {}).get("valor", 0), geral.get("falhas", {}).get("valor", 0))

    def test_diagnostico_summary_empty_bridges_when_no_cross(self):
        self.client.force_login(self.global_user)
        data = self.client.get(
            "/api/v1/falhas/diagnostico/?start_date=2099-01-01&end_date=2099-01-31&oficial=true",
            HTTP_HOST="localhost",
        ).json()
        self.assertEqual(data["support_bridge_preview"]["summary"]["agentes_com_regra3_e_falha"], 0)
        self.assertEqual(data["training_bridge_preview"]["summary"]["agentes_criticos_com_pendencia"], 0)

    def test_diagnostico_summary_omits_por_localidade(self):
        self.client.force_login(self.global_user)
        data = self.client.get(
            f"/api/v1/falhas/diagnostico/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        ).json()
        self.assertNotIn("por_localidade", data["consolidado"])

    def test_dashboard_summary_has_duration_ms(self):
        self.client.force_login(self.global_user)
        data = self.client.get(
            f"/api/v1/falhas/dashboard-summary/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        ).json()
        self.assertIn("duration_ms", data["meta"])
        self.assertIsInstance(data["meta"]["duration_ms"], int)

    def test_reincidence_summary_returns_200(self):
        self.client.force_login(self.global_user)
        response = self.client.get(
            f"/api/v1/falhas/reincidence-summary/?{self._SUMMARY_QS}",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for key in ("by_turno", "list", "ult3m", "charts", "meta"):
            self.assertIn(key, data)
        self.assertIn("duration_ms", data["meta"])
        self.assertIn("results", data["list"])

    def test_agent_detail_includes_filter_context(self):
        from datetime import date
        from apps.falhas_criticas.models import FalhasAgent, Failure
        agent = FalhasAgent.objects.create(
            matricula_norm="c99999a",
            name="Test Agent",
            localidade="Brasília",
        )
        Failure.objects.create(
            agent=agent,
            protocolo="P1",
            data_analise=date(2026, 6, 10),
            localidade="Brasília",
            modulo="G AUDITORIA",
        )
        Failure.objects.create(
            agent=agent,
            protocolo="P2",
            data_analise=date(2025, 1, 10),
            localidade="Brasília",
            modulo="G AUDITORIA",
        )
        self.client.force_login(self.global_user)
        data = self.client.get(
            "/api/v1/falhas/agent/c99999a/"
            "?start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral",
            HTTP_HOST="localhost",
        ).json()
        self.assertIn("filter_context", data)
        self.assertEqual(data["failures_count"], 1)
        self.assertEqual(data["filter_context"]["start_date"], "2026-06-01")
