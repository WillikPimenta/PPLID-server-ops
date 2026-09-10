"""Testes do planejamento D-1: volumetria parquet, amostra e redistribuição priorizada."""

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_d1_planning import (
    COLUNA_CONFIG_AUTOMATICOS,
    COLUNA_CONFIG_CLIENTE,
    COLUNA_CONFIG_MANUAIS,
    COLUNA_CONFIG_TOTAL,
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_CONFIG_WORKFLOW_D1,
    COLUNA_PROTOCOLO,
    COLUNA_WORKFLOW_PARQUET,
    COLUNA_WORKFLOW_PARQUET_NOME,
    _meta_colunas_config_d1,
    _normalizar_workflow,
    calcular_amostras_calculadora_d1,
    carregar_volumetria_d1_parquet,
    montar_config_replicacao_d1,
    redistribuir_amostra_priorizada,
)
from app.config import COLUNA_CONFIG_CATEGORIA, COLUNA_CONFIG_META_CLIENTE


def test_limite_balanceamento_nao_vai_para_resumo_analitico():
    row = pd.Series(
        {
            COLUNA_CONFIG_WORKFLOW: "WF A",
            COLUNA_CONFIG_CLIENTE: "Cliente A",
            COLUNA_CONFIG_META_CLIENTE: 1000,
        }
    )

    resumo = _meta_colunas_config_d1(row, "postgresql:2026-08-10")

    assert COLUNA_CONFIG_META_CLIENTE not in resumo


def _criar_fixture_config(base: Path) -> None:
    escala = base / "escala"
    escala.mkdir(parents=True)
    (escala / "escala_auditores.csv").write_text(
        "data,auditores_ativos\n20260602,10\n",
        encoding="utf-8",
    )

    pd.DataFrame(
        {
            "Cliente": ["Cliente A", "Cliente B"],
            "Segmento": ["Seg A", "Seg B"],
            "Categoria": ["Cat A", "Cat B"],
            "Meta Cliente": [1000, 800],
        }
    ).to_excel(base / "Categoria.xlsx", index=False)

    default = base / "Default.xlsx"
    with pd.ExcelWriter(default, engine="openpyxl") as writer:
        pd.DataFrame(
            {
                "Workflow": ["WF Tela A", "WF Tela B", "WF Off"],
                "Cliente": ["Cliente A", "Cliente B", "Cliente A"],
                "Workflow d-1": ["WF Parquet A", "WF Parquet B", "WF Parquet Off"],
                "Workflow - selenium": ["Nome A", "Nome B", "Nome Off"],
                "Status": [True, True, False],
            }
        ).to_excel(writer, sheet_name="Workflow d1", index=False)
        calc = pd.DataFrame(
            [
                ["Auditores ativos", 10, None, "Cliente", "Workflow", "Categoria", "Automático", "Manual", "Total", "Amostra total", "Amostra diária", "Amostra Conf. Prod"],
                ["Dias úteis", 24, None, None, None, None, None, None, None, None, None, None],
                ["Meta Produ (diária)", 300, None, None, None, None, None, None, None, None, None, None],
                ["Nível de confiança", 0.99, None, None, None, None, None, None, None, None, None, None],
                ["Margem de erro (high)", 0.0115, None, None, None, None, None, None, None, None, None, None],
                ["Margem de erro (low/mid)", 0.0178, None, None, None, None, None, None, None, None, None, None],
            ]
        )
        calc.to_excel(writer, sheet_name="Calculadora Padrão", index=False, header=False)


