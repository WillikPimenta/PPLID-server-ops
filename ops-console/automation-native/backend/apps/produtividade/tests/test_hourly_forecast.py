# -*- coding: utf-8 -*-
from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import BreakTime, Schedule
from apps.produtividade.services.hourly_forecast import (
    HOURLY_THRESHOLD,
    break_overlap_minutes,
    build_schedule_context_lookup,
    hourly_forecast_threshold,
    resolve_break_window_minutes,
    threshold_for_agent_hour,
)
from apps.workforce.models import Agent


class HourlyForecastTests(TestCase):
    def setUp(self):
        self.tz = timezone.get_current_timezone()
        self.ref_date = date(2026, 6, 17)
        self.agent = Agent.objects.create(
            full_name="Test Agent",
            user_lan_id="agent01",
        )

    def test_hourly_forecast_threshold_normal_hour(self):
        self.assertEqual(hourly_forecast_threshold(False, 0), HOURLY_THRESHOLD)

    def test_hourly_forecast_threshold_15min_break(self):
        threshold = hourly_forecast_threshold(False, 15)
        expected = round((45 / 60) * HOURLY_THRESHOLD, 2)
        self.assertEqual(threshold, expected)

    def test_hourly_forecast_threshold_overtime_null(self):
        self.assertIsNone(hourly_forecast_threshold(True, 0))
        self.assertIsNone(hourly_forecast_threshold(True, 15))

    def test_break_overlap_minutes_full_hour(self):
        hour = datetime(2026, 6, 17, 12, 0, tzinfo=self.tz)
        overlap = break_overlap_minutes(hour, 12 * 60, 12 * 60 + 15)
        self.assertEqual(overlap, 15)

    def test_break_overlap_minutes_no_overlap(self):
        hour = datetime(2026, 6, 17, 9, 0, tzinfo=self.tz)
        overlap = break_overlap_minutes(hour, 12 * 60, 12 * 60 + 15)
        self.assertEqual(overlap, 0)

    def test_break_overlap_minutes_partial_hour(self):
        hour = datetime(2026, 6, 17, 11, 0, tzinfo=self.tz)
        overlap = break_overlap_minutes(hour, 11 * 60 + 45, 12 * 60)
        self.assertEqual(overlap, 15)

    def test_resolve_break_window_weekday(self):
        bt = BreakTime(agent_lan_id="agent01", week="12:00", weekend="13:00", active=True)
        window = resolve_break_window_minutes(False, self.ref_date, bt)
        self.assertEqual(window, (12 * 60, 12 * 60 + 15))

    def test_threshold_for_agent_hour_with_schedule(self):
        BreakTime.objects.create(
            agent_lan_id="agent01",
            week="12:00",
            weekend="13:00",
            active=True,
        )
        Schedule.objects.create(
            agent=self.agent,
            date=self.ref_date,
            work_schedule="08:00-17:00",
            overtime=False,
        )
        lookup = build_schedule_context_lookup({"agent01"}, {self.ref_date})

        hour_break = datetime(2026, 6, 17, 12, 0, tzinfo=self.tz)
        hour_normal = datetime(2026, 6, 17, 9, 0, tzinfo=self.tz)

        self.assertEqual(
            threshold_for_agent_hour("agent01", hour_break, lookup),
            round((45 / 60) * HOURLY_THRESHOLD, 2),
        )
        self.assertEqual(threshold_for_agent_hour("agent01", hour_normal, lookup), HOURLY_THRESHOLD)

    def test_threshold_for_agent_hour_overtime_null(self):
        BreakTime.objects.create(
            agent_lan_id="agent01",
            week="12:00",
            active=True,
        )
        Schedule.objects.create(
            agent=self.agent,
            date=self.ref_date,
            overtime=True,
        )
        lookup = build_schedule_context_lookup({"agent01"}, {self.ref_date})
        hour = datetime(2026, 6, 17, 9, 0, tzinfo=self.tz)
        self.assertIsNone(threshold_for_agent_hour("agent01", hour, lookup))
