"""Reprocessa replicacao_aud_d1_conferencia a partir dos CSV replicados D-1 existentes."""
from __future__ import annotations

import argparse
import logging
import sys

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.bots.rotina.tasks.auditoria import (  # noqa: E402
    _reprocessar_conferencia_replicados_pendentes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera replicacao_aud_d1_conferencia_*.csv para replicados D-1 pendentes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerar mesmo quando o arquivo de conferência já existe",
    )
    parser.add_argument(
        "--data",
        dest="data_ref",
        metavar="YYYYMMDD",
        help="Processar apenas uma data (ex.: 20260610)",
    )
    args = parser.parse_args()

    resultados = _reprocessar_conferencia_replicados_pendentes(
        forcar=args.force,
        data_ref=args.data_ref,
    )

    if not resultados:
        log.warning("Nenhum arquivo brflow-replicadosd1-tratado encontrado.")
        return 1

    print()
    print(f"{'Data':<10} {'Status':<14} Detalhe")
    print("-" * 70)
    falhas = 0
    for item in resultados:
        detalhe = item.get("saida") or item.get("detalhe", "")
        print(f"{item['data_ref']:<10} {item['status']:<14} {detalhe}")
        if item["status"] == "erro":
            falhas += 1

    ok = sum(1 for r in resultados if r["status"] == "ok")
    print("-" * 70)
    print(f"Total={len(resultados)} | gerados={ok} | falhas={falhas}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
