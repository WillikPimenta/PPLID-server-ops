# -*- coding: utf-8 -*-
from datetime import date

from django.test import SimpleTestCase

from apps.falhas_criticas.services.dataframes import resolve_portal_comparativo_window


class PortalComparativoWindowTests(SimpleTestCase):
    def test_mtd_same_month_day1(self):
        prev_start, prev_end, mode = resolve_portal_comparativo_window(
            date(2026, 6, 1), date(2026, 6, 25),
        )
        self.assertEqual(mode, 'mtd')
        self.assertEqual(prev_start, date(2026, 5, 1))
        self.assertEqual(prev_end, date(2026, 5, 25))

    def test_custom_multi_month_uses_preceding_days(self):
        # 01/06 → 30/07 = 60 dias; anterior = 02/04 → 31/05
        prev_start, prev_end, mode = resolve_portal_comparativo_window(
            date(2026, 6, 1), date(2026, 7, 30),
        )
        self.assertEqual(mode, 'preceding')
        self.assertEqual(prev_end, date(2026, 5, 31))
        self.assertEqual(prev_start, date(2026, 4, 2))
        self.assertNotEqual(prev_start, date(2026, 6, 1))
        self.assertNotEqual(prev_end, date(2026, 7, 30))

    def test_custom_mid_month_uses_preceding(self):
        prev_start, prev_end, mode = resolve_portal_comparativo_window(
            date(2026, 6, 10), date(2026, 6, 20),
        )
        self.assertEqual(mode, 'preceding')
        self.assertEqual(prev_end, date(2026, 6, 9))
        self.assertEqual(prev_start, date(2026, 5, 30))
