from django.test import TestCase

from apps.escala_flex.services.schedule_utils import resolve_default_schedule


class ResolveDefaultScheduleTests(TestCase):
    def test_prefers_escala_horario_over_journey_label(self):
        self.assertEqual(
            resolve_default_schedule(horario="17:00 - 23:00", journey="CLT 8h"),
            "17:00 - 23:00",
        )

    def test_falls_back_to_journey_when_horario_empty(self):
        self.assertEqual(
            resolve_default_schedule(horario="", journey="08:00 - 17:00"),
            "08:00 - 17:00",
        )

    def test_normalizes_horario_format(self):
        self.assertEqual(
            resolve_default_schedule(horario="8:00-17:00", journey=""),
            "08:00 - 17:00",
        )
