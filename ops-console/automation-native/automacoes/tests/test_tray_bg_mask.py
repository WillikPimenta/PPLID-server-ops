"""Testes de fundo da bandeja vs ícone colorido."""
from __future__ import annotations

import numpy as np

import app.infrastructure.screen_automation as sa


def test_foreground_mask_keeps_colored_icon():
    screen = np.zeros((24, 60, 3), dtype=np.uint8)
    screen[:] = (28, 28, 28)
    screen[8:16, 24:32] = (0, 120, 255)
    mask = sa.foreground_mask(screen, tolerance=15)
    assert int(mask[10, 26]) == 255
    assert int(mask[0, 0]) == 0


def test_masked_match_ignores_uniform_background(tmp_path):
    from pathlib import Path
    from PIL import Image

    icon_path = Path(tmp_path) / "icon.png"
    Image.new("RGBA", (12, 12), (255, 128, 0, 255)).save(icon_path, format="PNG")
    loaded = sa._load_template_rgba(icon_path)
    assert loaded is not None
    bgr, mask = loaded

    dark = np.full((30, 80, 3), 32, dtype=np.uint8)
    light = np.full((30, 80, 3), 220, dtype=np.uint8)
    for screen in (dark, light):
        screen[10:22, 34:46] = bgr
        work, _ = sa.prepare_tray_screenshot(screen, tolerance=20)
        match = sa.find_template_masked_multiscale(
            bgr,
            mask,
            screenshot=work,
            offset=(0, 0),
            confidence=0.6,
            prepare_tray=False,
        )
        assert match is not None
        assert match.confidence >= 0.6
