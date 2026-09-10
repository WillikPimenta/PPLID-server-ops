"""Ícones de bandeja a partir dos templates AppData (seed do repositório)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

# Labels frágeis (legado / nomes de assets antigos) — desconexão só confiável com cluster.
FRAGILE_DISC_LABELS: frozenset[str] = frozenset({
    "alertIcon",
    "AppErrorBlue",
    "QuotaOverLimit_default",
})

ONEDRIVE_STATE_TEMPLATES: dict[str, list[str]] = {
    "connected": ["connected.png"],
    "disconnected": ["disconnected.png"],
    "syncing": ["syncing.png"],
}

CISCO_STATE_TEMPLATES: dict[str, list[str]] = {
    "connected": ["connected.png"],
    "disconnected": ["disconnected.png", "disconnected_alt.png"],
    "syncing": ["syncing.png"],
}

ONEDRIVE_RECOVERY: tuple[str, ...] = (
    "btn_inserir_credenciais.png",
    "btn_inserir_credenciais_alt.png",
    "btn_inserir_credenciais_accent.png",
)
CISCO_RECOVERY: tuple[str, ...] = ("btn_verificar_novamente.png",)

STATE_KIND_MAP = {
    "connected": "conn",
    "syncing": "sync",
    "disconnected": "disc",
}

_TEMPLATE_CACHE: dict[tuple, TrayIconTemplate] = {}


@dataclass(frozen=True)
class TrayIconTemplate:
    bgr: np.ndarray
    mask: np.ndarray
    source_path: Path
    label: str
    state: str
    kind: str


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def tray_icon_target_height() -> int:
    return max(16, _env_int("TRAY_ICON_TARGET_HEIGHT", 48))


def _effective_target_height(native_height: int, target_height: int) -> int:
    """Ícones já no tamanho da bandeja (<=64px) mantêm resolução nativa."""
    if native_height <= 64:
        return native_height
    return target_height


def _state_template_names(service: str) -> dict[str, list[str]]:
    if service == "onedrive":
        return ONEDRIVE_STATE_TEMPLATES
    if service == "cisco":
        return CISCO_STATE_TEMPLATES
    return {}


# Helpers usados apenas por tools/import_install_icons.py (não entram no matching do bot).
DEFAULT_CISCO_DIR = Path(r"C:\Program Files (x86)\Cisco\Cisco Secure Client\UI\res")


def resolve_onedrive_dir(explicit: Path | None = None) -> Path | None:
    base = explicit
    if base is None:
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "OneDrive"
    if not base.is_dir():
        return None
    versions = sorted(
        (p for p in base.iterdir() if p.is_dir() and p.name[:1].isdigit()),
        key=lambda p: p.name,
        reverse=True,
    )
    if versions:
        return versions[0]
    if explicit is not None and explicit.exists():
        return explicit
    return None


def resolve_cisco_dir(explicit: Path | None = None) -> Path | None:
    if explicit is not None and explicit.exists():
        return explicit
    env_raw = os.getenv("CISCO_UI_RES_DIR", "").strip()
    if env_raw:
        path = Path(env_raw)
        if path.is_dir():
            return path
    if DEFAULT_CISCO_DIR.is_dir():
        return DEFAULT_CISCO_DIR
    return None


def list_state_icon_paths(
    service: str,
    state: str,
    *,
    repo_templates_dir: Path | None = None,
    fallback_templates_dir: Path | None = None,
) -> list[Path]:
    """Lista PNGs de estado em AppData (repo_templates_dir). fallback é ignorado."""
    del fallback_templates_dir
    paths: list[Path] = []
    if repo_templates_dir is None or not repo_templates_dir.is_dir():
        return paths
    for rel in _state_template_names(service).get(state, []):
        candidate = repo_templates_dir / rel
        if candidate.is_file() and candidate not in paths:
            paths.append(candidate)
    return paths


def list_recovery_icon_paths(
    service: str,
    *,
    repo_templates_dir: Path | None = None,
    fallback_templates_dir: Path | None = None,
) -> list[Path]:
    del fallback_templates_dir
    rel_names = ONEDRIVE_RECOVERY if service == "onedrive" else CISCO_RECOVERY
    paths: list[Path] = []
    if repo_templates_dir is None or not repo_templates_dir.is_dir():
        return paths
    for rel in rel_names:
        candidate = repo_templates_dir / rel
        if candidate.is_file() and candidate not in paths:
            paths.append(candidate)
    return paths


def _prepare_rgba_image(path: Path, target_height: int) -> tuple[np.ndarray, np.ndarray]:
    img = Image.open(path).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    width, height = img.size
    if height <= 0:
        raise ValueError(f"Imagem inválida: {path}")
    out_height = _effective_target_height(height, target_height)
    scale = out_height / height
    new_size = (max(1, int(round(width * scale))), out_height)
    img = img.resize(new_size, Image.Resampling.LANCZOS)

    rgba = np.array(img)
    alpha = rgba[:, :, 3]
    mask = np.where(alpha > 128, 255, 0).astype(np.uint8)
    bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
    return bgr, mask


def load_tray_icon(path: Path, *, state: str = "", label: str = "") -> TrayIconTemplate | None:
    if not path.is_file():
        return None
    try:
        stat = path.stat()
        if stat.st_size == 0:
            log.warning("Ícone vazio ignorado: %s", path)
            return None
        mtime = stat.st_mtime
    except OSError:
        return None

    target_height = tray_icon_target_height()
    cache_key = (str(path.resolve()), mtime, target_height, state)
    cached = _TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        bgr, mask = _prepare_rgba_image(path, target_height)
    except (OSError, ValueError) as exc:
        log.warning("Falha ao preparar ícone %s: %s", path, exc)
        return None

    kind = STATE_KIND_MAP.get(state, "")
    icon = TrayIconTemplate(
        bgr=bgr,
        mask=mask,
        source_path=path,
        label=label or path.stem,
        state=state,
        kind=kind,
    )
    _TEMPLATE_CACHE[cache_key] = icon
    return icon


def list_state_icons(
    service: str,
    state: str,
    *,
    repo_templates_dir: Path | None = None,
    fallback_templates_dir: Path | None = None,
) -> list[TrayIconTemplate]:
    icons: list[TrayIconTemplate] = []
    for path in list_state_icon_paths(
        service,
        state,
        repo_templates_dir=repo_templates_dir,
        fallback_templates_dir=fallback_templates_dir,
    ):
        icon = load_tray_icon(path, state=state, label=path.stem)
        if icon is not None:
            icons.append(icon)
    return icons


def list_all_state_entries(
    service: str,
    *,
    repo_templates_dir: Path | None = None,
    fallback_templates_dir: Path | None = None,
) -> list[tuple[str, TrayIconTemplate]]:
    entries: list[tuple[str, TrayIconTemplate]] = []
    for state in ("connected", "syncing", "disconnected"):
        for icon in list_state_icons(
            service,
            state,
            repo_templates_dir=repo_templates_dir,
            fallback_templates_dir=fallback_templates_dir,
        ):
            entries.append((icon.kind, icon))
    return entries


def has_minimum_icons(
    service: str,
    *,
    repo_templates_dir: Path | None = None,
    fallback_templates_dir: Path | None = None,
) -> bool:
    return bool(
        list_state_icon_paths(
            service,
            "connected",
            repo_templates_dir=repo_templates_dir,
            fallback_templates_dir=fallback_templates_dir,
        )
    )


def install_status(
    service: str,
    *,
    repo_templates_dir: Path | None = None,
    fallback_templates_dir: Path | None = None,
) -> dict:
    """Status dos templates em AppData (sem pasta de instalação do app)."""
    del fallback_templates_dir
    states = {}
    for state in ("connected", "syncing", "disconnected"):
        paths = list_state_icon_paths(
            service,
            state,
            repo_templates_dir=repo_templates_dir,
        )
        states[state] = [str(p) for p in paths]
    recovery = [
        str(p)
        for p in list_recovery_icon_paths(
            service,
            repo_templates_dir=repo_templates_dir,
        )
    ]
    return {
        "service": service,
        "templates_dir": str(repo_templates_dir) if repo_templates_dir else None,
        "install_base": None,
        "states": states,
        "recovery": recovery,
        "ready": has_minimum_icons(service, repo_templates_dir=repo_templates_dir),
    }


def clear_cache() -> None:
    _TEMPLATE_CACHE.clear()
