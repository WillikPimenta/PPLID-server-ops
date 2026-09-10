# -*- coding: utf-8 -*-
from django.test import TestCase
from django.utils import timezone

from apps.prioridades_nh.models import NhPrioridadeFluxo
from apps.prioridades_nh.services.sync import sync_prioridades_from_rows


def _row(**overrides):
    base = {
        "prk_cliente": 1,
        "nom_cliente": "Cliente A",
        "prk_workflow": 10,
        "nom_workflow": "WF A",
        "prk_nivel_hierarquico": 100,
        "nom_nivel_hierarquico": "NH Teste",
        "prk_fluxo": 200,
        "nom_fluxo": "Etapa A",
        "prk_modulo": 6,
        "nom_modulo": "Análise Visual",
        "num_prioridade_fluxo": 5,
        "cod_analise": "",
        "hierarchical_level_id": None,
    }
    base.update(overrides)
    return base


class PrioridadesNhSyncTests(TestCase):
    def test_insert_then_unchanged_then_update_then_delete(self):
        t1 = timezone.now()
        stats = sync_prioridades_from_rows([_row()], now=t1)
        self.assertEqual(stats, {"inserted": 1, "updated": 0, "unchanged": 0, "deleted": 0})
        obj = NhPrioridadeFluxo.objects.get()
        self.assertEqual(obj.num_prioridade_fluxo, 5)
        self.assertEqual(obj.synced_at, t1)

        t2 = timezone.now()
        stats = sync_prioridades_from_rows([_row()], now=t2)
        self.assertEqual(stats, {"inserted": 0, "updated": 0, "unchanged": 1, "deleted": 0})
        obj.refresh_from_db()
        self.assertEqual(obj.synced_at, t1)

        t3 = timezone.now()
        stats = sync_prioridades_from_rows([_row(num_prioridade_fluxo=9)], now=t3)
        self.assertEqual(stats, {"inserted": 0, "updated": 1, "unchanged": 0, "deleted": 0})
        obj.refresh_from_db()
        self.assertEqual(obj.num_prioridade_fluxo, 9)
        self.assertEqual(obj.synced_at, t3)

        t4 = timezone.now()
        stats = sync_prioridades_from_rows([], now=t4)
        self.assertEqual(stats, {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 1})
        self.assertEqual(NhPrioridadeFluxo.objects.count(), 0)

    def test_accepts_brflow_uppercase_keys(self):
        from apps.prioridades_nh.services.sync import normalize_row

        row = normalize_row(
            {
                "PRK_CLIENTE": "384",
                "NOM_CLIENTE": "Cliente",
                "PRK_WORKFLOW": "13709",
                "NOM_WORKFLOW": "WF",
                "PRK_NIVEL_HIERARQUICO": "13822",
                "NOM_NIVEL_HIERARQUICO": "NH",
                "PRK_FLUXO": "3405",
                "NOM_FLUXO": "Etapa",
                "PRK_MODULO": "6",
                "NOM_MODULO": "Módulo",
                "NUM_PRIORIDADE_FLUXO": "18",
                "COD_ANALISE": None,
            }
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["prk_nivel_hierarquico"], 13822)
        self.assertEqual(row["num_prioridade_fluxo"], 18)
        self.assertEqual(row["cod_analise"], "")
