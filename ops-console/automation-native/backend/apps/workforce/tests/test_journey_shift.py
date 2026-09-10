from decimal import Decimal

from django.test import SimpleTestCase

from apps.workforce.services.journey_shift import (
    hours_to_hms,
    parse_hms_to_hours,
    resolve_journey_shift,
)


class JourneyShiftHelpersTests(SimpleTestCase):
    def test_resolve_by_start(self):
        self.assertEqual(resolve_journey_shift("00:00 - 06:00"), "Madrugada")
        self.assertEqual(resolve_journey_shift("08:00 - 14:00"), "Matutino")
        self.assertEqual(resolve_journey_shift("08:00 - 17:00"), "Integral")
        self.assertEqual(resolve_journey_shift("13:00 - 19:00"), "Intermediário")
        self.assertEqual(resolve_journey_shift("15:00 - 21:00"), "Vespertino")
        self.assertEqual(resolve_journey_shift("18:00 - 00:00"), "Noturno")
        self.assertEqual(resolve_journey_shift(""), "")

    def test_hms_roundtrip(self):
        self.assertEqual(parse_hms_to_hours("01:30:00"), Decimal("1.50"))
        self.assertEqual(hours_to_hms(Decimal("1.5")), "01:30:00")
        self.assertIsNone(parse_hms_to_hours(""))
        self.assertEqual(hours_to_hms(None), "")
