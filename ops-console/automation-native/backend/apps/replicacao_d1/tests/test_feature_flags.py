# -*- coding: utf-8 -*-
from django.test import SimpleTestCase, override_settings

from apps.replicacao_d1.feature_flags import (
    dashboard_db_enabled,
    dashboard_no_file_fallback,
    ingestion_enabled,
    optimized_planning_enabled,
    optimized_planning_shadow_enabled,
    reconciliation_enabled,
    rollout_flags_snapshot,
    shadow_mode_enabled,
)


class ReplicacaoD1FeatureFlagsTests(SimpleTestCase):
    @override_settings(
        REPLICACAO_D1_FF_NEW_INGESTION=True,
        REPLICACAO_D1_FF_NEW_RECONCILIATION=False,
        REPLICACAO_D1_FF_DASHBOARD_DB=True,
        REPLICACAO_D1_FF_DASHBOARD_NO_FILES=True,
        REPLICACAO_D1_FF_SHADOW_MODE=False,
        REPLICACAO_D1_FF_OPTIMIZED_PLANNING=True,
        REPLICACAO_D1_FF_OPTIMIZED_PLANNING_SHADOW=False,
    )
    def test_flags_respeitam_settings(self):
        self.assertTrue(ingestion_enabled())
        self.assertFalse(reconciliation_enabled())
        self.assertTrue(dashboard_db_enabled())
        self.assertTrue(dashboard_no_file_fallback())
        self.assertFalse(shadow_mode_enabled())
        self.assertTrue(optimized_planning_enabled())
        self.assertFalse(optimized_planning_shadow_enabled())

    @override_settings(
        REPLICACAO_D1_FF_NEW_INGESTION=True,
        REPLICACAO_D1_FF_NEW_RECONCILIATION=True,
        REPLICACAO_D1_FF_DASHBOARD_DB=True,
        REPLICACAO_D1_FF_DASHBOARD_NO_FILES=False,
        REPLICACAO_D1_FF_SHADOW_MODE=True,
        REPLICACAO_D1_FF_OPTIMIZED_PLANNING=False,
        REPLICACAO_D1_FF_OPTIMIZED_PLANNING_SHADOW=True,
    )
    def test_rollout_snapshot(self):
        snap = rollout_flags_snapshot()
        self.assertEqual(
            snap,
            {
                "new_ingestion": True,
                "new_reconciliation": True,
                "dashboard_db": True,
                "dashboard_no_files": False,
                "shadow_mode": True,
                "optimized_planning": False,
                "optimized_planning_shadow": True,
            },
        )

    def test_optimized_planning_flags_default_to_safe_disabled(self):
        self.assertFalse(optimized_planning_enabled())
        self.assertFalse(optimized_planning_shadow_enabled())
