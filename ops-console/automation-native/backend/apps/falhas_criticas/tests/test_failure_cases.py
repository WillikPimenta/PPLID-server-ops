# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.utils import timezone

from apps.falhas_criticas.constants import GROUP_GLOBAL
from apps.falhas_criticas.models import FalhasAgent, Failure

User = get_user_model()


class FailureCasesApiTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=GROUP_GLOBAL)
        self.user = User.objects.create_user("cases_user", password="test123")
        self.user.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        self.client = Client()
        self.agent = FalhasAgent.objects.create(
            matricula_norm="c90111a",
            name="Casos Agente",
            localidade="Brasília",
        )
        today = timezone.now().date()
        Failure.objects.create(
            protocolo="CASE-1",
            data_analise=today,
            cenario="FORMATACAO/FONTE",
            uf_documento="SP",
            tipo_documento="CNH",
            localidade="Brasília",
            cliente="Cliente A",
            agent=self.agent,
        )
        Failure.objects.create(
            protocolo="CASE-2",
            data_analise=today,
            cenario="FORMATACAO/FONTE",
            uf_documento="RJ",
            tipo_documento="RG",
            localidade="Brasília",
            agent=self.agent,
        )

    def test_cases_filter_cenario_uf(self):
        self.client.force_login(self.user)
        response = self.client.get(
            "/api/v1/falhas/cases/?start_date=2020-01-01&end_date=2030-12-31"
            "&cenario=FORMATACAO/FONTE&uf_documento=SP",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["protocolo"], "CASE-1")
        self.assertEqual(data["filters"]["uf_documento"], "SP")
        self.assertNotEqual(data["filters"].get("localidade"), "SP")
