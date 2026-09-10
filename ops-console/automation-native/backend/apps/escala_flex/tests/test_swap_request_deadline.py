"""Testes do prazo para solicitações de troca de escala."""

from datetime import date, datetime

from django.test import SimpleTestCase

from apps.escala_flex.services.swap_request_workflow import min_swap_date, validate_swap_date


class SwapRequestDeadlineTests(SimpleTestCase):
    def test_weekday_until_cutoff_accepts_next_day(self):
        monday_at_cutoff = datetime(2026, 8, 3, 17, 0)
        self.assertEqual(min_swap_date(monday_at_cutoff), date(2026, 8, 4))

    def test_weekday_after_cutoff_requires_two_days(self):
        monday_after_cutoff = datetime(2026, 8, 3, 17, 1)
        self.assertEqual(min_swap_date(monday_after_cutoff), date(2026, 8, 5))

    def test_friday_accepts_saturday_even_after_cutoff(self):
        friday_night = datetime(2026, 8, 7, 23, 59)
        self.assertEqual(min_swap_date(friday_night), date(2026, 8, 8))

    def test_saturday_accepts_only_from_tuesday(self):
        saturday = datetime(2026, 8, 8, 10, 0)
        self.assertEqual(min_swap_date(saturday), date(2026, 8, 11))
        self.assertIsNotNone(validate_swap_date(date(2026, 8, 10), saturday))
        self.assertIsNone(validate_swap_date(date(2026, 8, 11), saturday))

    def test_sunday_accepts_only_from_tuesday(self):
        sunday = datetime(2026, 8, 9, 20, 0)
        self.assertEqual(min_swap_date(sunday), date(2026, 8, 11))
