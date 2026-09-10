"""Executa replicação D-1 no BRFlow (browser visível) a partir de um run_id existente."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bots.bot_replicacao_aud_d1 import executar_bot_replicacao  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Execução BRFlow — replicação D-1")
    parser.add_argument(
        "--run-id",
        default="20260701_163116",
        help="run_id do plano já gerado (padrão: 20260701_163116)",
    )
    parser.add_argument(
        "--matricula",
        default="",
        help="Matrícula Okta (se omitida, solicita no terminal)",
    )
    parser.add_argument(
        "--forcar",
        action="store_true",
        help="Reexecutar workflows já concluídos (forcar_reexecucao)",
    )
    parser.add_argument(
        "--novo-plano",
        action="store_true",
        help="Gerar plano D-1 novo antes da execução BRFlow (gerar_novo_plano=True)",
    )
    args = parser.parse_args()

    matricula = (args.matricula or "").strip()
    if not matricula:
        matricula = input("Matrícula Okta: ").strip()
    senha = getpass.getpass("Senha Okta: ")

    if not matricula or not senha:
        print("Matrícula e senha são obrigatórias.", file=sys.stderr)
        return 1

    settings = {
        "run_id": args.run_id.strip() if not args.novo_plano else "",
        "gerar_novo_plano": bool(args.novo_plano),
        "apenas_planejamento": False,
        "apenas_pendentes": not args.forcar,
        "forcar_reexecucao": args.forcar,
        "headless": False,
        "matricula": matricula,
        "senha": senha,
    }

    if args.novo_plano:
        print("Modo completo: planejamento D-1 novo + execução BRFlow")
    else:
        print(f"Iniciando execução BRFlow | run_id={settings['run_id']} | browser visível")
    executar_bot_replicacao(settings)
    print("Execução finalizada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
