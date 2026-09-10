"""Verifica imports ausentes em módulos rotina (scan estático)."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "app" / "bots" / "rotina"

MODULES = [
    "app.bots.rotina.constants",
    "app.bots.rotina.state",
    "app.bots.rotina.notifications",
    "app.bots.rotina.csv_merge",
    "app.bots.rotina.io",
    "app.bots.rotina.selenium_brflow",
    "app.bots.rotina.orchestration",
    "app.bots.rotina.tasks",
    "app.bots.rotina.tasks.detalhado",
    "app.bots.rotina.tasks.produtividade",
    "app.bots.rotina.tasks.auditoria",
    "app.bots.rotina.tasks.monitor",
    "app.bots.rotina.tasks.confer",
    "app.bots.rotina.tasks.unificados",
    "app.bots.bot_rotina",
]


def main() -> int:
    errors = []
    for mod_path in MODULES:
        try:
            importlib.import_module(mod_path)
            print(f"  OK  {mod_path}")
        except Exception as exc:
            errors.append(f"{mod_path}: {exc}")
            print(f"  FAIL {mod_path}: {exc}")

    if errors:
        print(f"\n{len(errors)} módulo(s) com erro de import")
        return 1
    print(f"\nTodos os {len(MODULES)} módulos importaram com sucesso")
    return 0


if __name__ == "__main__":
    sys.exit(main())
