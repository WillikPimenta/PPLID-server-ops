from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from apps.escala_flex.services.escala_generation.holiday_calendar import (
    calculated_holidays,
    easter_sunday,
    merge_calculated_holidays,
    normalize_holiday_location,
)
from apps.escala_flex.services.escala_generation.rules import (
    holiday_applies,
    load_holidays_for_month,
)


class HolidayCalendarTests(SimpleTestCase):
    def test_calculates_2026_movable_dates(self):
        self.assertEqual(easter_sunday(2026), date(2026, 4, 5))
        holidays = calculated_holidays(2026)
        by_key = {
            (item.date, normalize_holiday_location(item.location), item.name)
            for item in holidays
        }

        self.assertIn(
            (date(2026, 4, 3), "sao carlos", "Paixão de Cristo"),
            by_key,
        )
        self.assertIn(
            (date(2026, 6, 4), "brasilia", "Corpus Christi"),
            by_key,
        )

    def test_includes_sao_carlos_municipal_holidays(self):
        holidays = calculated_holidays(2026)
        sao_carlos = {
            (item.date, item.name)
            for item in holidays
            if normalize_holiday_location(item.location) == "sao carlos"
        }

        self.assertIn(
            (date(2026, 8, 15), "Nossa Senhora da Babilônia"),
            sao_carlos,
        )
        self.assertIn(
            (date(2026, 11, 4), "Aniversário de São Carlos"),
            sao_carlos,
        )

    def test_includes_brasilia_district_holidays(self):
        holidays = calculated_holidays(2026)
        brasilia = {
            (item.date, item.name)
            for item in holidays
            if normalize_holiday_location(item.location) == "brasilia"
        }

        self.assertIn(
            (date(2026, 4, 21), "Aniversário de Brasília"),
            brasilia,
        )
        self.assertIn(
            (date(2026, 11, 30), "Dia do Evangélico"),
            brasilia,
        )

    def test_normalizes_location_aliases(self):
        self.assertEqual(normalize_holiday_location("São Carlos/SP"), "sao carlos")
        self.assertEqual(normalize_holiday_location("Brasília - DF"), "brasilia")
        self.assertEqual(normalize_holiday_location("Distrito Federal"), "brasilia")
        self.assertEqual(normalize_holiday_location("Nacional"), "")

    def test_merge_preserves_database_rows_without_exact_duplicates(self):
        stored = [
            SimpleNamespace(
                date=date(2026, 8, 15),
                name="Nossa Senhora da Babilônia",
                location="São Carlos/SP",
                holiday_type="municipal",
            )
        ]

        merged = merge_calculated_holidays(stored, 2026)
        matching = [
            item
            for item in merged
            if item.date == date(2026, 8, 15)
            and item.name == "Nossa Senhora da Babilônia"
            and normalize_holiday_location(item.location) == "sao carlos"
        ]

        self.assertEqual(matching, stored)

class HolidayLoadingTests(TestCase):
    def test_empty_dimension_still_loads_sao_carlos_holiday_for_august(self):
        holidays = load_holidays_for_month(date(2026, 8, 1))
        municipal = next(
            item
            for item in holidays
            if item.date == date(2026, 8, 15)
            and item.name == "Nossa Senhora da Babilônia"
        )

        self.assertTrue(holiday_applies(municipal, "São Carlos - SP"))
        self.assertFalse(holiday_applies(municipal, "Brasília - DF"))