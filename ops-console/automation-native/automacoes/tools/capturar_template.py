"""Ferramenta interativa para capturar templates de imagem no PC local."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import mss
import numpy as np
import pyautogui

from app.infrastructure.screen_automation import capture_region, resolve_region
from app.services.tray_templates import get_local_templates_dir

DEFAULT_SIZE = 48


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Captura recortes de tela para templates de automação visual.",
    )
    parser.add_argument(
        "--service",
        choices=("onedrive", "cisco"),
        default="onedrive",
        help="Serviço da bandeja (define pasta local em APPDATA/imagens)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Pasta de destino dos PNGs (default: APPDATA/PLAN_IDF_SERASA_BOTS/imagens/{service})",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=DEFAULT_SIZE,
        help="Tamanho final do quadrado (px)",
    )
    parser.add_argument(
        "--region",
        choices=("tray", "full"),
        default="tray",
        help="Região de preview inicial",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="",
        help="Nome do arquivo (sem .png). Se vazio, pede no terminal.",
    )
    return parser.parse_args()


def _capture_around_cursor(size: int) -> tuple[np.ndarray, tuple[int, int]]:
    x, y = pyautogui.position()
    half = size // 2
    with mss.mss() as sct:
        mon = sct.monitors[1]
        left = max(mon["left"], x - half)
        top = max(mon["top"], y - half)
        width = min(size, mon["width"])
        height = min(size, mon["height"])
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    frame = np.array(shot)
    if frame.ndim == 3 and frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    resized = cv2.resize(frame, (size, size), interpolation=cv2.INTER_LANCZOS4)
    return resized, (left, top)


def _show_preview(region: str) -> None:
    frame, _offset = capture_region(region)
    scale = 2 if region == "tray" else 1
    preview = cv2.resize(frame, (frame.shape[1] * scale, frame.shape[0] * scale), interpolation=cv2.INTER_NEAREST)
    cv2.imshow(f"Preview região {region} (feche com qualquer tecla)", preview)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir or get_local_templates_dir(args.service)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Captura de templates — {args.service} UI")
    print(f"Saída: {output_dir}")
    print(f"Tamanho: {args.size}x{args.size}px")
    print(f"Região preview: {resolve_region(args.region)}")
    print()
    print("Instruções:")
    print("  1. Posicione o mouse sobre o ícone/botão desejado")
    print("  2. Pressione ENTER para capturar (Ctrl+C para sair)")
    print("  3. Opcional: digite 'preview' para ver a região da bandeja")
    print()

    while True:
        try:
            raw = input("Nome do arquivo (ou 'preview' / 'sair'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if raw.lower() in ("sair", "exit", "quit", "q"):
            return 0

        if raw.lower() == "preview":
            _show_preview(args.region)
            continue

        name = raw or args.name
        if not name:
            print("Informe um nome para o arquivo.")
            continue

        if not name.lower().endswith(".png"):
            name = f"{name}.png"

        print("Posicione o mouse e pressione ENTER...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        frame, origin = _capture_around_cursor(args.size)
        out_path = output_dir / name
        cv2.imwrite(str(out_path), frame)
        x, y = pyautogui.position()
        print(f"Salvo: {out_path} (cursor em {x},{y}, origem {origin[0]},{origin[1]})")
        print()


if __name__ == "__main__":
    raise SystemExit(main())
