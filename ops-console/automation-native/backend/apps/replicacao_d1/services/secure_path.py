# -*- coding: utf-8 -*-
"""Validação de paths de artefatos bot dentro de roots configuradas."""
from __future__ import annotations

import os
from pathlib import Path


class InsecurePathError(ValueError):
    """Path rejeitado por estar fora da raiz permitida ou ser inválido."""


def _normalize_root(root: Path) -> Path:
    try:
        return root.resolve(strict=False)
    except OSError as exc:
        raise InsecurePathError(f"Raiz inválida: {root}") from exc


def assert_path_under_root(path: Path, root: Path) -> Path:
    """Resolve path e garante que está dentro de root (sem traversal/symlink externo)."""
    if not root:
        raise InsecurePathError("Raiz de artefatos não configurada.")
    root_resolved = _normalize_root(root)
    if not root_resolved.is_dir():
        raise InsecurePathError("Raiz de artefatos não existe ou não é diretório.")

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise InsecurePathError("Arquivo não encontrado na raiz permitida.") from exc
    except OSError as exc:
        raise InsecurePathError("Não foi possível validar o caminho do artefato.") from exc

    root_resolved = _normalize_root(root_resolved)
    resolved = _normalize_root(resolved)

    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise InsecurePathError("Artefato fora da raiz permitida.") from exc

    # UNC não autorizado fora do prefixo da raiz
    if os.name == "nt":
        root_str = str(root_resolved)
        resolved_str = str(resolved)
        if resolved_str.startswith("\\\\") and not resolved_str.lower().startswith(root_str.lower()):
            raise InsecurePathError("UNC não autorizado.")

    return resolved


def sanitize_error_message(message: str) -> str:
    """Remove paths absolutos de mensagens expostas à UI."""
    text = str(message or "")
    if not text:
        return "Operação rejeitada."
    # Oculta drives Windows e UNC
    parts = text.replace("\\", "/").split("/")
    safe_parts = []
    for part in parts:
        if len(part) >= 2 and part[1] == ":":
            safe_parts.append("[path]")
        elif part.startswith("\\\\"):
            safe_parts.append("[path]")
        else:
            safe_parts.append(part)
    out = "/".join(safe_parts)
    if len(out) > 240:
        return out[:237] + "..."
    return out
