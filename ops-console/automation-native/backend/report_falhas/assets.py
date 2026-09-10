# -*- coding: utf-8 -*-
"""Assets estáticos do relatório (logo embutido no HTML/e-mail)."""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

_BUNDLED_LOGO = _PKG_DIR / 'assets' / 'serasa_logo.png'

LOGO_KNOWN_FILENAMES = ('serasa_logo.png',)


def _logo_candidate_paths() -> tuple[Path, ...]:
    """Ordem: env > asset versionado > pasta de saída do relatório (legado)."""
    candidates: list[Path] = []
    env_path = (os.environ.get('REPORT_LOGO_PATH') or '').strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(_BUNDLED_LOGO)
    try:
        from report_falhas.config_report import resolve_output_dir
        candidates.append(resolve_output_dir() / 'serasa_logo.png')
    except Exception:
        pass
    return tuple(candidates)


def resolve_logo_path() -> Path | None:
    """Logo branco Serasa Experian (fundo azul do ABAS). Não usa logos do portal."""
    for candidate in _logo_candidate_paths():
        if candidate.is_file():
            return candidate
    return None


def _mime_for_path(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and guessed.startswith('image/'):
        return guessed
    return 'image/png'


def resolve_logo_data_uri() -> str:
    """Retorna data URI do logo para embutir no HTML (anexo autocontido)."""
    path = resolve_logo_path()
    if not path:
        return ''
    b64 = base64.b64encode(path.read_bytes()).decode('ascii')
    mime = _mime_for_path(path)
    return f'data:{mime};base64,{b64}'


def read_logo_bytes() -> tuple[bytes, str] | None:
    """Bytes + MIME do logo (para CID no mailer)."""
    path = resolve_logo_path()
    if not path:
        return None
    return path.read_bytes(), _mime_for_path(path)
