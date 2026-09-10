"""Testes funcionais offline dos tratamentos da rotina (sem Selenium)."""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from app.bots.rotina.constants import COLUNAS_MONITOR_UNIFICADO, EXPECTED_COLUMNS
from app.bots.rotina.csv_merge import MergeInteligente
from app.bots.rotina.io import _aplicar_limpeza_bi, _preparar_dataframe_para_parquet
from app.bots.rotina.tasks.monitor import (
    _normalizar_df_monitor_sessoes,
    tratar_arquivo,
)
from app.bots.rotina.tasks.produtividade import tratar_produtividade
from app.bots.rotina.tasks import TASK_REGISTRY, execute_task, TaskResult


@pytest.fixture
def tmp_csv_dir(tmp_path):
    return tmp_path


def _write_monitor_csv(path: Path) -> None:
    df = pd.DataFrame([
        {
            "Usuário": "c91123a",
            "Data do Evento": "01/06/2025 08:00:00",
            "Evento": "Autenticação com sucesso",
            "ID Sessão": "sess1",
            "Objeto": "login",
        },
        {
            "Usuário": "c91123a",
            "Data do Evento": "01/06/2025 12:00:00",
            "Evento": "Logout",
            "ID Sessão": "sess1",
            "Objeto": "logout",
        },
    ])
    df.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _write_prod_csv(path: Path) -> None:
    df = pd.DataFrame([
        {
            "datAnalise": "01/06/2025 08:05:00",
            "numTempoAnalise": "0:30:00",
            "desMatricula": "c91123a - Teste",
            "nomCliente": "CLI",
            "nomWorkflow": "WF",
            "nomEtapa": "ET",
        },
    ])
    df.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def test_tratar_arquivo_produz_parquet(tmp_path, monkeypatch):
    monkeypatch.setenv("PASTA_MONITOR_TRATADO", str(tmp_path / "monitor_tratado"))
    monitor = tmp_path / "monitor.csv"
    prod = tmp_path / "prod.csv"
    _write_monitor_csv(monitor)
    _write_prod_csv(prod)

    out = tratar_arquivo(str(monitor), str(prod), "20250601")
    assert out is not None
    assert Path(out).exists()
    df = pd.read_parquet(out)
    assert len(df) >= 1
    assert "Usuário" in df.columns or "Data do Evento" in df.columns


def test_tratar_arquivo_fecha_sessao_ativa_no_horario_do_snapshot(tmp_path, monkeypatch):
    import app.bots.rotina.tasks.monitor as monitor_mod

    out_dir = tmp_path / "monitor_tratado"
    monkeypatch.setattr(monitor_mod, "PASTA_MONITOR_TRATADO", out_dir)
    monitor = tmp_path / "monitor_ativo.csv"
    prod = tmp_path / "prod_ativo.csv"

    pd.DataFrame(
        [
            {
                "Usuário": "c91123a",
                "Data do Evento": "01/06/2025 08:00:00",
                "Evento": "Autenticação com sucesso",
                "ID Sessão": "sess-ativa",
                "Objeto": "login",
            }
        ]
    ).to_csv(monitor, sep=";", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {
                "datAnalise": "01/06/2025 09:00:00",
                "numTempoAnalise": "0:20:00",
                "desMatricula": "c91123a",
            }
        ]
    ).to_csv(prod, sep=";", index=False, encoding="utf-8-sig")

    out = tratar_arquivo(
        str(monitor),
        str(prod),
        "20250601",
        snapshot_at=datetime(2025, 6, 1, 10, 15),
    )

    tratado = pd.read_parquet(out)
    assert len(tratado) == 1
    assert tratado.iloc[0]["Data segundo evento"] == pd.Timestamp("2025-06-01 10:15:00")
    assert tratado.iloc[0]["Segundo evento"] == "Logout"


