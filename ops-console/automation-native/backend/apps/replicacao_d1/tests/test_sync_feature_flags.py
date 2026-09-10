# -*- coding: utf-8 -*-
from django.test import SimpleTestCase, override_settings

from apps.replicacao_d1.feature_flags import reconciliation_enabled


class ReconciliationFlagSyncTests(SimpleTestCase):
    @override_settings(REPLICACAO_D1_FF_NEW_RECONCILIATION=False)
    def test_reconciliation_disabled_by_flag(self):
        self.assertFalse(reconciliation_enabled())
