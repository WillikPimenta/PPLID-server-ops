"""Testes offline do tratamento mensal busca protocolo Confer."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from app.config import PREFIXO_CONF_BRUTO
from app.bots.rotina.constants import (
    BUSCA_PROTOCOLO_ABA_GERENCIAL,
    BUSCA_PROTOCOLO_COLUNAS_SAIDA,
)
from app.bots.rotina.tasks import confer as confer_mod
from app.bots.rotina.tasks.confer import (
    _consolidar_busca_protocolo_confer_mes,
    _tratar_busca_protocolo_confer_df,
)


def _df_bruto_confer() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Data/Hora do Cadastro": ["01/06/2026 08:00", "01/06/2026 09:00", "01/06/2026 10:00"],
            "Data/Hora da Conferência": ["01/06/2026 08:05", "01/06/2026 09:05", "01/06/2026 10:05"],
            "Tempo de Análise": ["0:05:00", "0:03:30", "0:10:00"],
            "Protocolo": ["P001", "P002", "P003"],
            "Status": ["Concluído", "Concluído", "Concluído"],
            "Ilha": ["Ilha A", "Ilha A", "Ilha B"],
            "Etapa": ["Reclassificação", "Outra Etapa", "Reclassificação"],
            "Matrícula do Colaborador": ["C93123A", "C93124B", "C93125C"],
            "Nome do Colaborador": ["Ana", "Bruno", "Carla"],
            "Tipo/Status conferencia": ["OK", "OK", "Pendente"],
        }
    )


def test_tratar_busca_protocolo_filtra_reclassificacao_e_colunas():
    df = _tratar_busca_protocolo_confer_df(_df_bruto_confer())
    assert list(df.columns) == BUSCA_PROTOCOLO_COLUNAS_SAIDA
    assert len(df) == 2
    assert set(df["Protocolo"]) == {"P001", "P003"}
    assert "Data/Hora do Cadastro" not in df.columns
    assert "Tempo por minuto" in df.columns
    assert df["Tempo por minuto"].iloc[0] == "0:05:00"
    assert df["Matrícula"].tolist() == ["C93123A", "C93125C"]


def test_tratar_busca_protocolo_coluna_etapa_ausente():
    df = _df_bruto_confer().drop(columns=["Etapa"])
    assert _tratar_busca_protocolo_confer_df(df).empty


def test_consolidar_busca_protocolo_mes_unifica_brutos(tmp_path, monkeypatch):
    pasta_bruto = tmp_path / "confer-prod-bruto"
    pasta_saida = tmp_path / "tratado"
    pasta_gerencial = tmp_path / "gerencial"
    pasta_bruto.mkdir()
    pasta_saida.mkdir()

    for dia in ("20250601", "20250602"):
        caminho = pasta_bruto / f"{PREFIXO_CONF_BRUTO}{dia}.parquet"
        _df_bruto_confer().to_parquet(caminho, index=False)

    monkeypatch.setattr(confer_mod, "PASTA_PRODUCAO_CONFER", pasta_bruto)
    monkeypatch.setattr(confer_mod, "PASTA_CONFER_BUSCAR_PROTOCOLO_TRATADO", pasta_saida)
    monkeypatch.setattr(confer_mod, "PASTA_BUSCA_PROTOCOLOS_GERENCIAL", pasta_gerencial)

    resultado = _consolidar_busca_protocolo_confer_mes(date(2025, 6, 2))
    assert resultado is not None
    assert resultado.name == "confer-buscarpIrregularidade-tratado_202506.csv"
    assert resultado.exists()

    df_saida = pd.read_csv(resultado, sep=";", encoding="cp1252")
    assert len(df_saida) == 4
    assert list(df_saida.columns) == BUSCA_PROTOCOLO_COLUNAS_SAIDA

    caminho_gerencial = pasta_gerencial / "confer-buscarpIrregularidade-tratado_202506.xlsx"
    assert caminho_gerencial.exists()
    with pd.ExcelFile(caminho_gerencial, engine="openpyxl") as xl:
        assert xl.sheet_names == [BUSCA_PROTOCOLO_ABA_GERENCIAL]
        df_gerencial = pd.read_excel(xl, sheet_name=BUSCA_PROTOCOLO_ABA_GERENCIAL)
    assert len(df_gerencial) == 4
    assert list(df_gerencial.columns) == BUSCA_PROTOCOLO_COLUNAS_SAIDA


def test_consolidar_sem_brutos_mes_retorna_none(tmp_path, monkeypatch):
    pasta_bruto = tmp_path / "confer-prod-bruto"
    pasta_bruto.mkdir()
    monkeypatch.setattr(confer_mod, "PASTA_PRODUCAO_CONFER", pasta_bruto)
    monkeypatch.setattr(confer_mod, "PASTA_CONFER_BUSCAR_PROTOCOLO_TRATADO", tmp_path / "saida")

    assert _consolidar_busca_protocolo_confer_mes(date(2025, 6, 2)) is None
