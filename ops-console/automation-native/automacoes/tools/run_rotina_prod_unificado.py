"""Executa apenas a tarefa prod_unificado da rotina (browser visível)."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bots.rotina.constants import TASK_PROD_UNIFICADO  # noqa: E402
from app.bots.rotina.orchestration import executar_novo_bot  # noqa: E402
from app.bots.rotina.state import parar_event  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotina — Prod Unificado (produtividade D-1 + confer)")
    parser.add_argument(
        "--matricula",
        default="",
        help="Matrícula Okta (se omitida, solicita no terminal)",
    )
    parser.add_argument(
        "--sem-deps",
        action="store_true",
        help="Não incluir dependências automáticas (só a união, se os parquets já existirem)",
    )
    args = parser.parse_args()

    matricula = (args.matricula or "").strip()
    if not matricula:
        matricula = input("Matrícula Okta: ").strip()
    senha = getpass.getpass("Senha Okta: ")

    if not matricula or not senha:
        print("Matrícula e senha são obrigatórias.", file=sys.stderr)
        return 1

    os.environ["ROBOT_HEADLESS"] = "0"
    os.environ["ROTINA_HEADLESS"] = "0"

    parar_event.clear()

    settings = {
        "matricula": matricula,
        "senha": senha,
        "executar_imediatamente": True,
        "tarefas": [TASK_PROD_UNIFICADO],
    }

    print("Iniciando rotina | tarefa=prod_unificado | browser visível")
    if not args.sem_deps:
        print(
            "Nota: o bot resolve dependências automaticamente "
            "(produtividade_d1 + confer_producao antes da união)."
        )

    if args.sem_deps:
        from app.bots.rotina.tasks.unificados import _juntar_confer_prod_tratados_dia
        from app.bots.rotina.tasks import _data_d1_ref

        data_ref = _data_d1_ref()
        print(f"Modo sem-deps: união direta para data_ref={data_ref}")
        resultado = _juntar_confer_prod_tratados_dia(data_ref)
        if resultado:
            print(f"União concluída: {resultado}")
            return 0
        print("Falha na união (arquivos confer/prod tratados ausentes?)", file=sys.stderr)
        return 1

    executar_novo_bot(settings=settings)
    print("Execução finalizada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
