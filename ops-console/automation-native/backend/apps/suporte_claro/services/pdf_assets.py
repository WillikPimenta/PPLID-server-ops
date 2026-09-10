# -*- coding: utf-8 -*-
"""Assets estaticos do relatorio PDF Suporte Claro."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

_APP_DIR = Path(__file__).resolve().parent.parent
_EXPERIAN_LOGO = _APP_DIR / "static" / "suporte_claro" / "experian_logo.png"
_LEGACY_LOGO = _APP_DIR / "static" / "suporte_claro" / "serasa_logo.png"
_FALLBACK_LOGO = (
    Path(__file__).resolve().parents[3] / "report_falhas" / "assets" / "serasa_logo.png"
)


def resolve_serasa_logo_path() -> Path | None:
    """Logo Serasa Experian para cabecalho do PDF."""
    env_path = (os.environ.get("SUPORTE_CLARO_LOGO_PATH") or "").strip()
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate
    for candidate in (_EXPERIAN_LOGO, _LEGACY_LOGO, _FALLBACK_LOGO):
        if candidate.is_file():
            return candidate
    return None


def prepare_logo_for_pdf(source: Path) -> Path:
    """Remove fundo preto/opaco e retorna PNG com transparencia para o ReportLab."""
    cached = source.parent / f"{source.stem}_pdf.png"
    if cached.is_file() and cached.stat().st_mtime >= source.stat().st_mtime:
        return cached

    img = Image.open(source).convert("RGBA")
    pixels = img.load()
    width, height = img.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if r < 45 and g < 45 and b < 45:
                pixels[x, y] = (r, g, b, 0)

    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    img.save(cached, "PNG")
    return cached
