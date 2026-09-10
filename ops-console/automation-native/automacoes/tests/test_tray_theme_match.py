"""Matching de botões da bandeja com cores de destaque diferentes do Windows."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import app.infrastructure.screen_automation as sa

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "bots" / "templates" / "onedrive"


def _solid_bgr(w: int, h: int, color: tuple[int, int, int]) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def _embed_button(screen: np.ndarray, button_bgr: np.ndarray, alpha: np.ndarray, x: int, y: int) -> None:
    h, w = button_bgr.shape[:2]
    region = screen[y : y + h, x : x + w]
    screen[y : y + h, x : x + w] = np.where(alpha[:, :, None] > 128, button_bgr, region)


@pytest.mark.skipif(
    not (_TEMPLATES_DIR / "btn_inserir_credenciais.png").is_file(),
    reason="template ausente",
)
def test_grayscale_match_finds_purple_accent_button():
    """Template vermelho encontra botão lilás quando usa escalas da bandeja + grayscale."""
    accent_path = _TEMPLATES_DIR / "btn_inserir_credenciais_accent.png"
    if not accent_path.is_file():
        pytest.skip("template accent ausente")

    red_loaded = sa._load_template_rgba(_TEMPLATES_DIR / "btn_inserir_credenciais.png")
    accent_img = Image.open(accent_path).convert("RGBA")
    accent_rgba = np.array(accent_img)
    accent_bgr = cv2.cvtColor(accent_rgba, cv2.COLOR_RGBA2BGR)
    accent_alpha = accent_rgba[:, :, 3]
    ah, aw = accent_bgr.shape[:2]

    screen = _solid_bgr(900, 500, (62, 62, 62))
    place_x, place_y = 300, 180
    _embed_button(screen, accent_bgr, accent_alpha, place_x, place_y)

    bgr, mask = red_loaded
    color_match = sa.find_template_masked_multiscale(
        bgr,
        mask,
        screenshot=screen,
        offset=(0, 0),
        confidence=0.0,
        scales=sa.DEFAULT_MULTISCALE,
        prepare_tray=False,
        match_mode="color",
    )
    gray_match = sa.find_template_masked_multiscale(
        bgr,
        mask,
        screenshot=screen,
        offset=(0, 0),
        confidence=0.65,
        scales=sa.tray_match_scales(),
        prepare_tray=False,
        match_mode="grayscale",
    )
    accent_match = sa.find_template_masked_multiscale(
        accent_bgr,
        np.where(accent_alpha > 128, 255, 0).astype(np.uint8),
        screenshot=screen,
        offset=(0, 0),
        confidence=0.65,
        scales=sa.tray_match_scales(),
        prepare_tray=False,
        match_mode="color",
    )

    expected = (place_x + aw // 2, place_y + ah // 2)

    assert gray_match is not None
    assert gray_match.confidence >= 0.65
    dist_gray = ((gray_match.center[0] - expected[0]) ** 2 + (gray_match.center[1] - expected[1]) ** 2) ** 0.5
    assert dist_gray <= 5

    assert accent_match is not None
    assert accent_match.confidence >= 0.65

    if color_match is not None:
        dist_color = (
            (color_match.center[0] - expected[0]) ** 2 + (color_match.center[1] - expected[1]) ** 2
        ) ** 0.5
        assert dist_color > dist_gray
