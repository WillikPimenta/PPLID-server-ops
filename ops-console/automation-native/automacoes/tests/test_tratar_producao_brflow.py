# -*- coding: utf-8 -*-
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
import pytest

_KIT = Path(__file__).resolve().parents[1] / "tools" / "producao-brflow-kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))

try:
    from tratar_producao import processar_lote_producao
except ImportError:  # kit opcional em alguns checkouts
    processar_lote_producao = None

from app.bots.bot_production import (
    EXPECTED_COLUNAS_PRODUCAO,
    _processar_dataframe_producao,
    processar_csv_brflow_para_detalhado,
)

def _df_brflow_fixture(data_str: str = "20/06/2026") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Matrícula": "C92928A",
                "Data de Análise": f"{data_str} 10:30:00",
                "Workflow": "WF1",
                "Etapa": "Etapa A",
                "Tempo Total": "00:05:00",
                "Total de Análise": "3",
            },
            {
                "Matrícula": "C92928A",
                "Data de Análise": f"{data_str} 11:00:00",
                "Workflow": "WF1",
                "Etapa": "Etapa A",
                "Tempo Total": "00:03:00",
                "Total de Análise": "2",
            },
            {
                "Matrícula": "C92928A",
                "Data de Análise": "19/06/2026 09:00:00",
                "Workflow": "WF1",
                "Etapa": "Etapa B",
                "Tempo Total": "00:01:00",
                "Total de Análise": "1",
            },
            {
                "Matrícula": "C92928A",
                "Data de Análise": "19/06/2026 23:15:00",
                "Workflow": "WF1",
                "Etapa": "Etapa Madrugada",
                "Tempo Total": "00:02:00",
                "Total de Análise": "1",
            },
        ]
    )


@patch("app.bots.bot_production.pd.read_excel")
def test_processar_dataframe_producao_filtra_target_date_explicita(mock_read_excel):
    mock_read_excel.side_effect = FileNotFoundError("sem metas")
    df = _df_brflow_fixture()
    resultado = _processar_dataframe_producao(df, target_date=date(2026, 6, 20))

    assert not resultado.empty
    # Janela 19/06 23:00 → 20/06 23:00: inclui 23h do dia anterior + dia 20
    datas = set(resultado["Data de Análise"].tolist())
    assert date(2026, 6, 20) in datas
    assert date(2026, 6, 19) in datas
    assert len(resultado) == 3  # 10:30, 11:00 e 23:15; exclui 09:00


@patch("app.bots.bot_production.pd.read_excel")
def test_processar_dataframe_producao_descarta_outro_dia(mock_read_excel):
    mock_read_excel.side_effect = FileNotFoundError("sem metas")
    df = _df_brflow_fixture()
    resultado = _processar_dataframe_producao(df, target_date=date(2026, 6, 19))

    assert not resultado.empty
    # Janela 18/06 23:00 → 19/06 23:00: 09:00 fica; 23:15 já é do turno 20/06
    assert all(resultado["Data de Análise"] == date(2026, 6, 19))
    assert len(resultado) == 1


def test_processar_csv_brflow_para_detalhado_gera_xlsx():
    with TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "brflow.csv"
        out_dir = Path(tmp) / "saida"
        _df_brflow_fixture().to_csv(csv_path, index=False, sep=";")

        destino = processar_csv_brflow_para_detalhado(
            csv_path,
            out_dir,
            target_date=date(2026, 6, 20),
        )

        assert destino.exists()
        assert destino.name == "relatorio_produtividade_detalhado_2026-06-20.xlsx"

        df_xlsx = pd.read_excel(destino)
        for col in EXPECTED_COLUNAS_PRODUCAO:
            assert col in df_xlsx.columns
        assert len(df_xlsx) >= 1


def test_processar_lote_producao_limpa_csv_apos_tratar():
    if processar_lote_producao is None:
        pytest.skip("kit producao-brflow ausente")
    with TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        csv_path = pasta / "brflow.csv"
        _df_brflow_fixture().to_csv(csv_path, index=False, sep=";")

        resultado = processar_lote_producao(
            pasta_origem=pasta,
            pasta_saida=pasta,
            csv_paths=[csv_path],
            limpar_csv=True,
        )

        assert len(resultado["arquivos"]) == 1
        assert not csv_path.exists()
        assert len(resultado["removidos"]) == 1
        assert Path(resultado["arquivos"][0]["caminho"]).exists()


def test_processar_csv_brflow_para_detalhado_csv_vazio():
    with TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "vazio.csv"
        pd.DataFrame().to_csv(csv_path, index=False)
        with pytest.raises(RuntimeError, match="CSV vazio"):
            processar_csv_brflow_para_detalhado(csv_path, tmp, target_date=date(2026, 6, 20))
