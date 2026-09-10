"""Utilitários de automação visual por reconhecimento de imagem (Windows)."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import cv2
import mss
import numpy as np
import pyautogui

log = logging.getLogger(__name__)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15

DEFAULT_CONFIDENCE = 0.85
DEFAULT_TRAY_REGION = (55.0, 92.0, 45.0, 8.0)
DEFAULT_FULL_REGION = (0.0, 0.0, 100.0, 100.0)

REGION_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "tray": DEFAULT_TRAY_REGION,
    "full": DEFAULT_FULL_REGION,
}


@dataclass(frozen=True)
class MatchResult:
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def _parse_region_env(name: str, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        log.warning("Região inválida em %s=%r; usando padrão", name, raw)
        return default
    try:
        return tuple(float(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        log.warning("Região inválida em %s=%r; usando padrão", name, raw)
        return default


def resolve_region(name: str | tuple[float, float, float, float] | None = None) -> tuple[float, float, float, float]:
    if isinstance(name, tuple):
        return name
    key = (name or "tray").lower()
    if key == "tray":
        return _parse_region_env("TRAY_SCAN_REGION", REGION_PRESETS["tray"])
    return REGION_PRESETS.get(key, DEFAULT_FULL_REGION)


def _region_to_pixels(
    region_pct: tuple[float, float, float, float],
    monitor: dict | None = None,
) -> tuple[int, int, int, int]:
    with mss.mss() as sct:
        mon = monitor or sct.monitors[1]
    left = mon["left"] + int(mon["width"] * region_pct[0] / 100.0)
    top = mon["top"] + int(mon["height"] * region_pct[1] / 100.0)
    width = max(1, int(mon["width"] * region_pct[2] / 100.0))
    height = max(1, int(mon["height"] * region_pct[3] / 100.0))
    return left, top, width, height


def capture_region(
    region: str | tuple[float, float, float, float] | None = "tray",
    monitor_index: int = 1,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Captura região da tela. Retorna (BGR array, offset_xy)."""
    region_pct = resolve_region(region)
    with mss.mss() as sct:
        if monitor_index >= len(sct.monitors):
            monitor_index = 1
        mon = sct.monitors[monitor_index]
        left, top, width, height = _region_to_pixels(region_pct, mon)
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    frame = np.array(shot)
    if frame.ndim == 3 and frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame, (left, top)


def _load_template(template_path: Path) -> np.ndarray | None:
    if not template_path.exists():
        log.error("Template não encontrado: %s", template_path)
        return None
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        log.error("Falha ao ler template: %s", template_path)
    return template


def find_template(
    template_path: Path | str,
    region: str | tuple[float, float, float, float] | None = "tray",
    confidence: float = DEFAULT_CONFIDENCE,
    screenshot: np.ndarray | None = None,
    offset: tuple[int, int] | None = None,
) -> MatchResult | None:
    """Localiza template na região usando matchTemplate (TM_CCOEFF_NORMED)."""
    path = Path(template_path)
    template = _load_template(path)
    if template is None:
        return None

    if screenshot is None:
        screenshot, offset = capture_region(region)

    if offset is None:
        offset = (0, 0)

    th, tw = template.shape[:2]
    sh, sw = screenshot.shape[:2]
    if th > sh or tw > sw:
        log.warning("Template maior que a região de busca: %s", path.name)
        return None

    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    if max_val < confidence:
        return None

    x = offset[0] + max_loc[0]
    y = offset[1] + max_loc[1]
    return MatchResult(x=x, y=y, width=tw, height=th, confidence=float(max_val))


def is_template_visible(
    template_path: Path | str,
    region: str | tuple[float, float, float, float] | None = "tray",
    confidence: float = DEFAULT_CONFIDENCE,
    screenshot: np.ndarray | None = None,
    offset: tuple[int, int] | None = None,
) -> bool:
    return (
        find_template(
            template_path,
            region=region,
            confidence=confidence,
            screenshot=screenshot,
            offset=offset,
        )
        is not None
    )


def find_any_template(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    screenshot: np.ndarray | None = None,
    offset: tuple[int, int] | None = None,
) -> tuple[Path, MatchResult] | None:
    """Retorna o melhor match entre vários templates (maior confidence)."""
    if screenshot is None:
        screenshot, offset = capture_region(region)
    best: tuple[Path, MatchResult] | None = None
    for template_path in template_paths:
        path = Path(template_path)
        match = find_template(
            path,
            region=region,
            confidence=confidence,
            screenshot=screenshot,
            offset=offset,
        )
        if match is None:
            continue
        if best is None or match.confidence > best[1].confidence:
            best = (path, match)
    return best


