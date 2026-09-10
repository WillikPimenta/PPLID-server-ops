# -*- coding: utf-8 -*-
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_OP_AGENTE,
    ROLE_OP_GERENCIA,
    ROLE_OP_LIDER,
    role_group_name,
)
from apps.produtividade.models import ProductivityRecord
from apps.produtividade.scoping import (
    apply_produtividade_scope,
    matricula_in_scope,
    produtividade_scope_for_user,
)
from apps.produtividade.services.analytics import get_filter_options
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True)
class ProdutividadeScopingTests(TestCase):
    def setUp(self):
        tz = timezone.get_current_timezone()
        self.day_old = datetime(2026, 6, 10, 10, tzinfo=tz)
        self.day_new = datetime(2026, 6, 17, 10, tzinfo=tz)

        for role in (ROLE_OP_AGENTE, ROLE_OP_LIDER, ROLE_OP_GERENCIA):
            Group.objects.get_or_create(name=role_group_name(role))

        self.leader_agent = Agent.objects.create(
            full_name="Lider Teste", user_lan_id="lider01", active=True
        )
        self.agent_a = Agent.objects.create(
            full_name="Agente A", user_lan_id="agente_a", active=True
        )
        self.agent_b = Agent.objects.create(
            full_name="Agente B", user_lan_id="agente_b", active=True
        )
        self.outsider = Agent.objects.create(
            full_name="Outro", user_lan_id="outsider", active=True
        )

        AgentHistory.objects.create(
            agent=self.agent_a,
            leader=self.leader_agent,
            start_date=date(2026, 1, 1),
            active=True,
            team="Time Lider",
        )
        AgentHistory.objects.create(
            agent=self.agent_b,
            leader=self.leader_agent,
            start_date=date(2026, 1, 1),
            active=True,
            team="Time Lider",
        )
        AgentHistory.objects.create(
            agent=self.outsider,
            leader=None,
            start_date=date(2026, 1, 1),
            active=True,
            team="Outro Time",
        )

        self.user_agente = User.objects.create_user(
            "agente_a", email="a@test.local", password="x"
        )
        self.user_agente.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))
        UserProfile.objects.create(user=self.user_agente, agent=self.agent_a)

        self.user_lider = User.objects.create_user(
            "lider01", email="l@test.local", password="x"
        )
        self.user_lider.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_LIDER)))
        UserProfile.objects.create(user=self.user_lider, agent=self.leader_agent)

        self.user_gerencia = User.objects.create_user(
            "gerencia01", email="g@test.local", password="x"
        )
        self.user_gerencia.groups.add(
            Group.objects.get(name=role_group_name(ROLE_OP_GERENCIA))
        )

        for mat, when in (
            ("agente_a", self.day_old),
            ("agente_a", self.day_new),
            ("agente_b", self.day_new),
            ("outsider", self.day_new),
        ):
            ProductivityRecord.objects.create(
                matricula_norm=mat,
                etapa="Etapa A",
                analysis_seconds=100,
                analysis_count=10,
                stage_goal=Decimal("100"),
                recorded_at=when,
                agent_name=mat,
                team="Time Lider" if mat != "outsider" else "Outro Time",
            )

        self.client = APIClient()

    def test_scope_own_team_global(self):
        self.assertEqual(produtividade_scope_for_user(self.user_agente), "own")
        self.assertEqual(produtividade_scope_for_user(self.user_lider), "team")
        self.assertEqual(produtividade_scope_for_user(self.user_gerencia), "global")

    def test_apply_scope_agente(self):
        qs = apply_produtividade_scope(ProductivityRecord.objects.all(), self.user_agente)
        mats = set(qs.values_list("matricula_norm", flat=True).distinct())
        self.assertEqual(mats, {"agente_a"})

    def test_apply_scope_lider_does_not_auto_filter_team(self):
        """Op. Líder vê todos por padrão; time só com filtro meu_time."""
        qs = apply_produtividade_scope(ProductivityRecord.objects.all(), self.user_lider)
        mats = set(qs.values_list("matricula_norm", flat=True).distinct())
        self.assertEqual(mats, {"agente_a", "agente_b", "outsider"})

    def test_apply_scope_gerencia(self):
        qs = apply_produtividade_scope(ProductivityRecord.objects.all(), self.user_gerencia)
        mats = set(qs.values_list("matricula_norm", flat=True).distinct())
        self.assertEqual(mats, {"agente_a", "agente_b", "outsider"})

    def test_matricula_in_scope(self):
        self.assertTrue(matricula_in_scope(self.user_agente, "agente_a"))
        self.assertFalse(matricula_in_scope(self.user_agente, "agente_b"))
        self.assertTrue(matricula_in_scope(self.user_lider, "agente_b"))
        self.assertTrue(matricula_in_scope(self.user_lider, "outsider"))
        self.assertTrue(matricula_in_scope(self.user_gerencia, "outsider"))

    def test_filter_defaults_one_civil_day(self):
        """Padrão = último dia civil (0h–23h); D-1 só ao ampliar o intervalo."""
        opts = get_filter_options()
        self.assertEqual(opts["default_end_date"], opts["max_date"])
        self.assertEqual(opts["max_date"], "2026-06-17")
        self.assertEqual(opts["min_date"], "2026-06-10")
        self.assertEqual(opts["default_start_date"], "2026-06-17")

    def test_api_por_agente_scoped(self):
        dates = {"start_date": "2026-06-10", "end_date": "2026-06-17"}
        self.client.force_authenticate(self.user_agente)
        resp = self.client.get("/api/v1/produtividade/por-agente/", dates)
        self.assertEqual(resp.status_code, 200)
        mats = {row["matricula"] for row in resp.data["results"]}
        self.assertEqual(mats, {"agente_a"})

        self.client.force_authenticate(self.user_lider)
        resp = self.client.get("/api/v1/produtividade/por-agente/", dates)
        self.assertEqual(resp.status_code, 200)
        mats = {row["matricula"] for row in resp.data["results"]}
        self.assertEqual(mats, {"agente_a", "agente_b", "outsider"})

        resp = self.client.get(
            "/api/v1/produtividade/por-agente/", {**dates, "meu_time": "true"}
        )
        self.assertEqual(resp.status_code, 200)
        mats = {row["matricula"] for row in resp.data["results"]}
        self.assertEqual(mats, {"agente_a", "agente_b"})

    def test_api_agent_detail_out_of_scope_404(self):
        dates = {"start_date": "2026-06-10", "end_date": "2026-06-17"}
        self.client.force_authenticate(self.user_agente)
        resp = self.client.get("/api/v1/produtividade/agente/outsider/", dates)
        self.assertEqual(resp.status_code, 404)

        # Líder pode abrir qualquer agente; recorte de time é só via meu_time.
        self.client.force_authenticate(self.user_lider)
        resp = self.client.get("/api/v1/produtividade/agente/outsider/", dates)
        self.assertEqual(resp.status_code, 200)

        self.client.force_authenticate(self.user_gerencia)
        resp = self.client.get("/api/v1/produtividade/agente/outsider/", dates)
        self.assertEqual(resp.status_code, 200)

    def test_api_filters_scoped_agentes(self):
        self.client.force_authenticate(self.user_lider)
        resp = self.client.get("/api/v1/produtividade/filters/")
        self.assertEqual(resp.status_code, 200)
        mats = {a["matricula"] for a in resp.data["agentes"]}
        self.assertEqual(mats, {"agente_a", "agente_b", "outsider"})
        self.assertEqual(resp.data["default_start_date"], "2026-06-17")
        self.assertEqual(resp.data["default_end_date"], resp.data["max_date"])
        self.assertTrue(resp.data["team_filter"]["can_filter_team"])

    def test_impersonated_lider_enables_meu_time_opt_in(self):
        """Impersonação: dados completos por padrão; time só com meu_time."""
        from apps.access.services.impersonation import set_impersonation_session

        admin = User.objects.create_superuser(
            "admin_imp", email="admin@test.local", password="x"
        )
        self.client.force_authenticate(admin)
        resp = self.client.get("/api/v1/produtividade/filters/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["team_filter"]["can_filter_team"])

        session = self.client.session
        request = type("R", (), {"user": admin, "session": session})()
        set_impersonation_session(request, "lider01")
        session.save()

        resp = self.client.get("/api/v1/produtividade/filters/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["team_filter"]["can_filter_team"])
        mats = {a["matricula"] for a in resp.data["agentes"]}
        self.assertEqual(mats, {"agente_a", "agente_b", "outsider"})

        dates = {"start_date": "2026-06-10", "end_date": "2026-06-17"}
        resp = self.client.get("/api/v1/produtividade/por-agente/", dates)
        self.assertEqual(resp.status_code, 200)
        mats = {row["matricula"] for row in resp.data["results"]}
        self.assertEqual(mats, {"agente_a", "agente_b", "outsider"})

        resp = self.client.get(
            "/api/v1/produtividade/por-agente/", {**dates, "meu_time": "true"}
        )
        self.assertEqual(resp.status_code, 200)
        mats = {row["matricula"] for row in resp.data["results"]}
        self.assertEqual(mats, {"agente_a", "agente_b"})
