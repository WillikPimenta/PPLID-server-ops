"""Resolução de templates tray_ui: pasta local AppData (seed opcional do repositório)."""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config.paths import TRAY_UI_IMAGENS_DIR

log = logging.getLogger(__name__)

_BOTS_DIR = Path(__file__).resolve().parents[1] / "bots"
_REPO_TEMPLATES_ROOT = _BOTS_DIR / "templates"


@dataclass(frozen=True)
class TemplatesContext:
    service: str
    local_dir: Path
    repo_dir: Path

    @property
    def flow_path(self) -> Path:
        return self.local_dir / "flow.json"

    def resolve_file(self, name: str) -> Path | None:
        candidate = self.local_dir / name
        if candidate.is_file():
            return candidate
        return None


def get_repo_templates_dir(service: str) -> Path:
    return _REPO_TEMPLATES_ROOT / service


def get_local_templates_dir(service: str) -> Path:
    return TRAY_UI_IMAGENS_DIR / service


def seed_templates_from_repo(service: str, *, local_dir: Path | None = None, repo_dir: Path | None = None) -> Path:
    """Garante pasta local e copia do repo apenas arquivos ausentes."""
    local = local_dir or get_local_templates_dir(service)
    repo = repo_dir or get_repo_templates_dir(service)
    local.mkdir(parents=True, exist_ok=True)

    if not repo.is_dir():
        log.warning("Templates do repositório ausentes para %s: %s", service, repo)
        return local

    for src in sorted(repo.iterdir()):
        if not src.is_file():
            continue
        dst = local / src.name
        if dst.exists():
            continue
        try:
            shutil.copy2(src, dst)
            log.info("Template seed %s -> %s", src.name, dst)
        except OSError as exc:
            log.warning("Falha ao copiar template %s: %s", src.name, exc)
    return local


def resolve_templates_context(service: str, *, seed: bool = True) -> TemplatesContext:
    repo_dir = get_repo_templates_dir(service)
    local_dir = get_local_templates_dir(service)
    if seed:
        seed_templates_from_repo(service, local_dir=local_dir, repo_dir=repo_dir)
    else:
        local_dir.mkdir(parents=True, exist_ok=True)
    return TemplatesContext(service=service, local_dir=local_dir, repo_dir=repo_dir)