def click_any_template(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    clicks: int = 1,
) -> tuple[Path, MatchResult] | None:
    found = find_any_template(template_paths, region=region, confidence=confidence)
    if found is None:
        return None
    path, match = found
    cx, cy = match.center
    click_at(cx, cy, clicks=clicks)
    log.info(
        "Clique em %s (%.0f%%) em (%s, %s)",
        path.name,
        match.confidence * 100,
        cx,
        cy,
    )
    return found


def wait_for_any_template(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
    stop_check: Callable[[], bool] | None = None,
) -> tuple[Path, MatchResult] | None:
    deadline = time.monotonic() + timeout
    names = ", ".join(Path(p).name for p in template_paths)
    while time.monotonic() < deadline:
        if stop_check and stop_check():
            return None
        found = find_any_template(template_paths, region=region, confidence=confidence)
        if found is not None:
            return found
        time.sleep(poll_interval)
    log.warning("Timeout aguardando templates: %s", names)
    return None


def wait_and_click_any_template(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
    clicks: int = 1,
    stop_check: Callable[[], bool] | None = None,
) -> tuple[Path, MatchResult] | None:
    found = wait_for_any_template(
        template_paths,
        region=region,
        confidence=confidence,
        timeout=timeout,
        poll_interval=poll_interval,
        stop_check=stop_check,
    )
    if found is None:
        return None
    path, match = found
    cx, cy = match.center
    click_at(cx, cy, clicks=clicks)
    log.info("Clique após espera em %s em (%s, %s)", path.name, cx, cy)
    return found


def click_at(x: int, y: int, clicks: int = 1, interval: float = 0.1) -> None:
    pyautogui.click(x=x, y=y, clicks=clicks, interval=interval)


def click_template(
    template_path: Path | str,
    region: str | tuple[float, float, float, float] | None = "tray",
    confidence: float = DEFAULT_CONFIDENCE,
    clicks: int = 1,
) -> MatchResult | None:
    match = find_template(template_path, region=region, confidence=confidence)
    if match is None:
        return None
    cx, cy = match.center
    click_at(cx, cy, clicks=clicks)
    log.info("Clique em %s (%.0f%%) em (%s, %s)", Path(template_path).name, match.confidence * 100, cx, cy)
    return match


def wait_for_template(
    template_path: Path | str,
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
    stop_check: Callable[[], bool] | None = None,
) -> MatchResult | None:
    deadline = time.monotonic() + timeout
    path = Path(template_path)
    while time.monotonic() < deadline:
        if stop_check and stop_check():
            return None
        match = find_template(path, region=region, confidence=confidence)
        if match is not None:
            return match
        time.sleep(poll_interval)
    log.warning("Timeout aguardando template: %s", path.name)
    return None


def wait_and_click_template(
    template_path: Path | str,
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
    clicks: int = 1,
    stop_check: Callable[[], bool] | None = None,
) -> MatchResult | None:
    match = wait_for_template(
        template_path,
        region=region,
        confidence=confidence,
        timeout=timeout,
        poll_interval=poll_interval,
        stop_check=stop_check,
    )
    if match is None:
        return None
    cx, cy = match.center
    click_at(cx, cy, clicks=clicks)
    log.info("Clique após espera em %s em (%s, %s)", Path(template_path).name, cx, cy)
    return match


DEFAULT_MULTISCALE = (0.9, 1.0, 1.1)
DEFAULT_TRAY_MATCH_SCALES = (0.5, 0.65, 0.75, 0.875, 1.0, 1.125, 1.25, 1.5)
DEFAULT_TRAY_BG_TOLERANCE = 25
DEFAULT_TEXT_LUMA_MAX = 100
MatchMode = Literal["color", "grayscale", "text"]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def tray_bg_tolerance() -> int:
    return max(1, _env_int("TRAY_BG_TOLERANCE", DEFAULT_TRAY_BG_TOLERANCE))


