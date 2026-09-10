import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.auditoria.models import AuditoriaAtividade, AuditoriaFalhaCadastro
from apps.auditoria.services.analise_origem import get_or_create_analise_origem
from apps.workforce.models import Agent


User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaRealizadosConsultaTests(TestCase):
    endpoint = "/api/v1/qualidade/auditoria/falhas/realizadas/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="auditor-consulta",
            email="auditor-consulta@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)
        self.auditor = Agent.objects.create(
            user_lan_id=self.user.username,
            full_name="Nome Completo do Auditor",
        )
        self.atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Auditoria realizada",
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
            created_by=self.user,
        )

    def create_realizado(self, protocolo: str, **overrides):
        data = {
            "atividade": self.atividade,
            "protocolo": protocolo,
            "tipo_falha": "Colaborador",
            "usuario": "c123456",
            "modulo": "Risk Manager",
            "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            "auditor": self.user.username,
            "auditor_ref": self.auditor,
            "created_by": self.user,
        }
        data.update(overrides)
        return AuditoriaFalhaCadastro.objects.create(**data)

    def test_exibe_nome_do_auditor_em_vez_da_matricula(self):
        self.create_realizado("AUDITOR-NOME")

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["created_by"], "Nome Completo do Auditor")
        self.assertIn("Nome Completo do Auditor", response.data["filter_options"]["created_by"])
        self.assertNotIn(self.user.username, response.data["filter_options"]["created_by"])

    def test_exibe_e_filtra_autor_legado_quando_auditor_ref_esta_vazio(self):
        self.user.first_name = "Auditor"
        self.user.last_name = "Legado"
        self.user.save(update_fields=["first_name", "last_name"])
        self.create_realizado(
            "AUDITOR-LEGADO",
            auditor="",
            auditor_ref=None,
        )

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["created_by"], "Auditor Legado")
        self.assertIn("Auditor Legado", response.data["filter_options"]["created_by"])

        filtered = self.client.get(
            self.endpoint,
            {"filter_created_by": json.dumps(["Auditor Legado"])},
        )
        self.assertEqual(filtered.status_code, 200, filtered.data)
        self.assertEqual(filtered.data["total"], 1)
        self.assertEqual(filtered.data["results"][0]["protocolo"], "AUDITOR-LEGADO")

    def test_lista_a_tabela_de_realizados_em_paginas_de_cinquenta(self):
        self.create_realizado("ALVO-FORA-DA-PRIMEIRA-PAGINA")
        for index in range(54):
            self.create_realizado(f"AUD-{index:03d}")

        AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade ainda sem realizado",
            created_by=self.user,
        )
        self.create_realizado(
            "CONTESTACAO-IGNORADA",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
        )

        first_page = self.client.get(self.endpoint)
        self.assertEqual(first_page.status_code, 200, first_page.data)
        self.assertEqual(first_page.data["total"], 55)
        self.assertEqual(first_page.data["page_size"], 50)
        self.assertEqual(first_page.data["total_pages"], 2)
        self.assertEqual(len(first_page.data["results"]), 50)
        self.assertNotIn(
            "ALVO-FORA-DA-PRIMEIRA-PAGINA",
            [item["protocolo"] for item in first_page.data["results"]],
        )

        second_page = self.client.get(self.endpoint, {"page": 2})
        self.assertEqual(len(second_page.data["results"]), 5)
        self.assertIn(
            "ALVO-FORA-DA-PRIMEIRA-PAGINA",
            [item["protocolo"] for item in second_page.data["results"]],
        )

    def test_reutiliza_opcoes_de_filtro_em_cache_curto(self):
        self.create_realizado("CACHE-FILTROS")

        with CaptureQueriesContext(connection) as first_queries:
            first = self.client.get(self.endpoint)
        with CaptureQueriesContext(connection) as cached_queries:
            second = self.client.get(self.endpoint)

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertLess(len(cached_queries), len(first_queries))

    def test_filtro_do_cabecalho_pesquisa_antes_da_paginacao(self):
        self.create_realizado("ALVO-GLOBAL")
        for index in range(54):
            self.create_realizado(f"OUTRO-{index:03d}")

        unfiltered = self.client.get(self.endpoint)
        self.assertIn("ALVO-GLOBAL", unfiltered.data["filter_options"]["protocolo"])
        self.assertNotIn(
            "ALVO-GLOBAL",
            [item["protocolo"] for item in unfiltered.data["results"]],
        )

        filtered = self.client.get(
            self.endpoint,
            {"filter_protocolo": json.dumps(["ALVO-GLOBAL"])},
        )
        self.assertEqual(filtered.status_code, 200, filtered.data)
        self.assertEqual(filtered.data["total"], 1)
        self.assertEqual(filtered.data["page"], 1)
        self.assertEqual(filtered.data["results"][0]["protocolo"], "ALVO-GLOBAL")
        self.assertEqual(filtered.data["results"][0]["atividade_nome"], "Auditoria realizada")

    def test_inclui_registro_legado_de_auditoria_com_origem_vazia(self):
        self.create_realizado("AUDITORIA-ATUAL")
        self.create_realizado("AUDITORIA-LEGADA", origem="")
        self.create_realizado(
            "REINSPECAO-IGNORADA",
            origem="",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
        )

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 2)
        self.assertCountEqual(
            [item["protocolo"] for item in response.data["results"]],
            ["AUDITORIA-ATUAL", "AUDITORIA-LEGADA"],
        )

    def test_nao_mistura_resultados_de_auditoria_compliance(self):
        self.create_realizado("AUD-FRAUD")
        compliance_origin = get_or_create_analise_origem(
            protocolo="AUD-COMPLIANCE-ATUAL",
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )
        self.create_realizado(
            "AUD-COMPLIANCE-ATUAL",
            atividade=None,
            analise_origem=compliance_origin,
            brflow_parsed={},
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
        )
        self.create_realizado(
            "AUD-COMPLIANCE-LEGADA",
            atividade=None,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
        )

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["protocolo"], "AUD-FRAUD")
        self.assertEqual(response.data["filter_options"]["protocolo"], ["AUD-FRAUD"])
