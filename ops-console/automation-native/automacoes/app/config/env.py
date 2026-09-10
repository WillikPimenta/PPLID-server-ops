"""Environment variable helpers."""

import os
from pathlib import Path

_LOCAL_ENV_LOADED = False
_AUTOMACOES_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _AUTOMACOES_ROOT.parent
_ENV_FILE_CANDIDATES = (
    _REPO_ROOT / "backend" / ".env",
    _AUTOMACOES_ROOT / ".env",
    _REPO_ROOT / ".env",
)


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        return None
    value = value.strip().strip('"').strip("'")
    return key, value


def _apply_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if not parsed:
            continue
        key, value = parsed
        if not value:
            continue
        if os.environ.get(key, "").strip():
            continue
        os.environ[key] = value


def load_local_env(force: bool = False) -> None:
    """Load .env files into os.environ without overriding non-empty vars."""
    global _LOCAL_ENV_LOADED
    if _LOCAL_ENV_LOADED and not force:
        return
    _LOCAL_ENV_LOADED = True
    for path in _ENV_FILE_CANDIDATES:
        _apply_env_file(path)


def reload_prefixed_env(prefix: str) -> int:
    """Recarrega do .env chaves com o prefixo (sobrescreve os.environ).

    Útil no runner: o processo pai (Django) pode ter DOCDB_* antigos em memória;
    load_local_env() não sobrescreve valores já setados.
    """
    prefix = str(prefix or "")
    if not prefix:
        return 0
    updated = 0
    for path in _ENV_FILE_CANDIDATES:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(raw_line)
            if not parsed:
                continue
            key, value = parsed
            if not key.startswith(prefix) or not value:
                continue
            if os.environ.get(key) != value:
                updated += 1
            os.environ[key] = value
    return updated


def env_bool(name: str, default: bool) -> bool:
    """Convert environment variable to boolean."""
    value = os.getenv(name)
    if value is None:
        return default
    return value not in ("0", "false", "False", "no", "NO")


def env_int(name: str, default: int) -> int:
    """Convert environment variable to integer."""
    return int(os.getenv(name, str(default)))


def env_str(name: str, default: str) -> str:
    """Get environment variable as string with default."""
    return os.getenv(name, default)


def ensure_directory(path: Path, description: str = "") -> Path:
    """Ensure directory exists, create if needed."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return path


# Legacy aliases for backward compatibility
_env_bool = env_bool
_env_int = env_int
_env_str = env_str
_ensure_directory = ensure_directory
