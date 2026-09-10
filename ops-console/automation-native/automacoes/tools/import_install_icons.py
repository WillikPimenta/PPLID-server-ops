"""Importa ícones das pastas de instalação para templates de referência (opcional)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.tray_install_icons import (  # noqa: E402
    resolve_cisco_dir,
    resolve_onedrive_dir,
    tray_icon_target_height,
)
from app.services.tray_templates import get_local_templates_dir, get_repo_templates_dir

# Referência opcional no git — runtime usa só AppData/imagens (seed a partir destes PNGs)
ONEDRIVE_TRAY_SOURCES: dict[str, str] = {
    "connected.png": "AppBlue.png",
}

CISCO_TRAY_SOURCES: dict[str, str] = {
    "connected.png": "vpn_connected.ico",
    "syncing.png": "transition_1.ico",
}

ONEDRIVE_PRINT_SOURCES: dict[str, str] = {}
CISCO_PRINT_SOURCES: dict[str, str] = {}


def _crop_and_resize_tray(src: Path, dst: Path, target_height: int = 48) -> None:
    img = Image.open(src).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    width, height = img.size
    if height <= 0:
        raise ValueError(f"Imagem inválida: {src}")
    scale = target_height / height
    new_size = (max(1, int(round(width * scale))), target_height)
    img = img.resize(new_size, Image.Resampling.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="PNG")


def _import_mapping(
    base_dir: Path,
    dest_dir: Path,
    mapping: dict[str, str],
    *,
    target_height: int,
    skip_existing: bool,
) -> list[Path]:
    written: list[Path] = []
    for dest_name, source_name in mapping.items():
        out = dest_dir / dest_name
        if skip_existing and out.exists():
            print(f"[skip] já existe: {out.name}")
            continue
        src = base_dir / source_name
        if not src.exists():
            print(f"[skip] ausente: {src}")
            continue
        try:
            _crop_and_resize_tray(src, out, target_height=target_height)
            print(f"[ok] {out.name} <- {source_name} ({out.stat().st_size} bytes)")
            written.append(out)
        except Exception as exc:
            print(f"[erro] {source_name}: {exc}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa ícones de instalação para templates tray_ui")
    parser.add_argument("--onedrive-dir", type=Path, default=None)
    parser.add_argument("--cisco-dir", type=Path, default=None)
    parser.add_argument("--target-height", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true", help="Sobrescreve PNGs existentes")
    parser.add_argument(
        "--dest",
        choices=("local", "repo"),
        default="local",
        help="Destino dos PNGs: pasta local por PC (default) ou repositório git",
    )
    args = parser.parse_args()

    onedrive_dir = resolve_onedrive_dir(args.onedrive_dir)
    cisco_dir = resolve_cisco_dir(args.cisco_dir)
    target_height = args.target_height or tray_icon_target_height()

    if onedrive_dir is None:
        print("OneDrive dir: não encontrado")
        return 1
    print(f"OneDrive dir: {onedrive_dir}")
    print(f"Cisco dir: {cisco_dir or 'não encontrado'}")

    if args.dest == "local":
        onedrive_dest = get_local_templates_dir("onedrive")
        cisco_dest = get_local_templates_dir("cisco")
    else:
        onedrive_dest = get_repo_templates_dir("onedrive")
        cisco_dest = get_repo_templates_dir("cisco")
    skip = not args.overwrite

    print(f"Destino: {args.dest}")
    print("OneDrive:")
    _import_mapping(
        onedrive_dir,
        onedrive_dest,
        ONEDRIVE_TRAY_SOURCES,
        target_height=target_height,
        skip_existing=skip,
    )
    if cisco_dir is not None:
        print("\nCisco:")
        _import_mapping(
            cisco_dir,
            cisco_dest,
            CISCO_TRAY_SOURCES,
            target_height=target_height,
            skip_existing=skip,
        )

    print("\nNota: disconnected.png é captura manual da bandeja — não sobrescrito pelo import.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
