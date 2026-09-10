from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from apps.dimensoes_processos.services.capacity_simulation import (
    calculate_capacity_simulation_comparison,
)


class CapacitySimulationComparisonTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch(
        "apps.dimensoes_processos.services.capacity_simulation."
        "current_capacity_source_fingerprint",
        return_value="source-a",
    )
    @patch(
        "apps.dimensoes_processos.services.capacity_simulation.calculate_capacity_period"
    )
    def test_variants_share_the_same_calculation_context(
        self, calculate_period, _fingerprint
    ):
        calculate_period.side_effect = lambda *_args, **kwargs: {
            "variant": kwargs["manual_overrides"]
        }
        payload = calculate_capacity_simulation_comparison(
            date(2026, 8, 24),
            date(2026, 8, 30),
            manual_overrides={
                "workflows": [{"id_cliente": 1, "id_workflow": 2, "volume": 10}],
                "derivations": [
                    {
                        "id_cliente": 1,
                        "id_workflow": 2,
                        "id_etapa": 3,
                        "percentual": 50,
                    }
                ],
                "metas": [{"id_etapa": 3, "meta_dia": 100}],
            },
        )

        self.assertEqual(calculate_period.call_count, 4)
        contexts = [call.kwargs["calculation_cache"] for call in calculate_period.call_args_list]
        self.assertTrue(all(context is contexts[0] for context in contexts))
        self.assertFalse(calculate_period.call_args_list[0].kwargs["force_live"])
        self.assertTrue(calculate_period.call_args_list[-1].kwargs["force_live"])
        self.assertEqual(
            payload["results"]["full"]["variant"]["metas"],
            [{"id_etapa": 3, "meta_dia": 100}],
        )

    @patch(
        "apps.dimensoes_processos.services.capacity_simulation."
        "current_capacity_source_fingerprint",
        return_value="source-a",
    )
    @patch(
        "apps.dimensoes_processos.services.capacity_simulation.calculate_capacity_period"
    )
    def test_equal_empty_variants_are_evaluated_once(
        self, calculate_period, _fingerprint
    ):
        calculate_period.return_value = {"ok": True}

        payload = calculate_capacity_simulation_comparison(
            date(2026, 8, 24),
            date(2026, 8, 24),
            manual_overrides={},
        )

        self.assertEqual(calculate_period.call_count, 1)
        self.assertIs(payload["results"]["baseline"], payload["results"]["full"])

    @patch(
        "apps.dimensoes_processos.services.capacity_simulation."
        "current_capacity_source_fingerprint",
        return_value="source-a",
    )
    @patch(
        "apps.dimensoes_processos.services.capacity_simulation.calculate_capacity_period",
        return_value={"ok": True},
    )
    def test_reuses_short_lived_cache_by_override_hash(
        self, calculate_period, _fingerprint
    ):
        first = calculate_capacity_simulation_comparison(
            date(2026, 8, 24), date(2026, 8, 24), manual_overrides={}
        )
        second = calculate_capacity_simulation_comparison(
            date(2026, 8, 24), date(2026, 8, 24), manual_overrides={}
        )

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(calculate_period.call_count, 1)
