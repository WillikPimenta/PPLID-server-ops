# -*- coding: utf-8 -*-
"""Fontes Unicode para PDF (acentuação pt-BR)."""
from __future__ import annotations

import os
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

PDF_FONT = "PdfSans"
PDF_FONT_BOLD = "PdfSans-Bold"

_REGISTERED = False


def _font_candidates() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    env_regular = (os.environ.get("SUPORTE_CLARO_PDF_FONT") or "").strip()
    env_bold = (os.environ.get("SUPORTE_CLARO_PDF_FONT_BOLD") or "").strip()
    if env_regular and env_bold:
        pairs.append((Path(env_regular), Path(env_bold)))

    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    pairs.extend([
        (win / "arial.ttf", win / "ARIALBD.TTF"),
        (win / "Arial.ttf", win / "Arialbd.ttf"),
    ])

    linux_dirs = [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/TTF"),
    ]
    for base in linux_dirs:
        pairs.append((base / "DejaVuSans.ttf", base / "DejaVuSans-Bold.ttf"))
        pairs.append((base / "LiberationSans-Regular.ttf", base / "LiberationSans-Bold.ttf"))

    return pairs


def ensure_pdf_fonts() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    for regular, bold in _font_candidates():
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont(PDF_FONT, str(regular)))
            pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD, str(bold)))
            _REGISTERED = True
            return
    raise RuntimeError(
        "Nenhuma fonte Unicode encontrada para o PDF. "
        "Defina SUPORTE_CLARO_PDF_FONT e SUPORTE_CLARO_PDF_FONT_BOLD."
    )


def xml_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
