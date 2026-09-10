# -*- coding: utf-8 -*-
"""API e serviço de etapas faltantes (produtividade ↔ Megazord)."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import DimEtapa, MetaEtapa
from apps.dimensoes_processos.services.etapas_faltantes import (
    MOTIVO_SEM_DIM,
    MOTIVO_SEM_META_VIGENTE,
    list_etapas_faltantes,
)
from apps.produtividade.models import SOURCE_BRFLOW, ProductivityRecord

User = get_user_model()


class EtapasFaltantesServiceTests(TestCase):
    def _rec(self, etapa: str, *, days_ago: int = 1):
        ProductivityRecord.objects.create(
            matricula_norm="c92928a",
            etapa=etapa,
            analysis_seconds=10,
            analysis_count=1,
            stage_goal=None,
            recorded_at=timezone.now() - timedelta(days=days_ago),
            source=SOURCE_BRFLOW,
        )

    def test_sem_dim_etapa(self):
        self._rec("Etapa Só HxH")
        gaps = list_etapas_faltantes(days=90)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["motivo"], MOTIVO_SEM_DIM)
        self.assertIsNone(gaps[0]["id_etapa"])
        self.assertEqual(gaps[0]["etapa_nome"], "Etapa Só HxH")

    def test_sem_meta_vigente(self):
        etapa = DimEtapa.objects.create(id_etapa=701, nome="Com Dim Sem Meta")
        MetaEtapa.objects.create(
            data_inicio=date(2025, 1, 1),
            data_fim=date(2025, 12, 31),
            etapa=etapa,
            meta_dia=Decimal("100"),
            servico=None,
        )
        self._rec("Com Dim Sem Meta")
        gaps = list_etapas_faltantes(days=90)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["motivo"], MOTIVO_SEM_META_VIGENTE)
        self.assertEqual(gaps[0]["id_etapa"], 701)

    def test_com_meta_vigente_ausente_da_lista(self):
        etapa = DimEtapa.objects.create(id_etapa=702, nome="OK Vigente")
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=etapa,
            meta_dia=Decimal("200"),
            servico=None,
        )
        self._rec("OK Vigente")
        gaps = list_etapas_faltantes(days=90)
        self.assertEqual(gaps, [])


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class EtapasFaltantesApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="gap_user", password="x")
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_get_etapas_faltantes(self):
        ProductivityRecord.objects.create(
            matricula_norm="c92928a",
            etapa="Falta Cadastro",
            analysis_seconds=5,
            analysis_count=1,
            stage_goal=None,
            recorded_at=timezone.now(),
            source=SOURCE_BRFLOW,
        )
        res = self.client.get("/api/v1/dimensoes-processos/metas-etapa/etapas-faltantes/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data.get("ok"))
        self.assertGreaterEqual(res.data.get("count"), 1)
        nomes = {r["etapa_nome"] for r in res.data.get("results") or []}
        self.assertIn("Falta Cadastro", nomes)