def tray_match_scales() -> tuple[float, ...]:
    raw = os.getenv("TRAY_MATCH_SCALES", "").strip()
    if not raw:
        return DEFAULT_TRAY_MATCH_SCALES
    try:
        return tuple(float(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError:
        log.warning("TRAY_MATCH_SCALES inválido; usando padrão")
        return DEFAULT_TRAY_MATCH_SCALES


def _load_template_rgba(template_path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    if not template_path.exists():
        log.error("Template não encontrado: %s", template_path)
        return None
    from PIL import Image

    try:
        img = Image.open(template_path).convert("RGBA")
    except OSError:
        log.error("Falha ao ler template: %s", template_path)
        return None
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    rgba = np.array(img)
    alpha = rgba[:, :, 3]
    mask = np.where(alpha > 128, 255, 0).astype(np.uint8)
    bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
    return bgr, mask


def estimate_taskbar_background(screenshot: np.ndarray) -> np.ndarray:
    """Mediana dos pixels das bordas da captura da bandeja."""
    h, w = screenshot.shape[:2]
    if h < 2 or w < 2:
        return screenshot[0, 0].astype(np.float32)
    border_pixels = np.concatenate(
        [
            screenshot[0, :, :].reshape(-1, 3),
            screenshot[-1, :, :].reshape(-1, 3),
            screenshot[:, 0, :].reshape(-1, 3),
            screenshot[:, -1, :].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(border_pixels, axis=0).astype(np.float32)


def foreground_mask(screenshot: np.ndarray, tolerance: int | None = None) -> np.ndarray:
    bg = estimate_taskbar_background(screenshot)
    diff = np.linalg.norm(screenshot.astype(np.float32) - bg, axis=2)
    tol = tolerance if tolerance is not None else tray_bg_tolerance()
    return (diff > tol).astype(np.uint8) * 255


def prepare_tray_screenshot(screenshot: np.ndarray, tolerance: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Remove fundo uniforme da bandeja antes do match."""
    fg_mask = foreground_mask(screenshot, tolerance=tolerance)
    work = screenshot.copy()
    work[fg_mask == 0] = 0
    return work, fg_mask


def _text_luminance_mask(
    template_bgr: np.ndarray,
    base_mask: np.ndarray,
    max_luma: int = DEFAULT_TEXT_LUMA_MAX,
) -> np.ndarray:
    """Máscara só dos pixels escuros (texto), ignorando cor de destaque do botão."""
    gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    return np.where((gray < max_luma) & (base_mask > 0), 255, 0).astype(np.uint8)


def _apply_match_mode(
    template_bgr: np.ndarray,
    template_mask: np.ndarray,
    screenshot: np.ndarray,
    match_mode: str | MatchMode | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mode = (match_mode or "color").lower()
    if mode == "grayscale":
        template_gray = cv2.cvtColor(cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        screenshot_gray = cv2.cvtColor(cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        return template_gray, template_mask, screenshot_gray
    if mode == "text":
        return template_bgr, _text_luminance_mask(template_bgr, template_mask), screenshot
    return template_bgr, template_mask, screenshot


def _match_on_screenshot_masked(
    template: np.ndarray,
    template_mask: np.ndarray,
    screenshot: np.ndarray,
    offset: tuple[int, int],
    confidence: float,
) -> MatchResult | None:
    th, tw = template.shape[:2]
    sh, sw = screenshot.shape[:2]
    if th > sh or tw > sw:
        return None
    if template_mask.shape[:2] != (th, tw):
        return None
    if int(template_mask.sum()) == 0:
        return None
    try:
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCORR_NORMED, mask=template_mask)
    except cv2.error:
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    if not np.isfinite(max_val):
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    if max_val < confidence:
        return None
    x = offset[0] + max_loc[0]
    y = offset[1] + max_loc[1]
    return MatchResult(x=x, y=y, width=tw, height=th, confidence=float(max_val))


def find_template_masked_multiscale(
    template_bgr: np.ndarray,
    template_mask: np.ndarray,
    screenshot: np.ndarray | None = None,
    offset: tuple[int, int] | None = None,
    confidence: float = DEFAULT_CONFIDENCE,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
    region: str | tuple[float, float, float, float] | None = "tray",
    prepare_tray: bool = True,
    match_mode: str | MatchMode | None = None,
) -> MatchResult | None:
    if screenshot is None:
        screenshot, offset = capture_region(region)
    if offset is None:
        offset = (0, 0)
    work = prepare_tray_screenshot(screenshot)[0] if prepare_tray else screenshot

    best: MatchResult | None = None
    for scale in scales:
        if abs(scale - 1.0) < 0.01:
            scaled = template_bgr
            scaled_mask = template_mask
        else:
            new_w = max(1, int(round(template_bgr.shape[1] * scale)))
            new_h = max(1, int(round(template_bgr.shape[0] * scale)))
            scaled = cv2.resize(template_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            scaled_mask = cv2.resize(template_mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        scaled, scaled_mask, work_mode = _apply_match_mode(scaled, scaled_mask, work, match_mode)
        match = _match_on_screenshot_masked(scaled, scaled_mask, work_mode, offset, confidence)
        if match is None:
            continue
        if best is None or match.confidence > best.confidence:
            best = match
    return best


def find_template_masked_from_path(
    template_path: Path | str,
    screenshot: np.ndarray | None = None,
    offset: tuple[int, int] | None = None,
    confidence: float = DEFAULT_CONFIDENCE,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
    region: str | tuple[float, float, float, float] | None = "tray",
    prepare_tray: bool = True,
    match_mode: str | MatchMode | None = None,
) -> MatchResult | None:
    loaded = _load_template_rgba(Path(template_path))
    if loaded is None:
        return None
    bgr, mask = loaded
    return find_template_masked_multiscale(
        bgr,
        mask,
        screenshot=screenshot,
        offset=offset,
        confidence=confidence,
        scales=scales,
        region=region,
        prepare_tray=prepare_tray,
        match_mode=match_mode,
    )


def find_any_template_masked_multiscale(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
    prepare_tray: bool = False,
    match_mode: str | MatchMode | None = None,
) -> tuple[Path, MatchResult] | None:
    screenshot, offset = capture_region(region)
    work = prepare_tray_screenshot(screenshot)[0] if prepare_tray else screenshot
    best: tuple[Path, MatchResult] | None = None
    for template_path in template_paths:
        path = Path(template_path)
        loaded = _load_template_rgba(path)
        if loaded is None:
            continue
        bgr, mask = loaded
        match = find_template_masked_multiscale(
            bgr,
            mask,
            screenshot=work,
            offset=offset,
            confidence=confidence,
            scales=scales,
            prepare_tray=False,
            match_mode=match_mode,
        )
        if match is None:
            continue
        if best is None or match.confidence > best[1].confidence:
            best = (path, match)
    return best


def click_any_template_masked_multiscale(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    clicks: int = 1,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
    prepare_tray: bool = False,
    match_mode: str | MatchMode | None = None,
) -> tuple[Path, MatchResult] | None:
    found = find_any_template_masked_multiscale(
        template_paths,
        region=region,
        confidence=confidence,
        scales=scales,
        prepare_tray=prepare_tray,
        match_mode=match_mode,
    )
    if found is None:
        return None
    path, match = found
    cx, cy = match.center
    click_at(cx, cy, clicks=clicks)
    log.info(
        "Clique em %s (%.0f%%) em (%s, %s)",
        path.name,
        match.confidence * 100,
        cx,
        cy,
    )
    return found


def wait_and_click_any_template_masked_multiscale(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
    clicks: int = 1,
    stop_check: Callable[[], bool] | None = None,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
    prepare_tray: bool = False,
    match_mode: str | MatchMode | None = None,
) -> tuple[Path, MatchResult] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_check and stop_check():
            return None
        found = click_any_template_masked_multiscale(
            template_paths,
            region=region,
            confidence=confidence,
            clicks=clicks,
            scales=scales,
            prepare_tray=prepare_tray,
            match_mode=match_mode,
        )
        if found is not None:
            return found
        time.sleep(poll_interval)
    names = ", ".join(Path(p).name for p in template_paths)
    log.warning("Timeout aguardando templates mascarados: %s", names)
    return None


def capture_roi(
    center_x: int,
    center_y: int,
    size_px: int,
    monitor_index: int = 1,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Captura quadrado centrado em (center_x, center_y)."""
    half = max(1, size_px // 2)
    with mss.mss() as sct:
        if monitor_index >= len(sct.monitors):
            monitor_index = 1
        mon = sct.monitors[monitor_index]
        left = max(mon["left"], center_x - half)
        top = max(mon["top"], center_y - half)
        right = min(mon["left"] + mon["width"], center_x + half)
        bottom = min(mon["top"] + mon["height"], center_y + half)
        width = max(1, right - left)
        height = max(1, bottom - top)
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    frame = np.array(shot)
    if frame.ndim == 3 and frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame, (left, top)


def _match_on_screenshot(
    template: np.ndarray,
    screenshot: np.ndarray,
    offset: tuple[int, int],
    confidence: float,
) -> MatchResult | None:
    th, tw = template.shape[:2]
    sh, sw = screenshot.shape[:2]
    if th > sh or tw > sw:
        return None
    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    if max_val < confidence:
        return None
    x = offset[0] + max_loc[0]
    y = offset[1] + max_loc[1]
    return MatchResult(x=x, y=y, width=tw, height=th, confidence=float(max_val))


def find_template_multiscale(
    template_path: Path | str,
    screenshot: np.ndarray | None = None,
    offset: tuple[int, int] | None = None,
    confidence: float = DEFAULT_CONFIDENCE,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
    region: str | tuple[float, float, float, float] | None = "full",
) -> MatchResult | None:
    path = Path(template_path)
    template = _load_template(path)
    if template is None:
        return None
    if screenshot is None:
        screenshot, offset = capture_region(region)
    if offset is None:
        offset = (0, 0)

    best: MatchResult | None = None
    for scale in scales:
        if abs(scale - 1.0) < 0.01:
            scaled = template
        else:
            new_w = max(1, int(round(template.shape[1] * scale)))
            new_h = max(1, int(round(template.shape[0] * scale)))
            scaled = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
        match = _match_on_screenshot(scaled, screenshot, offset, confidence)
        if match is None:
            continue
        if best is None or match.confidence > best.confidence:
            best = match
    return best


def _center_within_tolerance(
    match: MatchResult,
    anchor_x: int,
    anchor_y: int,
    tolerance_px: int,
) -> bool:
    cx, cy = match.center
    return abs(cx - anchor_x) <= tolerance_px and abs(cy - anchor_y) <= tolerance_px


def find_template_at_anchor(
    template_path: Path | str,
    anchor_x: int,
    anchor_y: int,
    roi_size_px: int = 32,
    tolerance_px: int = 12,
    confidence: float = 0.0,
    screenshot: np.ndarray | None = None,
    offset: tuple[int, int] | None = None,
    monitor_index: int = 1,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
) -> MatchResult | None:
    """Match no ROI da âncora; rejeita se o centro distar da âncora."""
    if screenshot is None:
        screenshot, offset = capture_roi(anchor_x, anchor_y, roi_size_px, monitor_index)
    if offset is None:
        offset = (0, 0)

    match = find_template_multiscale(
        template_path,
        screenshot=screenshot,
        offset=offset,
        confidence=confidence,
        scales=scales,
    )
    if match is None:
        return None
    if not _center_within_tolerance(match, anchor_x, anchor_y, tolerance_px):
        return None
    return match


def click_anchor(anchor_x: int, anchor_y: int, clicks: int = 1) -> None:
    click_at(anchor_x, anchor_y, clicks=clicks)
    log.info("Clique na âncora (%s, %s)", anchor_x, anchor_y)


def find_any_template_multiscale(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
) -> tuple[Path, MatchResult] | None:
    screenshot, offset = capture_region(region)
    best: tuple[Path, MatchResult] | None = None
    for template_path in template_paths:
        path = Path(template_path)
        match = find_template_multiscale(
            path,
            screenshot=screenshot,
            offset=offset,
            confidence=confidence,
            scales=scales,
        )
        if match is None:
            continue
        if best is None or match.confidence > best[1].confidence:
            best = (path, match)
    return best


def click_any_template_multiscale(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    clicks: int = 1,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
) -> tuple[Path, MatchResult] | None:
    found = find_any_template_multiscale(template_paths, region=region, confidence=confidence, scales=scales)
    if found is None:
        return None
    path, match = found
    cx, cy = match.center
    click_at(cx, cy, clicks=clicks)
    return found


def wait_and_click_any_template_multiscale(
    template_paths: list[Path | str],
    region: str | tuple[float, float, float, float] | None = "full",
    confidence: float = DEFAULT_CONFIDENCE,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
    clicks: int = 1,
    stop_check: Callable[[], bool] | None = None,
    scales: tuple[float, ...] = DEFAULT_MULTISCALE,
) -> tuple[Path, MatchResult] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_check and stop_check():
            return None
        found = click_any_template_multiscale(
            template_paths,
            region=region,
            confidence=confidence,
            clicks=clicks,
            scales=scales,
        )
        if found is not None:
            return found
        time.sleep(poll_interval)
    return None
