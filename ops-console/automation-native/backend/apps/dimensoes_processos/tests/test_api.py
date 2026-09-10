"""Smoke: meta endpoint e registry de slugs."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import DimEtapa
from apps.dimensoes_processos.views import REGISTRY

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class DimensoesProcessosApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dim_user", password="x")
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_registry_has_expected_slugs(self):
        expected = {
            "produto",
            "grupo-servico",
            "servico",
            "clientes",
            "workflow",
            "nivel-hierarquico",
            "nivel-hierarquico-atendimento",
            "etapas",
            "metas-etapa",
            "projecao-sla",
            "prioridades-nh-fluxo",
        }
        self.assertEqual(set(REGISTRY.keys()), expected)

    def test_meta_ok(self):
        res = self.client.get("/api/v1/dimensoes-processos/meta/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data.get("ok"))
        self.assertEqual(len(res.data.get("items") or []), 11)

    def test_list_produto_empty(self):
        res = self.client.get("/api/v1/dimensoes-processos/produto/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("results", res.data)

    def test_create_etapa_ok(self):
        res = self.client.post(
            "/api/v1/dimensoes-processos/etapas/",
            {"id_etapa": 9001, "nome": "Etapa Megazord"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data.get("id_etapa"), 9001)
        self.assertEqual(res.data.get("nome"), "Etapa Megazord")

    def test_create_etapa_auto_increment_id(self):
        DimEtapa.objects.create(id_etapa=10, nome="Existente")
        res = self.client.post(
            "/api/v1/dimensoes-processos/etapas/",
            {"nome": "Nova auto"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data.get("id_etapa"), 11)
        self.assertEqual(res.data.get("nome"), "Nova auto")

    def test_etapas_proximo_id(self):
        DimEtapa.objects.create(id_etapa=6309, nome="Alta")
        res = self.client.get("/api/v1/dimensoes-processos/etapas/proximo-id/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data.get("next_id"), 6310)

    def test_catalog_search_accepts_primary_key_from_derivacao_link(self):
        DimEtapa.objects.create(id_etapa=6309, nome="Nome sem o identificador")
        DimEtapa.objects.create(id_etapa=6310, nome="Outra etapa")

        res = self.client.get("/api/v1/dimensoes-processos/etapas/", {"search": "6309"})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(res.data["results"][0]["id_etapa"], 6309)

    def test_projecao_equipes_catalog_removed(self):
        res = self.client.get("/api/v1/dimensoes-processos/projecao-equipes/")
        self.assertEqual(res.status_code, 404)
    def test_create_metas_etapa_still_blocked(self):
        res = self.client.post(
            "/api/v1/dimensoes-processos/metas-etapa/",
            {
                "data_inicio": "2026-01-01",
                "etapa": 1,
                "meta_dia": 10,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 405)

    def test_create_projecao_sla_still_blocked(self):
        res = self.client.post(
            "/api/v1/dimensoes-processos/projecao-sla/",
            {
                "data_inicio": "2026-01-01",
                "cliente": 1,
                "workflow": 1,
                "nivel_hierarquico": 1,
                "dias_semana": "{0..4}",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 405)

    def test_ciclo_apos_criar_etapa(self):
        create = self.client.post(
            "/api/v1/dimensoes-processos/etapas/",
            {"id_etapa": 9002, "nome": "Com ciclo"},
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        ciclo = self.client.post(
            "/api/v1/dimensoes-processos/metas-etapa/ciclos/",
            {
                "acao": "criar",
                "data_inicio": "2026-07-01",
                "etapa": 9002,
                "meta_dia": "12.5",
            },
            format="json",
        )
        self.assertIn(ciclo.status_code, (200, 201))
        self.assertTrue(ciclo.data.get("ok"))
        self.assertEqual(ciclo.data.get("acao"), "criar")

    def test_nivel_hierarquico_atendimento_crud_feeds_dimensions(self):
        """Catálogo Megazord e combobox Escala Flex compartilham ef_hierarchical_level."""
        create = self.client.post(
            "/api/v1/dimensoes-processos/nivel-hierarquico-atendimento/",
            {"name": "NH Atendimento Teste", "active": True},
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        level_id = create.data.get("id")
        self.assertTrue(level_id)

        listed = self.client.get("/api/v1/dimensoes-processos/nivel-hierarquico-atendimento/")
        self.assertEqual(listed.status_code, 200)
        names = [r.get("name") for r in listed.data.get("results") or []]
        self.assertIn("NH Atendimento Teste", names)

        dims = self.client.get("/api/v1/escala-flex/dimensions/hierarchical-levels/")
        self.assertEqual(dims.status_code, 200)
        dim_ids = [str(r.get("id")) for r in dims.data.get("results") or []]
        self.assertIn(str(level_id), dim_ids)

    def test_prioridades_nh_fluxo_readonly(self):
        from django.utils import timezone

        from apps.prioridades_nh.models import NhPrioridadeFluxo

        NhPrioridadeFluxo.objects.create(
            prk_cliente=1,
            nom_cliente="Cliente Teste",
            prk_workflow=10,
            nom_workflow="WF Teste",
            prk_nivel_hierarquico=100,
            nom_nivel_hierarquico="NH Teste",
            prk_fluxo=200,
            nom_fluxo="Etapa Teste",
            num_prioridade_fluxo=5,
            synced_at=timezone.now(),
        )
        listed = self.client.get("/api/v1/dimensoes-processos/prioridades-nh-fluxo/")
        self.assertEqual(listed.status_code, 200)
        results = listed.data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("nom_nivel_hierarquico"), "NH Teste")
        self.assertEqual(listed.data.get("count"), 1)

        # Paginação padrão de 50; page_size aceito pela API do catálogo.
        page = self.client.get(
            "/api/v1/dimensoes-processos/prioridades-nh-fluxo/",
            {"page": 1, "page_size": 50},
        )
        self.assertEqual(page.status_code, 200)
        self.assertLessEqual(len(page.data.get("results") or []), 50)

        create = self.client.post(
            "/api/v1/dimensoes-processos/prioridades-nh-fluxo/",
            {"prk_nivel_hierarquico": 1, "prk_fluxo": 2, "num_prioridade_fluxo": 1},
            format="json",
        )
        self.assertEqual(create.status_code, 405)
