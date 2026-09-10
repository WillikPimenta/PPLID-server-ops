"""Testes de matching mascarado e remoção de fundo da bandeja."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import app.infrastructure.screen_automation as sa


def _solid_bgr(w: int, h: int, color: tuple[int, int, int]) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def _write_rgba_icon(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    Image.new("RGBA", size, color).save(path, format="PNG")


def test_prepare_tray_screenshot_zeros_background():
    screen = _solid_bgr(40, 20, (30, 30, 30))
    screen[8:14, 18:26] = (0, 0, 255)
    work, fg = sa.prepare_tray_screenshot(screen, tolerance=10)
    assert fg.shape == (20, 40)
    assert work[0, 0].tolist() == [0, 0, 0]
    assert work[10, 20].tolist() == [0, 0, 255]


def test_find_template_masked_multiscale_picks_icon(tmp_path: Path):
    icon_path = tmp_path / "icon.png"
    _write_rgba_icon(icon_path, (10, 10), (255, 0, 0, 255))
    loaded = sa._load_template_rgba(icon_path)
    assert loaded is not None
    bgr, mask = loaded
    screen = _solid_bgr(50, 30, (25, 25, 25))
    screen[10:20, 20:30] = bgr
    work, _ = sa.prepare_tray_screenshot(screen)
    match = sa.find_template_masked_multiscale(
        bgr,
        mask,
        screenshot=work,
        offset=(0, 0),
        confidence=0.7,
        prepare_tray=False,
    )
    assert match is not None
    assert match.confidence >= 0.7


def test_find_template_masked_from_path(tmp_path: Path):
    icon_path = tmp_path / "t.png"
    _write_rgba_icon(icon_path, (8, 8), (0, 255, 0, 255))
    screen = _solid_bgr(40, 40, (20, 20, 20))
    loaded = sa._load_template_rgba(icon_path)
    assert loaded is not None
    bgr, _mask = loaded
    screen[15:23, 15:23] = bgr
    work, _ = sa.prepare_tray_screenshot(screen)
    match = sa.find_template_masked_from_path(
        icon_path,
        screenshot=work,
        offset=(0, 0),
        confidence=0.6,
        prepare_tray=False,
    )
    assert match is not None


def test_find_template_multiscale_picks_best(tmp_path: Path):
    template = _solid_bgr(10, 10, (255, 0, 0))
    template_path = tmp_path / "t.png"
    cv2.imwrite(str(template_path), template)
    base = _solid_bgr(40, 40, (0, 0, 0))
    base[15:25, 15:25] = template
    match = sa.find_template_multiscale(template_path, screenshot=base, offset=(0, 0), confidence=0.8)
    assert match is not None
    assert match.confidence >= 0.8