def test_tratar_produtividade_parquet(tmp_path, monkeypatch):
    out_dir = tmp_path / "prod_tratado"
    monkeypatch.setenv("PASTA_PROD_TRATADO", str(out_dir))
    import app.bots.rotina.tasks.produtividade as prod_mod
    monkeypatch.setattr(prod_mod, "PASTA_PROD_TRATADO", out_dir, raising=False)

    prod = tmp_path / "prod_d1.parquet"
    df = pd.DataFrame([
        {
            "datAnalise": pd.Timestamp("2025-06-01 08:05:00"),
            "numTempoAnalise": "0:30:00",
            "desMatricula": "c91123a",
            "nomCliente": "CLI",
            "nomWorkflow": "WF",
            "nomEtapa": "ET",
        },
    ])
    df.to_parquet(prod, index=False)

    result = tratar_produtividade(str(prod))
    assert result is not None
    assert Path(result).exists()


def test_tratar_produtividade_inclui_tempo_zero(tmp_path, monkeypatch):
    out_dir = tmp_path / "prod_tratado"
    import app.bots.rotina.tasks.produtividade as prod_mod
    monkeypatch.setattr(prod_mod, "PASTA_PROD_TRATADO", out_dir, raising=False)

    prod = tmp_path / "prod_d1.parquet"
    df = pd.DataFrame([
        {
            "datAnalise": pd.Timestamp("2025-06-01 08:05:00"),
            "numTempoAnalise": "0:00:00",
            "desMatricula": "c91123a",
            "nomCliente": "CLI",
            "nomWorkflow": "WF",
            "nomEtapa": "ET",
        },
    ])
    df.to_parquet(prod, index=False)

    result = tratar_produtividade(str(prod))
    assert result is not None
    tratado = pd.read_parquet(result)
    assert len(tratado) >= 1
    assert tratado.iloc[0]["contagem"] >= 1


def test_normalizar_df_monitor_sessoes():
    df = pd.DataFrame([
        {
            "Data": datetime(2025, 6, 1).date(),
            "Hora": 8,
            "Usuário": "c91123a",
            "Data do Evento": pd.Timestamp("2025-06-01 08:00:00"),
            "Evento": "Autenticação com sucesso",
            "Data segundo evento": pd.Timestamp("2025-06-01 12:00:00"),
            "Segundo evento": "Logout",
        },
        {
            "Data": datetime(2025, 6, 1).date(),
            "Hora": 9,
            "Usuário": "c91123a",
            "Data do Evento": pd.Timestamp("2025-06-01 09:00:00"),
            "Evento": "Outro",
            "Data segundo evento": pd.NaT,
            "Segundo evento": "",
        },
    ])
    out = _normalizar_df_monitor_sessoes(df)
    assert list(out.columns) == COLUNAS_MONITOR_UNIFICADO
    assert len(out) == 1
    assert out.iloc[0]["Segundo evento"] == "Logout"


def test_aplicar_limpeza_bi_matricula_flag():
    df = pd.DataFrame([{
        "Protocolo": "123",
        "CPF": "111",
        "matrícula": "c91123a",
        "Alertas": "GC - 9U1ZD6WWXU",
        "Usuário": "x",
        "Tempo de Análise": "1",
    }])
    out = _aplicar_limpeza_bi(df)
    assert "Usuário" not in out.columns
    assert out.iloc[0]["matrícula"] == 0
    assert out.iloc[0]["Alertas"] == "GC - 9U1ZD6WWXU"


def test_merge_inteligente_prioriza_concluido(tmp_path):
    cols = EXPECTED_COLUMNS
    df_novo = pd.DataFrame([{
        **{c: "" for c in cols},
        "Protocolo": "999",
        "CPF": "111",
        "Workflow": "WF",
        "Status do Registro": "Em análise",
    }])
    df_novo["Protocolo"] = "999"
    df_novo["CPF"] = "111"
    df_novo["Workflow"] = "WF"
    df_novo["Status do Registro"] = "Em análise"

    existente = tmp_path / "old.parquet"
    df_old = df_novo.copy()
    df_old["Status do Registro"] = "Concluído"
    _preparar_dataframe_para_parquet(df_old).to_parquet(existente, index=False)

    merged = MergeInteligente.merge(df_novo, str(existente), ["Protocolo", "Workflow"])
    assert merged.iloc[0]["Status do Registro"] == "Concluído"


def test_task_registry_completo():
    assert len(TASK_REGISTRY) == 12


def test_execute_task_desconhecida():
    r = execute_task(None, "tarefa_inexistente")
    assert r.ok is False


def test_task_result_confer_break():
    assert TaskResult(ok=False, should_break=True).should_break is True