def _criar_parquet(base: Path, linhas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    path = base / "brflow-detalhado-tratado_20260601.parquet"
    df.to_parquet(path, index=False)
    return df


def _with_fixture(parquet_linhas, run, *, sync=True):
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        config = base / "config"
        config.mkdir()
        _criar_fixture_config(config)
        df = _criar_parquet(base, parquet_linhas)
        run(base, config, df)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_carregar_volumetria_d1_parquet():
    df = pd.DataFrame(
        {
            COLUNA_PROTOCOLO: ["1", "2", "3", "4"],
            COLUNA_WORKFLOW_PARQUET: ["WF Parquet A", "WF Parquet A", "WF Parquet B", "WF Parquet B"],
            "Data de Análise": ["01/06/2026 10:00"] * 4,
            COLUNA_CONFIG_CLIENTE: ["Cliente A", "Cliente A", "Cliente B", "Cliente B"],
        }
    )
    vol = carregar_volumetria_d1_parquet(df)
    assert len(vol) == 2
    row_a = vol[vol[COLUNA_WORKFLOW_PARQUET_NOME] == "WF Parquet A"].iloc[0]
    assert int(row_a[COLUNA_CONFIG_TOTAL]) == 2
    assert int(row_a[COLUNA_CONFIG_MANUAIS]) == 2
    assert int(row_a[COLUNA_CONFIG_AUTOMATICOS]) == 0


def test_carregar_volumetria_d1_parquet_classifica_matricula():
    df = pd.DataFrame(
        {
            COLUNA_PROTOCOLO: ["1", "2", "3", "4", "5"],
            COLUNA_WORKFLOW_PARQUET: ["WF A"] * 5,
            "Data de Análise": ["01/06/2026 10:00"] * 5,
            "matrícula": [1, 1, 0, 0, 0],
        }
    )
    vol = carregar_volumetria_d1_parquet(df)
    row = vol.iloc[0]
    assert int(row[COLUNA_CONFIG_AUTOMATICOS]) == 2
    assert int(row[COLUNA_CONFIG_MANUAIS]) == 3
    assert int(row[COLUNA_CONFIG_TOTAL]) == 5


def test_montar_config_merge_volumetria_d1():
    def _run(base, config, df_parquet):
        out, warnings, ref = montar_config_replicacao_d1(
            df_parquet,
            settings={
                "fonte_banco_ativa": False,
                "replicacao_config_base": str(config),
                "replicacao_sincronizar_workflow_d1": False,
            },
        )
        assert "parquet" in ref.lower() or ref
        assert len(out) >= 2
        row = out[out[COLUNA_CONFIG_WORKFLOW] == "WF Tela A"].iloc[0]
        assert int(row[COLUNA_CONFIG_TOTAL]) == 100

    linhas = []
    for i in range(100):
        linhas.append(
            {
                COLUNA_PROTOCOLO: str(i),
                COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
                "Data de Análise": "01/06/2026 10:00:00",
                COLUNA_CONFIG_CLIENTE: "Cliente A",
            }
        )
    for i in range(50):
        linhas.append(
            {
                COLUNA_PROTOCOLO: str(100 + i),
                COLUNA_WORKFLOW_PARQUET: "WF Parquet B",
                "Data de Análise": "01/06/2026 11:00:00",
                COLUNA_CONFIG_CLIENTE: "Cliente B",
            }
        )
    _with_fixture(linhas, _run)


def test_amostra_diaria_igual_amostra_total_sem_divisao():
    def _run(base, config, df_parquet):
        out, _, _ = montar_config_replicacao_d1(
            df_parquet,
            settings={
                "fonte_banco_ativa": False,
                "replicacao_config_base": str(config),
                "replicacao_sincronizar_workflow_d1": False,
            },
        )
        row = out[out[COLUNA_CONFIG_WORKFLOW] == "WF Tela A"].iloc[0]
        assert int(row["amostra_diaria"]) == int(row["amostra_total"])
        assert int(row["amostra_diaria"]) > 0

    linhas = [
        {
            COLUNA_PROTOCOLO: str(i),
            COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
            "Data de Análise": "01/06/2026 10:00:00",
            COLUNA_CONFIG_CLIENTE: "Cliente A",
        }
        for i in range(1000)
    ]
    _with_fixture(linhas, _run)


def test_calcular_amostras_calculadora_d1_direto():
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        _criar_fixture_config(base)
        df_wf = pd.DataFrame(
            {
                "_wf_key": ["wf_a"],
                COLUNA_CONFIG_TOTAL: [500],
                COLUNA_CONFIG_CATEGORIA: ["Cat A"],
            }
        )
        amostras = calcular_amostras_calculadora_d1(base / "Default.xlsx", df_wf)
        assert not amostras.empty
        assert amostras.iloc[0]["amostra_diaria"] == amostras.iloc[0]["amostra_total"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_redistribuir_prioridade_cliente():
    fontes = [
        {"workflow": "WF Off", "amostra": 10, "cliente": "Cliente A", "categoria": "Cat A"},
    ]
    destinos = [
        {"workflow": "WF A", "amostra": 5, "cliente": "Cliente A", "categoria": "Cat A"},
        {"workflow": "WF C", "amostra": 5, "cliente": "Cliente C", "categoria": "Cat C"},
    ]
    bonus = redistribuir_amostra_priorizada(fontes, destinos)
    assert bonus.get("WF A", 0) == 10
    assert bonus.get("WF C", 0) == 0


def test_redistribuir_prioridade_categoria():
    fontes = [
        {"workflow": "WF Off", "amostra": 9, "cliente": "Cliente X", "categoria": "Cat B"},
    ]
    destinos = [
        {"workflow": "WF B", "amostra": 6, "cliente": "Cliente B", "categoria": "Cat B"},
        {"workflow": "WF C", "amostra": 3, "cliente": "Cliente C", "categoria": "Cat C"},
    ]
    bonus = redistribuir_amostra_priorizada(fontes, destinos)
    assert bonus.get("WF B", 0) == 9
    assert bonus.get("WF C", 0) == 0


def test_redistribuir_prioridade_geral():
    fontes = [
        {"workflow": "WF Off", "amostra": 6, "cliente": "Cliente X", "categoria": "Cat X"},
    ]
    destinos = [
        {"workflow": "WF B", "amostra": 4, "cliente": "Cliente B", "categoria": "Cat B"},
        {"workflow": "WF C", "amostra": 2, "cliente": "Cliente C", "categoria": "Cat C"},
    ]
    bonus = redistribuir_amostra_priorizada(fontes, destinos)
    assert sum(bonus.values()) == 6
    assert bonus.get("WF B", 0) == 4
    assert bonus.get("WF C", 0) == 2


def test_workflow_novo_no_parquet_sync_pendente():
    def _run(base, config, df_parquet):
        out, warnings, _ = montar_config_replicacao_d1(
            df_parquet,
            settings={
                "fonte_banco_ativa": False,
                "replicacao_config_base": str(config),
                "replicacao_sincronizar_workflow_d1": True,
            },
        )
        raw = pd.read_excel(config / "Default.xlsx", sheet_name="Workflow d1", engine="openpyxl")
        assert "WF Novo Parquet" in raw["Workflow"].astype(str).tolist()
        assert len(out[out[COLUNA_CONFIG_WORKFLOW] == "WF Tela A"]) == 1
        assert any("WF Novo Parquet" in w for w in warnings) or any(
            "PENDENTE" in w and "WF Novo Parquet" in w for w in warnings
        )

    linhas = [
        {
            COLUNA_PROTOCOLO: "1",
            COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
            "Data de Análise": "01/06/2026 10:00:00",
            COLUNA_CONFIG_CLIENTE: "Cliente A",
        },
        {
            COLUNA_PROTOCOLO: "2",
            COLUNA_WORKFLOW_PARQUET: "WF Novo Parquet",
            "Data de Análise": "01/06/2026 10:00:00",
            COLUNA_CONFIG_CLIENTE: "Cliente Novo",
        },
    ]
    _with_fixture(linhas, _run)


def test_validar_replicacao_aud_d1_escala_aplica_ensure_d1(monkeypatch):
    from app.config.paths import PASTA_REPLICACAO_AUD_D1_CONFIG, ESCALA_AUDITORES_D1_CSV
    from app.services.robot_manager import RobotProcessManager, REPLICACAO_AUD_D1_CONFIG_DEFAULT

    captured: dict = {}

    def _fake_validar(settings):
        captured.update(settings or {})

    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning.validar_escala_replicacao_pre_exec",
        _fake_validar,
    )

    merged = {key: merged_val for key, merged_val in REPLICACAO_AUD_D1_CONFIG_DEFAULT.items()}
    merged["usar_escala_auditores"] = True
    merged["replicacao_config_base"] = ""
    merged["escala_auditores_csv"] = ""

    ok, msg = RobotProcessManager._validar_replicacao_aud_d1_escala(merged)
    assert ok is True
    assert msg == ""
    assert str(PASTA_REPLICACAO_AUD_D1_CONFIG) in str(captured.get("replicacao_config_base", ""))
    assert str(ESCALA_AUDITORES_D1_CSV) == str(captured.get("escala_auditores_csv", ""))

