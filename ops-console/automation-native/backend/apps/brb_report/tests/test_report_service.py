# -*- coding: utf-8 -*-
from apps.brb_report.services.report_service import _artifact_label


def test_artifact_label_uses_safe_slug_not_display_name():
    assert _artifact_label("telefonica brasil sa | vivo sa") == "telefonica-brasil-sa-vivo-sa"
    assert _artifact_label("brb") == "brb"
    assert "|" not in _artifact_label("a|b")
    assert "/" not in _artifact_label("a/b")


def test_artifact_label_windows_invalid_chars():
    label = _artifact_label('foo<>:"/\\|?*bar')
    for ch in '<>:"/\\|?*':
        assert ch not in label
