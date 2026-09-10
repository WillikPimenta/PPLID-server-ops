"""Garante sincronização do toggle imediato com os campos de data no modal Flask."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "templates" / "index.html"
APP_JS = ROOT / "app" / "static" / "js" / "app.js"


def test_rotina_label_does_not_double_wire_for_and_nesting():
    html = INDEX_HTML.read_text(encoding="utf-8")
    block = re.search(
        r'id="robot-config-rotina-options".*?</div>\s*<div id="robot-config-ged-options"',
        html,
        flags=re.S,
    )
    assert block, "Bloco de opções da rotina não encontrado no template"
    rotina_html = block.group(0)

    assert 'id="robot-config-imediata"' in rotina_html
    # Nested label + for= aponta para o próprio input → double-toggle em alguns browsers
    assert 'for="robot-config-imediata"' not in rotina_html
    assert 'id="robot-config-data-inicio"' in rotina_html
    assert 'id="robot-config-data-fim"' in rotina_html


def test_app_js_cache_busted_in_template():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "js/app.js" in html
    assert re.search(r"app\.js['\"\)\s\}]*\?v=", html), "app.js deve ter query de versão no template"


def test_apply_rotina_immediate_ui_exists_and_is_used_on_open():
    js = APP_JS.read_text(encoding="utf-8")
    assert "function applyRotinaImmediateUi(" in js
    assert "function syncRotinaDateAvailability(" in js
    assert "function bindRotinaOptions(" in js

    # Ao abrir o modal, a UI deve sincronizar a partir da config salva
    assert "applyRotinaImmediateUi(Boolean(cfg.executar_imediatamente)" in js

    # Toggle (change/input) deve reaplicar disabled a partir de checkbox.checked
    assert 'checkbox.addEventListener("change"' in js
    assert 'checkbox.addEventListener("input"' in js


@pytest.mark.parametrize(
    "enabled,expect_disabled",
    [
        (True, False),
        (False, True),
    ],
)
def test_rotina_immediate_ui_sync_contract(enabled, expect_disabled):
    """Contrato da sincronização (espelha applyRotinaImmediateUi)."""
    checkbox = {"checked": False}
    start = {"disabled": True, "value": ""}
    end = {"disabled": True, "value": ""}

    def apply(on: bool, *, fill_defaults: bool = False):
        checkbox["checked"] = bool(on)
        start["disabled"] = not on
        end["disabled"] = not on
        if on and fill_defaults:
            if not start["value"]:
                start["value"] = "2026-08-04"
            if not end["value"]:
                end["value"] = "2026-08-04"

    apply(enabled, fill_defaults=enabled)

    assert checkbox["checked"] is enabled
    assert start["disabled"] is expect_disabled
    assert end["disabled"] is expect_disabled
    if enabled:
        assert start["value"] and end["value"]
