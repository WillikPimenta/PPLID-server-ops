# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ALLOWED_PREFIXES = ("documentation/", "backend/scripts/")
ALLOWED_SUFFIXES = {".md", ".json", ".txt", ".py"}

CONTENT_TYPES = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".txt": "text/plain",
    ".py": "text/x-python",
}


def resolve_repo_doc(relative_path: str) -> Path:
    rel = relative_path.replace("\\", "/").strip()
    if not rel or ".." in rel.split("/"):
        raise ValueError("Caminho inválido.")
    if not any(rel.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        raise ValueError("Apenas arquivos em documentation/ ou backend/scripts/.")
    full = (REPO_ROOT / rel).resolve()
    root = REPO_ROOT.resolve()
    if not str(full).startswith(str(root)):
        raise ValueError("Caminho fora do repositório.")
    if not full.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {rel}")
    if full.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"Tipo não permitido: {full.suffix}")
    return full


def read_doc_text(relative_path: str) -> tuple[str, str, str]:
    path = resolve_repo_doc(relative_path)
    content = path.read_text(encoding="utf-8")
    content_type = CONTENT_TYPES.get(path.suffix.lower(), "text/plain")
    return content, path.name, content_type
