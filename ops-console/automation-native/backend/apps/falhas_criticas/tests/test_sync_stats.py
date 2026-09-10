# -*- coding: utf-8 -*-
import pandas as pd
from django.test import TestCase

from apps.falhas_criticas.services.sync_stats import build_sync_stats


class SyncStatsTests(TestCase):
    def test_build_sync_stats_aggregates_removals(self):
        contest = pd.DataFrame({
            'Registros base antes': [2100],
            'Linhas removidas': [10],
            'Registros base depois': [2090],
        })
        removidas = pd.DataFrame({
            'Linhas removidas': [5],
            'Registros base depois': [2085],
        })
        stats = build_sync_stats(
            contest_resumo=contest,
            removidas_resumo=removidas,
            df_base_len=2085,
            failures_persisted=2085,
            hc_rows_read=800,
            agents_persisted=711,
            support_rows_read=13661,
            support_persisted=13661,
            training_rows_read=13307,
            training_persisted=13307,
            contest_rows_read=967,
            contest_persisted=967,
        )
        self.assertEqual(stats['sheets']['base']['rows_removed_by_contestation'], 10)
        self.assertEqual(stats['sheets']['base']['rows_removed_by_removed_failures'], 5)
        self.assertEqual(stats['totals']['rows_removed'], 15)
        self.assertEqual(stats['totals']['rows_persisted'], 2085 + 711 + 13661 + 13307 + 967)
