"""Testes do mapeamento Workflow (tela) vs Workflow d-1 (parquet)."""

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.bots.replicacao_aud_planning import (
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_CONFIG_WORKFLOW_D1,
    _workflow_d1_da_linha,
    carregar_config_auditoria,
    contar_por_hora,
)


def test_workflow_d1_fallback():
    row = pd.Series({COLUNA_CONFIG_WORKFLOW: "Tela WF", COLUNA_CONFIG_WORKFLOW_D1: ""})
    assert _workflow_d1_da_linha(row) == "Tela WF"


def test_workflow_d1_coluna_excel():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.xlsx"
        pd.DataFrame(
            {
                "Workflow": ["Tela A", "Tela B"],
                "Workflow d-1": ["Parquet A", ""],
                "Amostra diária": [10, 5],
            }
        ).to_excel(path, index=False)

        df = carregar_config_auditoria(path)
        assert df.loc[0, COLUNA_CONFIG_WORKFLOW_D1] == "Parquet A"
        assert df.loc[1, COLUNA_CONFIG_WORKFLOW_D1] == "Tela B"


def test_contar_por_hora_usa_d1():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.xlsx"
        pd.DataFrame(
            {
                "Workflow": ["Tela"],
                "Workflow d-1": ["Nome Parquet"],
                "Amostra diária": [5],
            }
        ).to_excel(path, index=False)

        parquet_df = pd.DataFrame(
            {
                "Protocolo": ["1", "2"],
                "Workflow": ["NOME PARQUET", "nome parquet"],
                "Data de Análise": ["01/01/2026 10:00:00", "01/01/2026 11:00:00"],
            }
        )
        pq_path = Path(tmp) / "d1.parquet"
        parquet_df.to_parquet(pq_path)

        config = carregar_config_auditoria(path)
        row = config.iloc[0]
        contagens = contar_por_hora(parquet_df, _workflow_d1_da_linha(row))
        assert sum(contagens.values()) == 2


def main():
    test_workflow_d1_fallback()
    test_workflow_d1_coluna_excel()
    test_contar_por_hora_usa_d1()
    print("OK: testes Workflow d-1 passaram")


if __name__ == "__main__":
    main()
