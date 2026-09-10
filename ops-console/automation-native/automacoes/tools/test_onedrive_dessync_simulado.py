"""Teste simulado: OneDrive dessincronizado (nao altera registro nem abre janelas)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.bots.bot_onedrive as bot


def _print_section(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    include_real = "--real" in sys.argv

    bot.parar_event.clear()
    statuses: list[str] = []

    bot.set_status_callback(lambda msg: statuses.append(f"STATUS|{msg}"))
    bot.set_progress_callback(lambda pct, msg: statuses.append(f"PROGRESS|{pct}|{msg}"))

    os.environ["ONEDRIVE_SIMULATE_DESSYNC"] = "1"
    os.environ["ONEDRIVE_SIMULATE_RECOVER"] = "1"
    bot._POST_RECOVER_WAIT_SECONDS = 0

    _print_section("1) Verificacao simulada (OneDrive dessincronizado)")
    ok, exit_code, output = bot.verificar_status_onedrive()
    print(f"Resultado: ok={ok}, exit_code={exit_code}")
    for line in output.splitlines():
        if any(token in line for token in ("MODO TESTE", "Motivos", "STATUS FINAL", " - ")):
            print(line)

    _print_section("2) Recuperacao simulada (sem abrir janelas)")
    success, recover_code, recover_output = bot.recuperar_sessao_onedrive()
    print(f"Resultado: success={success}, exit_code={recover_code}")
    for line in recover_output.splitlines():
        if "SIMULACAO" in line or "MODO TESTE" in line:
            print(line)

    _print_section("3) Ciclo completo do bot (verify -> recover -> re-verify)")
    statuses.clear()
    cycle_ok = bot._run_cycle()
    print(f"ciclo_ok={cycle_ok}")
    for entry in statuses:
        print(entry)

    _print_section("4) Verificacao real (sem simulacao)")
    if not include_real:
        print("Pulado (use --real para executar verificacao real; leva alguns minutos)")
    else:
        os.environ.pop("ONEDRIVE_SIMULATE_DESSYNC", None)
        os.environ.pop("ONEDRIVE_SIMULATE_RECOVER", None)
        ok_real, exit_real, _ = bot.verificar_status_onedrive()
        print(f"Resultado real: ok={ok_real}, exit_code={exit_real}")

    os.environ.pop("ONEDRIVE_SIMULATE_DESSYNC", None)
    os.environ.pop("ONEDRIVE_SIMULATE_RECOVER", None)

    print()
    if not ok and exit_code == 2 and success and not cycle_ok:
        print("TESTE SIMULADO: PASSOU (dessincronizacao detectada e recuperacao acionada)")
        return 0

    print("TESTE SIMULADO: revisar saida acima")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
