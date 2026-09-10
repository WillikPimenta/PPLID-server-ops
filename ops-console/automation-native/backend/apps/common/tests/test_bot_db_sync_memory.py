# -*- coding: utf-8 -*-
"""Guard de memória para syncs pesados."""
from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.common.bot_db_sync_memory import memory_ok_for_heavy_sync


class BotDbSyncMemoryTests(SimpleTestCase):
    @override_settings(BOT_DB_SYNC_MIN_FREE_MB=2048)
    @patch("apps.common.bot_db_sync_memory.free_memory_mb", return_value=4096.0)
    def test_ok_when_enough_free(self, _mock):
        self.assertTrue(memory_ok_for_heavy_sync())

    @override_settings(BOT_DB_SYNC_MIN_FREE_MB=2048)
    @patch("apps.common.bot_db_sync_memory.free_memory_mb", return_value=100.0)
    def test_false_when_low(self, _mock):
        self.assertFalse(memory_ok_for_heavy_sync())

    @override_settings(BOT_DB_SYNC_MIN_FREE_MB=2048)
    @patch("apps.common.bot_db_sync_memory.free_memory_mb", return_value=None)
    def test_fail_open_when_api_unavailable(self, _mock):
        self.assertTrue(memory_ok_for_heavy_sync())
