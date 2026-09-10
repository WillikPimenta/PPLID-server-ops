# -*- coding: utf-8 -*-
from datetime import date, datetime
import unittest

from django.test import SimpleTestCase
from django.utils import timezone

from apps.rotina_bruto.services.parsers import (
    parse_date,
    parse_date_series,
    parse_datetime,
    parse_datetime_series,
    parse_protocolo,
    parse_tempo_segundos,
)
import pandas as pd


class ParserTests(SimpleTestCase):
    def test_parse_date_iso_and_br(self):
        self.assertEqual(parse_date("2026-06-18"), date(2026, 6, 18))
        self.assertEqual(parse_date("18/06/2026"), date(2026, 6, 18))
        self.assertEqual(parse_date("2026-06-18 14:30:00"), date(2026, 6, 18))

    def test_parse_date_empty(self):
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))

    def test_parse_date_series_matches_parse_date(self):
        raw = ["2026-06-18", "18/06/2026", "", None, "2026-06-18 14:30:00", "invalid"]
        series_out = parse_date_series(pd.Series(raw))
        scalar_out = [parse_date(v) for v in raw]
        self.assertEqual(series_out, scalar_out)

    def test_parse_date_series_empty(self):
        self.assertEqual(parse_date_series(pd.Series([], dtype=object)), [])
        self.assertEqual(parse_date_series(None), [])

    def test_parse_datetime_preserves_time_and_is_aware(self):
        parsed = parse_datetime("2026-06-18 14:30:00")
        assert parsed is not None
        self.assertTrue(timezone.is_aware(parsed))
        local = timezone.localtime(parsed)
        self.assertEqual(local.replace(tzinfo=None), datetime(2026, 6, 18, 14, 30, 0))

    def test_parse_datetime_keeps_aware_input(self):
        aware = timezone.make_aware(datetime(2026, 6, 18, 10, 0, 0), timezone.get_current_timezone())
        parsed = parse_datetime(aware)
        assert parsed is not None
        self.assertTrue(timezone.is_aware(parsed))
        self.assertEqual(parsed, aware)

    def test_parse_datetime_series_matches_parse_datetime(self):
        raw = ["2026-06-18 14:30:00", "18/06/2026 09:15:00", "", None, "invalid"]
        series_out = parse_datetime_series(pd.Series(raw))
        scalar_out = [parse_datetime(v) for v in raw]
        self.assertEqual(series_out, scalar_out)

    def test_parse_protocolo_digits_only(self):
        self.assertEqual(parse_protocolo("123456"), 123456)
        self.assertIsNone(parse_protocolo("A1"))
        self.assertIsNone(parse_protocolo(""))

    def test_parse_tempo_segundos(self):
        self.assertEqual(parse_tempo_segundos(90), 90)
        self.assertEqual(parse_tempo_segundos("90"), 90)
        self.assertEqual(parse_tempo_segundos("0 days 00:01:30"), 90)
        self.assertIsNone(parse_tempo_segundos(""))
        self.assertIsNone(parse_tempo_segundos(None))
