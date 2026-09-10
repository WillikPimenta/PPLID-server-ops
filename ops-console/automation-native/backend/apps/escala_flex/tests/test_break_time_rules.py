from django.test import TestCase

from apps.escala_flex.services.break_time_rules import (
    break_exit_bounds,
    validate_break_exit_against_schedule,
)


class BreakTimeRulesTests(TestCase):
    def test_standard_bounds_for_six_hour_shift(self):
        self.assertEqual(
            break_exit_bounds("17:00 - 23:00", extra=False),
            ("18:00", "21:45"),
        )

    def test_extra_bounds_for_six_hour_shift(self):
        self.assertEqual(
            break_exit_bounds("17:00 - 23:00", extra=True, overtime=True),
            ("18:00", "21:00"),
        )

    def test_standard_bounds_for_twelve_to_eighteen(self):
        self.assertEqual(
            break_exit_bounds("12:00 - 18:00", extra=False),
            ("13:00", "16:45"),
        )

    def test_extra_bounds_without_overtime_use_standard_duration(self):
        self.assertEqual(
            break_exit_bounds("17:00 - 23:00", extra=True, overtime=False),
            ("18:00", "21:45"),
        )

    def test_extra_bounds_with_overtime_use_one_hour(self):
        self.assertEqual(
            break_exit_bounds("17:00 - 23:00", extra=True, overtime=True),
            ("18:00", "21:00"),
        )

    def test_rejects_exit_before_minimum(self):
        with self.assertRaises(ValueError):
            validate_break_exit_against_schedule("17:00 - 23:00", "17:30", extra=False)

    def test_rejects_standard_exit_after_maximum(self):
        with self.assertRaises(ValueError):
            validate_break_exit_against_schedule("17:00 - 23:00", "22:00", extra=False)

    def test_accepts_standard_exit_at_maximum(self):
        validate_break_exit_against_schedule("17:00 - 23:00", "21:45", extra=False)

    def test_accepts_extra_exit_at_maximum(self):
        validate_break_exit_against_schedule("17:00 - 23:00", "21:00", extra=True, overtime=True)
