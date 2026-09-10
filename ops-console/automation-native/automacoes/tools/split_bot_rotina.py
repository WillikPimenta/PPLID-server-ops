"""One-time script to split bot_rotina.py into app/bots/rotina/ package."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "bots" / "bot_rotina.py"
PKG = ROOT / "app" / "bots" / "rotina"

# Line ranges (1-based, inclusive) for each module section from original file.
# Format: module_name -> list of (start, end) ranges to include
SECTIONS: dict[str, list[tuple[int, int]]] = {
    "constants": [(91, 227)],
    "state": [(229, 369)],  # timing dict + exec summary helpers (without _contar_linhas which needs CSVReader)
    "csv_merge": [(388, 691)],
    "io_timing": [(698, 879)],  # timing + utils + fix_single_column (before downloads)
    "io_downloads": [(886, 1112)],  # download wait + baixar_arquivos + data helpers
    "io_parquet": [(2942, 3157), (3666, 3720)],  # merge parquet, normalizar data, resolver datas
    "io_consolidacao": [(3159, 3518)],  # combinar, processar dia, salvar consolidado
    "selenium_brflow": [(4234, 4365)],  # login + navegar (orchestration stays separate)
    "tasks_produtividade": [(1151, 1315), (1914, 2010)],
    "tasks_monitor": [(1115, 1149), (1180, 1220), (1317, 1912), (2664, 2744), (2746, 2903)],
    "tasks_auditoria": [(2012, 2328)],
    "tasks_detalhado": [(3520, 3664)],
    "tasks_confer_selenium": [(2330, 2612), (4376, 5623)],  # confer block + log_eventos
    "tasks_unificados": [(2614, 2662), (2693, 2744)],
    "orchestration": [(3722, 4227), (5814, 5824)],  # start/stop/exec + __main__
    "notifications": [(300, 381)],  # teams helpers (needs _contar_linhas moved to notifications or io)
}

# Functions that span sections - handled manually in notifications
EXTRA_STATE_FUNCS = [(345, 369)]  # _contar_linhas uses CSVReader


def read_lines() -> list[str]:
    return SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def extract_ranges(lines: list[str], ranges: list[tuple[int, int]]) -> str:
    parts = []
    for start, end in ranges:
        parts.extend(lines[start - 1 : end])
    return "".join(parts)


def write_module(name: str, body: str, header: str) -> None:
    path = PKG / f"{name}.py"
    content = header.rstrip() + "\n\n" + body.lstrip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {path} ({len(content.splitlines())} lines)")


def main() -> None:
    lines = read_lines()
    for name, ranges in SECTIONS.items():
        body = extract_ranges(lines, ranges)
        write_module(name, body, f'"""Auto-split from bot_rotina — {name}."""')
    print("Done. Manual import fixes required.")


if __name__ == "__main__":
    main()
