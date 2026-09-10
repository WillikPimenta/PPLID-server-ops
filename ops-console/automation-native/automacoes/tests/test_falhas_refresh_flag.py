# -*- coding: utf-8 -*-
from app.bots.falhas_criticas.orchestration import _should_skip_refresh


def test_skip_refresh_when_falhas_refresh_queries_false():
    assert _should_skip_refresh({"falhas_refresh_queries": False}) is True


def test_run_refresh_when_falhas_refresh_queries_true():
    assert _should_skip_refresh({"falhas_refresh_queries": True}) is False


def test_default_refresh_on_when_key_missing():
    assert _should_skip_refresh({}) is False


def test_legacy_skip_refresh_queries_key():
    assert _should_skip_refresh({"skip_refresh_queries": True}) is True
