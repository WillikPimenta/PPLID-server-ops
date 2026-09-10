"""Carrega variáveis de ambiente do backend em dev local e releases de deploy."""

from __future__ import annotations

import os
from pathlib import Path


def _deploy_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("PPLID_BASE_DIR", "PPLID_DEPLOY_ROOT"):
        value = (os.environ.get(key) or "").strip()
        if value:
            roots.append(Path(value))
    roots.append(Path("C:/PPLID"))
    return roots


def _profile_from_path(base_dir: Path) -> str:
    parts = base_dir.resolve().parts
    for idx, part in enumerate(parts):
        if part.lower() == "deploy" and idx + 1 < len(parts):
            candidate = parts[idx + 1].upper()
            if candidate in {"MAIN", "DEV", "HOM"}:
                return candidate
    return ""


def _apply_env_file(path: Path, *, overwrite: bool) -> None:
    """Carrega KEY=VALUE em os.environ tolerando UTF-8 BOM (comum em arquivos do Windows)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        if overwrite or key not in os.environ:
            os.environ[key] = value


def bootstrap_environ(base_dir: Path) -> None:
    """Usa o shared autoritativo e completa apenas chaves ausentes pelo ativo."""
    profile = (os.environ.get("PPLID_ENVIRONMENT") or "").strip().upper()
    if not profile:
        profile = _profile_from_path(base_dir)
    if not profile:
        local_env = base_dir / ".env"
        if local_env.is_file():
            _apply_env_file(local_env, overwrite=True)
        return

    explicit_loaded = False
    explicit = (os.environ.get("PPLID_BACKEND_ENV_FILE") or "").strip()
    if explicit:
        explicit_path = Path(explicit)
        if explicit_path.is_file():
            _apply_env_file(explicit_path, overwrite=True)
            explicit_loaded = True

    for root in _deploy_roots():
        shared_env = root / "deploy" / profile / "shared" / "backend.env"
        active_env = root / "deploy" / profile / "current" / "backend" / ".env"
        loaded = explicit_loaded
        if not explicit_loaded and shared_env.is_file():
            _apply_env_file(shared_env, overwrite=True)
            loaded = True
        if active_env.is_file():
            _apply_env_file(active_env, overwrite=False)
            loaded = True
        if loaded:
            return
