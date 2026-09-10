from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.escala_flex.services.escala_generation.rules import (
    proposed_work_breaks_consecutive_days,
    proposed_work_breaks_rest,
)


class GenerationCandidateRulesTests(SimpleTestCase):
    def setUp(self):
        self.agent = SimpleNamespace(id="agent-1")

    def entry(self, day: int, value: str):
        return {
            "agent": self.agent,
            "date": date(2026, 8, day),
            "day_value": value,
            "schedule": "11:00 - 17:00",
        }

    def test_rejects_candidate_that_breaks_previous_interjourney(self):
        previous = self.entry(4, "17:00 - 23:00")
        candidate = self.entry(5, "FOLGA")
        self.assertTrue(
            proposed_work_breaks_rest(
                [previous, candidate], candidate, "06:00 - 12:00", 11
            )
        )
        self.assertFalse(
            proposed_work_breaks_rest(
                [previous, candidate], candidate, "11:00 - 17:00", 11
            )
        )

    def test_rejects_candidate_that_breaks_following_interjourney(self):
        candidate = self.entry(5, "FOLGA")
        following = self.entry(6, "06:00 - 12:00")
        self.assertTrue(
            proposed_work_breaks_rest(
                [candidate, following], candidate, "17:00 - 23:00", 11
            )
        )

    def test_rejects_candidate_when_joined_streak_exceeds_limit(self):
        entries = [
            self.entry(1, "11:00 - 17:00"),
            self.entry(2, "11:00 - 17:00"),
            self.entry(3, "FOLGA"),
            self.entry(4, "11:00 - 17:00"),
            self.entry(5, "11:00 - 17:00"),
        ]
        candidate = entries[2]
        self.assertTrue(proposed_work_breaks_consecutive_days(entries, candidate, 4))
        self.assertFalse(proposed_work_breaks_consecutive_days(entries, candidate, 5))