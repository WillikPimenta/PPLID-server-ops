from django.test import SimpleTestCase

from apps.dimensoes_processos.services.capacity_dax_response import build_capacity_comparative


class CapacityDaxResponseTests(SimpleTestCase):
    def test_build_comparative_does_not_subtract_incompatible_units(self):
        legacy = {
            "summary": {"agents_required": 419},
            "hourly": {
                "by_hour": [
                    {"hour": 13, "agents_required": 120},
                    {"hour": 14, "agents_required": 80},
                ]
            },
        }
        dax = {
            "summary": {
                "capacity_esperada_total": "512.3400",
                "capacity_recebida_total": "401.1200",
                "peak_hour_esperada": 13,
                "peak_capacity_esperada": "48.5600",
            }
        }

        comparative = build_capacity_comparative(legacy_payload=legacy, dax_payload=dax)

        self.assertEqual(comparative["legacy"]["agents_required"], 419)
        self.assertEqual(comparative["legacy"]["peak_hourly_agents"], 120)
        self.assertEqual(comparative["dax"]["peak_hour"], 13)
        self.assertNotIn("delta", comparative)
        self.assertFalse(comparative["comparison"]["comparable"])
        self.assertIsNone(comparative["comparison"]["total_delta"])
        self.assertEqual(
            comparative["units"]["dax_capacity_esperada_total"],
            "agent_hours_equivalent",
        )
