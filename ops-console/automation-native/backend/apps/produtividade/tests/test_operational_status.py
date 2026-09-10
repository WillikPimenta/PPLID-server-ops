# -*- coding: utf-8 -*-
"""Testes do status operacional intradiário (plano de correção)."""
from django.test import SimpleTestCase

from apps.produtividade.services.meta_context import META_COMPLIANCE, META_FRAUD, META_MISTA
from apps.produtividade.services.operational_status import (
    ATTENTION_MIN,
    BEHIND_MIN,
    CRITICAL_MIN_PRODUCTIVE_SECONDS,
    ON_TRACK_MIN,
    STATUS_METHODOLOGY_VERSION,
    WARMUP_SECONDS,
    classify_operational_status,
    compute_pace_expected_pct,
    is_never_critical,
    operational_priority_rank,
)


class OperationalStatusClassificationTests(SimpleTestCase):
    def test_ten_minutes_no_production_is_warming_up_not_critical(self):
        result = classify_operational_status(
            applied_meta=META_FRAUD,
            pace_actual_pct=0.0,
            productive_logged_seconds=10 * 60,
            has_login=True,
            has_production=False,
        )
        self.assertEqual(result.operational_status, "warming_up")
        self.assertTrue(is_never_critical(result.operational_status))
        self.assertIsNone(result.projected_closing_pct)
        self.assertEqual(result.status_methodology_version, STATUS_METHODOLOGY_VERSION)

    def test_fraud_30min_on_expected_is_on_track(self):
        # Após warmup: 30 min exatos ainda é warming_up (< 30min exclusivo).
        # Usar 31 min para sair do aquecimento.
        elapsed = WARMUP_SECONDS + 60
        expected = compute_pace_expected_pct(META_FRAUD, elapsed)
        result = classify_operational_status(
            applied_meta=META_FRAUD,
            pace_actual_pct=expected,
            productive_logged_seconds=elapsed,
        )
        self.assertEqual(result.operational_status, "on_track")
        self.assertAlmostEqual(result.pace_attainment_pct, 100.0, places=1)

    def test_two_hours_below_daily_meta_but_on_pace(self):
        elapsed = 2 * 3600
        expected = compute_pace_expected_pct(META_FRAUD, elapsed)
        # Acumulado bem abaixo de 92%, mas igual ao esperado do horário.
        self.assertLess(expected, META_FRAUD)
        result = classify_operational_status(
            applied_meta=META_FRAUD,
            pace_actual_pct=expected,
            productive_logged_seconds=elapsed,
        )
        self.assertEqual(result.operational_status, "on_track")

    def test_attainment_90_is_attention(self):
        elapsed = 3600
        expected = compute_pace_expected_pct(META_MISTA, elapsed)
        actual = expected * 0.90
        result = classify_operational_status(
            applied_meta=META_MISTA,
            pace_actual_pct=actual,
            productive_logged_seconds=elapsed,
        )
        self.assertEqual(result.operational_status, "attention")
        self.assertGreaterEqual(result.pace_attainment_pct, ATTENTION_MIN)
        self.assertLess(result.pace_attainment_pct, ON_TRACK_MIN)

    def test_attainment_60_without_persistence_is_behind(self):
        elapsed = CRITICAL_MIN_PRODUCTIVE_SECONDS
        expected = compute_pace_expected_pct(META_MISTA, elapsed)
        actual = expected * 0.60
        result = classify_operational_status(
            applied_meta=META_MISTA,
            pace_actual_pct=actual,
            productive_logged_seconds=elapsed,
            consecutive_critical_windows=0,
        )
        self.assertEqual(result.operational_status, "behind")
        self.assertLess(result.pace_attainment_pct, BEHIND_MIN)

    def test_critical_requires_persistence(self):
        elapsed = CRITICAL_MIN_PRODUCTIVE_SECONDS
        expected = compute_pace_expected_pct(META_MISTA, elapsed)
        actual = expected * 0.60
        result = classify_operational_status(
            applied_meta=META_MISTA,
            pace_actual_pct=actual,
            productive_logged_seconds=elapsed,
            consecutive_critical_windows=2,
        )
        self.assertEqual(result.operational_status, "critical")

    def test_recovery_after_bad_window_is_on_track(self):
        elapsed = CRITICAL_MIN_PRODUCTIVE_SECONDS
        expected = compute_pace_expected_pct(META_FRAUD, elapsed)
        result = classify_operational_status(
            applied_meta=META_FRAUD,
            pace_actual_pct=expected,
            productive_logged_seconds=elapsed,
            consecutive_critical_windows=0,
        )
        self.assertEqual(result.operational_status, "on_track")

    def test_contextual_metas(self):
        for meta in (META_FRAUD, META_MISTA, META_COMPLIANCE):
            elapsed = 3600
            expected = compute_pace_expected_pct(meta, elapsed)
            result = classify_operational_status(
                applied_meta=meta,
                pace_actual_pct=expected,
                productive_logged_seconds=elapsed,
            )
            self.assertEqual(result.applied_meta, meta)
            self.assertEqual(result.operational_status, "on_track")

    def test_unknown_operation_no_fallback(self):
        result = classify_operational_status(
            applied_meta=None,
            pace_actual_pct=50.0,
            productive_logged_seconds=3600,
        )
        self.assertEqual(result.operational_status, "unknown")
        self.assertEqual(result.status_phase, "unavailable")

    def test_data_delayed_never_critical(self):
        result = classify_operational_status(
            applied_meta=META_MISTA,
            pace_actual_pct=10.0,
            productive_logged_seconds=3600,
            data_delayed=True,
        )
        self.assertEqual(result.operational_status, "data_delayed")
        self.assertTrue(is_never_critical(result.operational_status))

    def test_final_states(self):
        cases = [
            (100.0, "final_on_target"),
            (97.0, "final_near"),
            (85.0, "final_below"),
            (70.0, "final_critical"),
        ]
        for final_pct, expected_status in cases:
            # final_attainment = final_pct / meta * 100
            # For meta 95: 100% actual → ~105 attainment on_target;
            # Use absolute final comparison via final_actual vs applied_meta ratio.
            result = classify_operational_status(
                applied_meta=META_MISTA,
                shift_closed=True,
                final_actual_pct=final_pct * META_MISTA / 100.0,
            )
            self.assertEqual(
                result.operational_status,
                expected_status,
                msg=f"final={final_pct}",
            )

    def test_not_started(self):
        result = classify_operational_status(
            applied_meta=META_FRAUD,
            has_login=False,
            has_production=False,
            productive_logged_seconds=0,
        )
        self.assertEqual(result.operational_status, "not_started")
        self.assertEqual(result.status_phase, "pre_shift")

    def test_priority_rank_warming_up_not_urgent(self):
        self.assertGreater(
            operational_priority_rank("warming_up"),
            operational_priority_rank("critical"),
        )
        self.assertGreater(
            operational_priority_rank("warming_up"),
            operational_priority_rank("behind"),
        )

    def test_expected_capped_at_meta(self):
        from apps.produtividade.services.operational_status import PRODUCTIVE_DAY_SECONDS

        expected = compute_pace_expected_pct(META_FRAUD, PRODUCTIVE_DAY_SECONDS)
        self.assertEqual(expected, META_FRAUD)
        self.assertEqual(PRODUCTIVE_DAY_SECONDS, 19800)
