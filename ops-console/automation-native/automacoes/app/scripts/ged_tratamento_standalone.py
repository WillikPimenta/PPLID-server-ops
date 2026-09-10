"""Tratamento standalone de arquivos GED (CSV/XLS/XLSX).

Uso rápido:
    python teste.py --input "C:/caminho/arquivo.csv" --inicio 01/04/2026 --fim 03/04/2026
    python teste.py --input "C:/caminho/pasta" --inicio 01/04/2026 --fim 03/04/2026
    python teste.py --input "C:/caminho/*.csv" --inicio 01/04/2026 --fim 03/04/2026
"""

from __future__ import annotations

import argparse
import glob
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.bots import bot_ged


PASTA_GED_TRATADO = Path(r"C:\Users\c93123a\Downloads\testetstestte")
PREFIXO_GED_TRATADO = "ged-detalhado-tratado_"

# Mantém exatamente as colunas pedidas.
GED_COLUNAS_SAIDA = [
    "Protocolo",
    "Data do Recebimento",
    "Tipo de Serviço Primário",
    "Data da Venda",
    "Data do Batimento",
    "Data Retorno Inspeção",
    "Data Envio Inspe.",
    "Canal de Ativação",
    "Status Contrato",
    "Aceite Digital",
]


def _montar_nome_saida_ged_tratado(data_inicio: str, data_fim: str) -> str:
    dt_inicio = datetime.strptime(data_inicio.strip(), "%d/%m/%Y")
    dt_fim = datetime.strptime(data_fim.strip(), "%d/%m/%Y")
    sufixo_periodo = f"{dt_inicio.strftime('%d%m')}-{dt_fim.strftime('%d%m%Y')}"
    return f"{PREFIXO_GED_TRATADO}{sufixo_periodo}.parquet"


def _tratar_arquivo_ged(caminho_arquivo: Path, pasta_saida: Path, data_inicio: str, data_fim: str) -> Path:
    if not caminho_arquivo.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_arquivo}")

    df = bot_ged._carregar_dataframe_ged(caminho_arquivo)

    faltantes = [c for c in GED_COLUNAS_SAIDA if c not in df.columns]
    if faltantes:
        raise ValueError(f"Colunas obrigatórias ausentes em {caminho_arquivo.name}: {faltantes}")

    df = df[GED_COLUNAS_SAIDA].copy()
    bot_ged._validar_alinhamento_protocolo(df)

    serie = df["Data do Batimento"].astype(str).str.strip()
    invalidos = {"", "-", "null", "none", "nan"}
    df = df.loc[~serie.str.lower().isin(invalidos)].copy()

    pasta_saida.mkdir(parents=True, exist_ok=True)
    nome_saida = _montar_nome_saida_ged_tratado(data_inicio=data_inicio, data_fim=data_fim)

    # Evita sobrescrever quando tratar múltiplos arquivos no mesmo período.
    base = caminho_arquivo.stem
    caminho_saida = pasta_saida / nome_saida
    if caminho_saida.exists():
        caminho_saida = pasta_saida / f"{Path(nome_saida).stem}_{base}.parquet"

    df.to_parquet(caminho_saida, index=False)

    return caminho_saida


def _resolver_entradas(input_arg: str) -> list[Path]:
    alvo = Path(input_arg)
    if alvo.exists() and alvo.is_file():
        return [alvo]
    if alvo.exists() and alvo.is_dir():
        return sorted(
            [p for p in alvo.iterdir() if p.is_file() and p.suffix.lower() in {".csv", ".xlsx", ".xls"}]
        )

    encontrados = [Path(p) for p in glob.glob(input_arg)]
    return sorted([p for p in encontrados if p.is_file() and p.suffix.lower() in {".csv", ".xlsx", ".xls"}])


def main() -> int:
    parser = argparse.ArgumentParser(description="Trata arquivos GED em modo standalone")
    parser.add_argument("--input", required=True, help="Arquivo, pasta, ou padrão glob (ex.: C:/dados/*.csv)")
    parser.add_argument("--inicio", required=True, help="Data inicial no formato DD/MM/AAAA")
    parser.add_argument("--fim", required=True, help="Data final no formato DD/MM/AAAA")
    parser.add_argument("--output", default=str(PASTA_GED_TRATADO), help="Pasta de saída")
    args = parser.parse_args()

    arquivos = _resolver_entradas(args.input)
    if not arquivos:
        print("Nenhum arquivo CSV/XLS/XLSX encontrado para tratar.")
        return 1

    pasta_saida = Path(args.output)
    erros = 0
    for arquivo in arquivos:
        try:
            saida = _tratar_arquivo_ged(arquivo, pasta_saida, args.inicio, args.fim)
            print(f"OK: {arquivo} -> {saida}")
        except Exception as exc:
            erros += 1
            print(f"ERRO: {arquivo} | {exc}")

    return 0 if erros == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())