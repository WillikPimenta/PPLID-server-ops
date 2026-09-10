"""Teste ao vivo do bot unificado da bandeja (OneDrive + Cisco)."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.bots.bot_tray_ui as tray
import app.infrastructure.screen_automation as sa
from app.bots.tray_ui_runner import TrayUiBot
from app.infrastructure.cisco_vpn import get_vpn_state
from app.services import tray_install_icons as install_icons


def _print_section(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def _select_bots(service: str) -> list[TrayUiBot]:
    key = service.strip().lower()
    all_bots = list(tray._combined._bots)
    if key in ("all", ""):
        return all_bots
    aliases = {
        "onedrive": "onedrive",
        "one": "onedrive",
        "cisco": "cisco",
    }
    subdir = aliases.get(key)
    if not subdir:
        raise SystemExit(f"Servico invalido: {service!r}. Use: onedrive, cisco ou all")
    bot = tray._combined.get_service_bot(subdir)
    if bot is None:
        raise SystemExit(f"Bot nao encontrado para: {service}")
    return [bot]


def _print_install_status(bots: list[TrayUiBot]) -> None:
    _print_section("Templates AppData")
    for bot in bots:
        svc = bot._service_id()
        status = install_icons.install_status(svc, **bot._template_lookup_kwargs())
        print(f"\n  [{bot.config.service_name}]")
        print(f"    templates_dir: {status.get('templates_dir') or bot._templates_dir}")
        print(f"    seed_repo:     {bot._repo_templates_dir}")
        print(f"    flow:          {bot._flow_path}")
        print(f"    ready: {status['ready']}")
        for state, paths in status["states"].items():
            print(f"    {state}: {len(paths)} icone(s)")
            for path in paths[:5]:
                print(f"      - {path}")
        print(f"    recovery: {len(status['recovery'])} template(s)")


def _scan_service(bot: TrayUiBot, confidence: float) -> str:
    name = bot.config.service_name
    flow = bot.load_flow()

    if bot._is_cisco_service():
        vpn = get_vpn_state()
        print(f"\n  [{name}] vpncli: {vpn.state} (cmd={vpn.command or 'n/a'})")
        if vpn.exe:
            print(f"    exe: {vpn.exe}")

    if not bot.validate_flow(flow):
        print(f"\n  [{name}] TEMPLATES AUSENTES — verifique AppData/imagens/{bot._service_id()}")
        return "missing_icons"

    bot._begin_cycle_cache()
    try:
        state = bot.resolve_tray_state(flow, confidence)
        records = bot._gather_tray_matches(flow, confidence)
        cluster = bot._pick_best_cluster(bot._cluster_records(records, bot._overlap_tolerance_px(flow)))
        print(f"\n  [{name}] estado visual: {state}")
        print(f"  scores: {bot._format_tray_scores(flow, confidence, cluster)}")

        for kind, icon in bot._install_state_entries():
            match = bot._match_install_icon(icon, confidence=0.0)
            if match:
                print(f"    {icon.label} [{kind}]: ENCONTRADO ({match.confidence * 100:.1f}%)")
            else:
                print(f"    {icon.label} [{kind}]: nao encontrado")

        steps = flow.get("steps") or []
        print_names: list[str] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("region") or "").lower() != "full":
                continue
            raw_list = step.get("templates")
            if isinstance(raw_list, list):
                print_names.extend(str(item).strip() for item in raw_list if str(item).strip())
            single = str(step.get("template") or "").strip()
            if single and single not in print_names:
                print_names.append(single)
        if print_names:
            print("  [botoes full]")
            templates = bot._resolve_step_templates(flow, {"templates": print_names, "region": "full"})
            for path in templates:
                step_conf = confidence
                for step in steps:
                    if not isinstance(step, dict) or str(step.get("region") or "").lower() != "full":
                        continue
                    names = [str(t).strip() for t in (step.get("templates") or []) if str(t).strip()]
                    single = str(step.get("template") or "").strip()
                    if single:
                        names.append(single)
                    if path.name in names:
                        try:
                            step_conf = float(step.get("confidence") or confidence)
                        except (TypeError, ValueError):
                            step_conf = confidence
                        break
                match = sa.find_template_masked_from_path(
                    path,
                    region="full",
                    confidence=step_conf,
                    prepare_tray=False,
                    scales=sa.tray_match_scales(),
                    match_mode="grayscale",
                )
                if match:
                    print(f"    {path.name}: ENCONTRADO ({match.confidence * 100:.1f}%)")
                else:
                    print(f"    {path.name}: nao encontrado")

        return state
    finally:
        bot._end_cycle_cache()


def main() -> int:
    parser = argparse.ArgumentParser(description="Teste ao vivo da bandeja (OneDrive + Cisco)")
    parser.add_argument(
        "--service",
        choices=("all", "onedrive", "cisco"),
        default="all",
        help="Testar todos os servicos ou apenas um (default: all)",
    )
    parser.add_argument("--execute", action="store_true", help="Executa um ciclo completo com cliques")
    parser.add_argument("--install-status", action="store_true", help="Mostra templates resolvidos em AppData")
    parser.add_argument("--profile-status", action="store_true", help="Alias de --install-status")
    parser.add_argument("--confidence", type=float, default=None, help="Threshold de match (default: 0.85)")
    args = parser.parse_args()

    tray.parar_event.clear()
    bots = _select_bots(args.service)

    if args.install_status or args.profile_status:
        _print_install_status(bots)
        return 0

    _print_section("1) Servicos monitorados")
    for bot in bots:
        print(f"  - {bot.config.service_name}")
        print(f"      templates AppData: {bot._templates_dir}")
        print(f"      seed repo:         {bot._repo_templates_dir}")

    confidence = args.confidence if args.confidence is not None else 0.85
    print(f"\n  Confidence scan: {confidence}")

    _print_section("2) Deteccao")
    states: dict[str, str] = {}
    for bot in bots:
        states[bot.config.service_name] = _scan_service(bot, confidence)

    print("\n  Resumo:")
    for name, state in states.items():
        print(f"    {name}: {state}")

    if not args.execute:
        print("\nDica: use --execute para rodar um ciclo com cliques reais.")
        print("      Sync infinito: python tools/test_sync_watch_live.py")
        print("      Ajuste TRAY_SCAN_REGION se o estado ficar unknown.")
        return 0

    _print_section("3) Executando ciclo (3 segundos...)")
    for sec in range(3, 0, -1):
        print(f"  {sec}...")
        time.sleep(1)

    statuses: list[str] = []
    tray.set_status_callback(lambda msg: statuses.append(msg))
    tray.set_progress_callback(lambda pct, msg: statuses.append(f"PROGRESS|{pct}|{msg}"))

    if args.service == "all":
        ok = tray._run_cycle()
    else:
        bot = bots[0]
        flow = bot.load_flow()
        if not bot.validate_flow(flow):
            print("\nResultado: FALHOU — templates AppData ausentes")
            return 2
        ok = bot.run_cycle(flow, bot.flow_confidence(flow))

    print(f"\nResultado: {'OK' if ok else 'FALHOU ou inconcluso'}")
    for line in statuses:
        print(f"  {line}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
