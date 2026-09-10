# -*- coding: utf-8 -*-
"""Lookup MetaEtapa → stage_goal (Megazord)."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.dimensoes_processos.models import DimEtapa, DimServico, MetaEtapa
from apps.dimensoes_processos.services.meta_etapa_lookup import (
    MetaEtapaLookupCache,
    meta_dia_as_stage_goal,
    normalize_etapa_nome,
    resolve_meta_etapa,
    resolve_stage_goal,
)


class NormalizeEtapaNomeTests(TestCase):
    def test_strip_casefold_collapse_spaces(self):
        self.assertEqual(
            normalize_etapa_nome("  Análise   Visual  "),
            "análise visual",
        )
        self.assertEqual(normalize_etapa_nome(None), "")
        self.assertEqual(normalize_etapa_nome("FOO"), normalize_etapa_nome("foo"))


class ResolveMetaEtapaTests(TestCase):
    def setUp(self):
        self.etapa = DimEtapa.objects.create(id_etapa=101, nome="Análise Visual")
        self.servico = DimServico.objects.create(
            id_servico=1, nome="Serviço A", meta_dia=Decimal("10")
        )

    def test_vigente_hoje_preferido(self):
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=date(2026, 6, 30),
            etapa=self.etapa,
            meta_dia=Decimal("100"),
            servico=None,
        )
        vigente = MetaEtapa.objects.create(
            data_inicio=date(2026, 7, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=Decimal("250"),
            servico=None,
        )
        today = date(2026, 7, 22)
        got = resolve_meta_etapa(
            etapa_nome="análise visual",
            on_date=today,
            today=today,
        )
        self.assertEqual(got.pk, vigente.pk)
        self.assertEqual(meta_dia_as_stage_goal(got), Decimal("250"))

    def test_historico_com_data_fim(self):
        antigo = MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=date(2026, 3, 31),
            etapa=self.etapa,
            meta_dia=Decimal("80"),
            servico=None,
        )
        MetaEtapa.objects.create(
            data_inicio=date(2026, 4, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=Decimal("200"),
            servico=None,
        )
        got = resolve_meta_etapa(
            etapa_nome="Análise Visual",
            on_date=date(2026, 2, 15),
            today=date(2026, 7, 22),
        )
        self.assertEqual(got.pk, antigo.pk)
        self.assertEqual(got.meta_dia, Decimal("80"))

    def test_prefer_servico_null(self):
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=Decimal("999"),
            servico=self.servico,
        )
        sem_servico = MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=Decimal("400"),
            servico=None,
        )
        got = resolve_meta_etapa(
            etapa_nome="Análise Visual",
            on_date=date(2026, 7, 1),
            today=date(2026, 7, 1),
        )
        self.assertEqual(got.pk, sem_servico.pk)

    def test_fallback_servico_quando_so_existe_com_servico(self):
        com_servico = MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=Decimal("320"),
            servico=self.servico,
        )
        got = resolve_meta_etapa(
            etapa_nome="Análise Visual",
            on_date=date(2026, 7, 1),
            today=date(2026, 7, 1),
        )
        self.assertEqual(got.pk, com_servico.pk)

    def test_duplicata_escolhe_maior_data_inicio(self):
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=date(2026, 12, 31),
            etapa=self.etapa,
            meta_dia=Decimal("50"),
            servico=None,
        )
        mais_recente = MetaEtapa.objects.create(
            data_inicio=date(2026, 6, 1),
            data_fim=date(2026, 12, 31),
            etapa=self.etapa,
            meta_dia=Decimal("75"),
            servico=None,
        )
        got = resolve_meta_etapa(
            etapa_nome="Análise Visual",
            on_date=date(2026, 7, 10),
            today=date(2026, 8, 1),  # não é "hoje" → não força vigente
        )
        self.assertEqual(got.pk, mais_recente.pk)

    def test_etapa_sem_dim_retorna_none(self):
        self.assertIsNone(
            resolve_meta_etapa(
                etapa_nome="Etapa Inexistente",
                on_date=date(2026, 7, 1),
                today=date(2026, 7, 1),
            )
        )

    def test_meta_dia_invalida_returns_none_stage_goal(self):
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=Decimal("0"),
            servico=None,
        )
        meta = resolve_meta_etapa(
            etapa_nome="Análise Visual",
            on_date=date(2026, 7, 1),
            today=date(2026, 7, 1),
        )
        self.assertIsNotNone(meta)
        self.assertIsNone(meta_dia_as_stage_goal(meta))
        self.assertIsNone(
            resolve_stage_goal(
                etapa_nome="Análise Visual",
                on_date=date(2026, 7, 1),
                today=date(2026, 7, 1),
            )
        )

    def test_cache_batch_resolve(self):
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=Decimal("111"),
            servico=None,
        )
        cache = MetaEtapaLookupCache.build(
            etapa_nomes=["Análise Visual"],
            date_min=date(2026, 7, 1),
            date_max=date(2026, 7, 31),
        )
        got = resolve_meta_etapa(
            etapa_nome="  Análise   Visual ",
            on_date=date(2026, 7, 15),
            today=date(2026, 7, 15),
            cache=cache,
        )
        self.assertEqual(got.meta_dia, Decimal("111"))
