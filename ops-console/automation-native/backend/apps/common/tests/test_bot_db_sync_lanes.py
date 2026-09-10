# -*- coding: utf-8 -*-
"""Classificação high/low da fila bot→banco."""
from __future__ import annotations

from django.test import SimpleTestCase

from apps.common.bot_db_sync_lanes import LANE_HIGH, LANE_MID, LANE_LOW, resolve_lane


class BotDbSyncLanesTests(SimpleTestCase):
    def test_high_rotina_pesados(self):
        for report in (
            "detalhado",
            "prod",
            "ged_detalhado",
            "ged_irregularidade",
            "g_auditoria",
        ):
            self.assertEqual(resolve_lane("rotina_bruto", report), LANE_HIGH)

    def test_low_rotina_leves(self):
        for report in ("monitor", "confer_busca"):
            self.assertEqual(resolve_lane("rotina_bruto", report), LANE_LOW)

    def test_falhas_is_high(self):
        self.assertEqual(resolve_lane("falhas_criticas"), LANE_HIGH)

    def test_produtividade_is_low_like_its_runner(self):
        self.assertEqual(resolve_lane("produtividade"), LANE_LOW)
        self.assertEqual(resolve_lane("produtividade", "prod"), LANE_LOW)

    def test_domains_low(self):
        self.assertEqual(resolve_lane("monitor_eventos"), LANE_LOW)
        self.assertEqual(resolve_lane("replicacao_d1", "replicados"), LANE_LOW)

    def test_quality_projection_is_mid(self):
        self.assertEqual(resolve_lane("qualidade_projection"), LANE_MID)
