"""Teste ao vivo da vigilância de sync infinito do OneDrive (tray_ui).

Executa _handle_syncing_state com detecção visual real na bandeja.
Use enquanto o PC está com sync infinito para validar poll + reinício.

Exemplos:
  python tools/test_sync_watch_live.py
  python tools/test_sync_watch_live.py --window-seconds 60 --poll-seconds 5
  python tools/test_sync_watch_live.py --simulate-restart
  python tools/test_sync_watch_live.py --force
"""
from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.bots.bot_tray_ui as tray
from app.bots.tray_ui_runner import TrayUiBot

_ONEDRIVE = tray._onedrive_bot
assert _ONEDRIVE is not None


def _print_section(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def _build_flow(
    bot: TrayUiBot,
    *,
    poll_seconds: int | None,
    window_seconds: int | None,
) -> dict:
    flow = copy.deepcopy(bot.load_flow())
    sync = flow.setdefault("sync", {})
    if not isinstance(sync, dict):
        sync = {}
        flow["sync"] = sync
    watch = sync.setdefault("watch", {})
    if not isinstance(watch, dict):
        watch = {}
        sync["watch"] = watch
    sync.setdefault("enabled", True)
    sync.setdefault("recovery", {"action": "restart_process"})
    if poll_seconds is not None:
        watch["poll_seconds"] = poll_seconds
    if window_seconds is not None:
        watch["window_seconds"] = window_seconds
    return flow


def _print_watch_config(bot: TrayUiBot, flow: dict) -> None:
    poll = bot._sync_stuck_poll_seconds(flow)
    window = bot._sync_stuck_window_seconds(flow)
    cooldown = bot._sync_restart_cooldown_seconds(flow)
    wait = bot._sync_restart_wait_seconds(flow)
    action = bot._sync_recovery_action(flow)
    enabled = bot._sync_enabled(flow)
    simulate = os.getenv("ONEDRIVE_SIMULATE_RESTART", "").strip() == "1"
    print(f"  enabled:          {enabled}")
    print(f"  recovery_action:  {action}")
    print(f"  poll_seconds:     {poll}")
    print(f"  window_seconds:   {window}")
    print(f"  restart_cooldown: {cooldown}s")
    print(f"  restart_wait:     {wait}s")
    print(f"  simulate_restart: {simulate}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Teste ao vivo da vigilância de sync OneDrive")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=None,
        help="Intervalo entre reavaliações (default: flow.json ou env)",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=None,
        help="Janela antes do reinício (default: 180; use 60 para teste mais rápido)",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=None,
        help="Threshold de match visual (default: flow.json)",
    )
    parser.add_argument(
        "--simulate-restart",
        action="store_true",
        help="Não mata OneDrive.exe — só loga o reinício (ONEDRIVE_SIMULATE_RESTART=1)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Executa vigilância mesmo se o estado atual não for syncing",
    )
    parser.add_argument(
        "--skip-precheck",
        action="store_true",
        help="Pula leitura inicial e countdown",
    )
    args = parser.parse_args()

    if args.simulate_restart:
        os.environ["ONEDRIVE_SIMULATE_RESTART"] = "1"

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    bot = _ONEDRIVE
    tray.parar_event.clear()
    bot._sync_restart_cooldown_until = 0.0

    if not bot.validate_flow(bot.load_flow()):
        print("OneDrive: ícones de instalação ausentes — abortando.")
        return 2

    flow = _build_flow(bot, poll_seconds=args.poll_seconds, window_seconds=args.window_seconds)
    confidence = args.confidence if args.confidence is not None else bot.flow_confidence(flow)

    _print_section("Configuração da vigilância")
    _print_watch_config(bot, flow)

    if not args.skip_precheck:
        _print_section("Estado atual na bandeja")
        bot._begin_cycle_cache()
        try:
            state = bot.resolve_tray_state(flow, confidence)
            records = bot._gather_tray_matches(flow, confidence)
            cluster = bot._pick_best_cluster(
                bot._cluster_records(records, bot._overlap_tolerance_px(flow))
            )
            print(f"  estado: {state}")
            print(f"  scores: {bot._format_tray_scores(flow, confidence, cluster)}")
        finally:
            bot._end_cycle_cache()

        if state != "syncing" and not args.force:
            print()
            print("Estado atual não é 'syncing'. Vigilância não iniciada.")
            print("  --force          executa mesmo assim")
            print("  --window-seconds 60   teste mais curto quando entrar em sync")
            return 1

        if state != "syncing" and args.force:
            print("\n  [--force] Iniciando vigilância apesar do estado:", state)

        print("\n  Iniciando em 5s (Ctrl+C cancela)...")
        for sec in range(5, 0, -1):
            print(f"    {sec}...")
            time.sleep(1)

    statuses: list[str] = []

    def on_status(msg: str) -> None:
        statuses.append(msg)
        print(f"  STATUS|{msg}", flush=True)

    def on_progress(pct: int, msg: str = "") -> None:
        line = f"PROGRESS|{pct}|{msg}"
        statuses.append(line)
        print(f"  {line}", flush=True)

    bot.set_status_callback(on_status)
    bot.set_progress_callback(on_progress)

    _print_section("Vigilância de sync (reavaliação real)")
    started = time.monotonic()
    try:
        ok = bot._handle_syncing_state(flow, confidence)
    except KeyboardInterrupt:
        tray.parar_event.set()
        print("\n  Interrompido pelo usuário.")
        return 130
    elapsed = int(time.monotonic() - started)

    _print_section("Resultado")
    print(f"  concluído em: {elapsed}s")
    print(f"  sucesso: {ok}")
    if os.getenv("ONEDRIVE_SIMULATE_RESTART") == "1":
        print("  (reinício foi SIMULADO — OneDrive.exe não foi encerrado)")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
