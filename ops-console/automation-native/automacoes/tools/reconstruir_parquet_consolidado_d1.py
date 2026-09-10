"""Reconstrói replicacao_aud_d1_consolidado.parquet a partir de planos/relatórios existentes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_d1_planning import (
    _ensure_d1_settings,
    listar_run_ids_com_dados_d1,
    reconstruir_parquet_consolidado_d1,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera o parquet consolidado BI D-1 com dados já existentes.",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        help="Processar apenas estes run_ids (pode repetir a flag).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Mesclar com parquet existente em vez de substituir por completo.",
    )
    parser.add_argument(
        "--config-base",
        default="",
        help="Caminho opcional para replicacao_config_base (pasta config D-1).",
    )
    args = parser.parse_args()

    settings = _ensure_d1_settings({})
    if args.config_base.strip():
        settings["replicacao_config_base"] = args.config_base.strip()

    run_ids = args.run_ids
    if not run_ids:
        run_ids = listar_run_ids_com_dados_d1(settings)
        print(f"Runs encontrados: {len(run_ids)}")

    resultado = reconstruir_parquet_consolidado_d1(
        settings=settings,
        run_ids=run_ids,
        substituir=not args.append,
    )
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0 if resultado.get("path") else 1


if __name__ == "__main__":
    raise SystemExit(main())
