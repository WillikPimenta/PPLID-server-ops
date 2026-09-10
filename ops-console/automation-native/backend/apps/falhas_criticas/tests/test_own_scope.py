# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.access.constants import ROLE_OP_AGENTE, role_group_name
from apps.falhas_criticas.constants import GROUP_GLOBAL
from apps.falhas_criticas.models import FalhasAgent, Failure
from apps.falhas_criticas.scoping import get_user_scope, scoped_query_params

User = get_user_model()


class OwnScopeTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=GROUP_GLOBAL)
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        self.agent = FalhasAgent.objects.create(
            matricula_norm="c90001a",
            name="Agente Teste Painel",
            localidade="Brasília",
            turno_atual="Comercial",
        )
        Failure.objects.create(
            protocolo="OWN-1",
            data_analise=timezone.now().date(),
            cenario="Cenário teste own",
            cliente="Cliente X",
            localidade="Brasília",
            agent=self.agent,
        )
        self.own_user = User.objects.create_user("c90001a", password="test123", email="c90001a@test.local")
        self.own_user.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))
        self.other = FalhasAgent.objects.create(
            matricula_norm="c90002a",
            name="Outro Agente",
            localidade="São Carlos",
        )

    def test_get_user_scope_own(self):
        scope = get_user_scope(self.own_user)
        self.assertEqual(scope["scope"], "own")
        self.assertEqual(scope["matricula_forcada"], "c90001a")
        self.assertIsNone(scope["localidade_forcada"])
        self.assertFalse(scope["can_choose_localidade"])

    def test_scoped_params_force_matricula(self):
        params, scope = scoped_query_params(
            self.own_user,
            {"localidade": "São Carlos", "matricula": "c90002a", "meu_time": "true"},
        )
        self.assertEqual(scope["scope"], "own")
        self.assertEqual(params["matricula"], "c90001a")
        self.assertFalse(params.get("meu_time"))

    def test_me_exposes_own_flags(self):
        self.client.force_login(self.own_user)
        data = self.client.get("/api/v1/falhas/me/", HTTP_HOST="localhost").json()
        self.assertEqual(data["scope"], "own")
        self.assertTrue(data["is_own_scope"])
        self.assertEqual(data["matricula_forcada"], "c90001a")

    def test_agent_detail_self_ok(self):
        self.client.force_login(self.own_user)
        response = self.client.get(
            "/api/v1/falhas/agent/c90001a/?start_date=2020-01-01&end_date=2030-12-31",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["matricula"], "c90001a")
        self.assertGreaterEqual(data["failures_count"], 1)

    def test_agent_detail_other_forbidden(self):
        self.client.force_login(self.own_user)
        response = self.client.get(
            "/api/v1/falhas/agent/c90002a/",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 403)

    def test_comparativo_forbidden_for_own(self):
        self.client.force_login(self.own_user)
        response = self.client.get(
            "/api/v1/falhas/comparativo-bsb-sc/?start_date=2026-06-01&end_date=2026-06-25",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 403)
