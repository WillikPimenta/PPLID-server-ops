"""Testes do modo amostra 100% na replicação D-1."""

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots import replicacao_aud_planning as rap
from app.bots.replicacao_aud_d1_planning import (
    COLUNA_CONFIG_CLIENTE,
    COLUNA_PROTOCOLO,
    COLUNA_WORKFLOW_PARQUET,
    gerar_plano_replicacao_d1,
    redistribuir_amostra_priorizada,
)
from tests.test_replicacao_aud_d1_planning import _criar_fixture_config, _criar_parquet, _with_fixture


def _df_protocolos(linhas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(linhas)


def _patch_plano_paths(monkeypatch, base: Path) -> None:
    parquet_path = base / "brflow-detalhado-tratado_20260601.parquet"
    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning.resolver_parquet_d1",
        lambda data_ref=None, fallback_ultimo=False: parquet_path,
    )
    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning.carregar_protocolos_historico",
        lambda **kwargs: {"0", "1"},
    )
    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning.PASTA_REPLICACAO_AUD_D1_PROTOCOLOS",
        base / "protocolos",
    )
    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning.PASTA_REPLICACAO_AUD_D1_RESUMO",
        base / "resumo",
    )
    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning.caminho_estado_execucao",
        lambda run_id: base / "resumo" / f"execucao_d1_{run_id}.json",
    )


def _linhas_parquet_padrao():
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
    return linhas


def test_carregar_workflows_amostra_100_string():
    keys = rap.carregar_workflows_amostra_100(
        {"replicacao_workflows_amostra_100": "WF Tela A\nWF Tela B, Outro"}
    )
    assert rap._normalizar_workflow("WF Tela A") in keys
    assert rap._normalizar_workflow("WF Tela B") in keys
    assert rap._normalizar_workflow("Outro") in keys


def test_carregar_workflows_amostra_100_lista():
    keys = rap.carregar_workflows_amostra_100(
        {"replicacao_workflows_amostra_100": ["  WF A  ", "", "WF B"]}
    )
    assert rap._normalizar_workflow("WF A") in keys
    assert rap._normalizar_workflow("WF B") in keys
    assert len(keys) == 2


def test_carregar_workflows_amostra_override_dict():
    overrides = rap.carregar_workflows_amostra_override(
        {
            "replicacao_workflows_amostra_pct": {"WF A": 50, "WF B": 100, "": 80, "WF C": "x"},
            "replicacao_workflows_amostra_100": ["WF Legado"],
        }
    )
    assert overrides[rap._normalizar_workflow("WF A")] == 50
    assert overrides[rap._normalizar_workflow("WF B")] == 100
    assert overrides[rap._normalizar_workflow("WF Legado")] == 100
    assert rap._normalizar_workflow("WF C") not in overrides


def test_selecionar_protocolos_por_porcentagem():
    df = _df_protocolos(
        [
            {
                COLUNA_PROTOCOLO: str(i),
                COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
                "Data de Análise": "01/06/2026 10:00:00",
            }
            for i in range(100)
        ]
    )
    selecionados, excl = rap.selecionar_protocolos_por_porcentagem(df, "WF Parquet A", 50, seed=42)
    assert excl == 0
    assert len(selecionados) == 50

    todos, _ = rap.selecionar_protocolos_por_porcentagem(df, "WF Parquet A", 100, seed=42)
    assert len(todos) == 100


def test_selecionar_todos_protocolos_retorna_todos():
    df = _df_protocolos(
        [
            {
                COLUNA_PROTOCOLO: "101",
                COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
                "Data de Análise": "01/06/2026 10:00:00",
            },
            {
                COLUNA_PROTOCOLO: "102",
                COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
                "Data de Análise": "01/06/2026 11:00:00",
            },
            {
                COLUNA_PROTOCOLO: "103",
                COLUNA_WORKFLOW_PARQUET: "WF Parquet B",
                "Data de Análise": "01/06/2026 10:00:00",
            },
        ]
    )
    selecionados, excl = rap.selecionar_todos_protocolos(df, "WF Parquet A")
    assert excl == 0
    assert set(selecionados[COLUNA_PROTOCOLO].astype(str).tolist()) == {"101", "102"}


def test_selecionar_todos_protocolos_respeita_historico():
    df = _df_protocolos(
        [
            {
                COLUNA_PROTOCOLO: "201",
                COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
                "Data de Análise": "01/06/2026 10:00:00",
            },
            {
                COLUNA_PROTOCOLO: "202",
                COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
                "Data de Análise": "01/06/2026 11:00:00",
            },
        ]
    )
    selecionados, excl = rap.selecionar_todos_protocolos(df, "WF Parquet A", historico={"201"})
    assert excl == 1
    assert selecionados[COLUNA_PROTOCOLO].astype(str).tolist() == ["202"]


def test_workflow_override_nao_recebe_redistribuicao():
    fontes = [
        {"workflow": "WF Off", "amostra": 10, "cliente": "Cliente A", "categoria": "Cat A"},
    ]
    destinos_amostra = [
        {
            "workflow": "WF A",
            "amostra": 5,
            "cliente": "Cliente A",
            "categoria": "Cat A",
            "amostra_override_pct": 75,
        },
        {"workflow": "WF B", "amostra": 5, "cliente": "Cliente B", "categoria": "Cat B"},
    ]
    com_d1_redist = [d for d in destinos_amostra if d.get("amostra_override_pct") is None]
    bonus = redistribuir_amostra_priorizada(fontes, com_d1_redist)
    assert bonus.get("WF A", 0) == 0
    assert bonus.get("WF B", 0) == 10


def test_workflow_100_nao_recebe_redistribuicao():
    fontes = [
        {"workflow": "WF Off", "amostra": 10, "cliente": "Cliente A", "categoria": "Cat A"},
    ]
    destinos_amostra = [
        {"workflow": "WF A", "amostra": 5, "cliente": "Cliente A", "categoria": "Cat A", "amostra_override_pct": 100},
        {"workflow": "WF B", "amostra": 5, "cliente": "Cliente B", "categoria": "Cat B", "amostra_override_pct": None},
    ]
    com_d1_redist = [d for d in destinos_amostra if d.get("amostra_override_pct") is None]
    bonus = redistribuir_amostra_priorizada(fontes, com_d1_redist)
    assert bonus.get("WF A", 0) == 0
    assert bonus.get("WF B", 0) == 10


def test_gerar_plano_modo_100_seleciona_todos_disponiveis(monkeypatch):
    captured = {}

    def _run(base, config, df_parquet):
        _patch_plano_paths(monkeypatch, base)
        plano = gerar_plano_replicacao_d1(
            data_ref=datetime(2026, 6, 1),
            settings={
                "replicacao_config_base": str(config),
                "fonte_banco_ativa": False,
                "replicacao_sincronizar_workflow_d1": False,
                "usar_escala_auditores": False,
                "excluir_historico": True,
                "replicacao_workflows_amostra_pct": {"WF Tela A": 100},
                "run_id": "test_100",
            },
            salvar_plano_detalhado=False,
        )
        captured["plano"] = plano

    _with_fixture(_linhas_parquet_padrao(), _run)
    plano = captured["plano"]
    protocolos_a = plano.protocolos_por_workflow.get("WF Tela A", [])
    assert len(protocolos_a) == 98
    linha_a = next(r for r in plano.resumo if r.get("Workflow") == "WF Tela A")
    assert linha_a["Amostra Solicitada"] == "100%"
    assert linha_a["Protocolos Salvos"] == 98
    assert "100%" in linha_a["Observacao"] or "100% do D-1" in linha_a["Observacao"]

    linha_b = next(r for r in plano.resumo if r.get("Workflow") == "WF Tela B")
    assert linha_b["Amostra Solicitada"] != "100%"
    assert linha_b["Protocolos Salvos"] < 50


def test_gerar_plano_workflow_100_desconhecido_gera_warning(monkeypatch):
    captured = {}

    def _run(base, config, df_parquet):
        _patch_plano_paths(monkeypatch, base)
        monkeypatch.setattr(
            "app.bots.replicacao_aud_d1_planning.carregar_protocolos_historico",
            lambda **kwargs: set(),
        )
        plano = gerar_plano_replicacao_d1(
            data_ref=datetime(2026, 6, 1),
            settings={
                "replicacao_config_base": str(config),
                "fonte_banco_ativa": False,
                "replicacao_sincronizar_workflow_d1": False,
                "usar_escala_auditores": False,
                "replicacao_workflows_amostra_100": "Workflow Inexistente",
                "run_id": "test_warn",
            },
            salvar_plano_detalhado=False,
        )
        captured["plano"] = plano

    linhas = [
        {
            COLUNA_PROTOCOLO: "1",
            COLUNA_WORKFLOW_PARQUET: "WF Parquet A",
            "Data de Análise": "01/06/2026 10:00:00",
            COLUNA_CONFIG_CLIENTE: "Cliente A",
        }
    ]
    _with_fixture(linhas, _run)
    plano = captured["plano"]
    assert any("Amostra por %" in w and "não encontrado" in w for w in plano.warnings)


def test_robot_manager_persiste_workflows_amostra_pct():
    from app.services.robot_manager import RobotProcessManager

    cfg = RobotProcessManager._normalize_robot_config(
        {
            "replicacao_workflows_amostra_pct": {" WF A ": 50, "WF B": 150, "WF C": "x"},
        },
        "replicacao_auditoria_d1",
    )
    assert cfg["replicacao_workflows_amostra_pct"] == {"WF A": 50, "WF B": 100}


def test_robot_manager_persiste_workflows_amostra_100():
    from app.services.robot_manager import RobotProcessManager

    cfg = RobotProcessManager._normalize_robot_config(
        {
            "replicacao_workflows_amostra_100": [" WF A ", "WF B", "WF A"],
        },
        "replicacao_auditoria_d1",
    )
    assert cfg["replicacao_workflows_amostra_100"] == ["WF A", "WF B"]

    cfg_str = RobotProcessManager._normalize_robot_config(
        {"replicacao_workflows_amostra_100": "WF X\nWF Y, WF Z"},
        "replicacao_auditoria",
    )
    assert cfg_str["replicacao_workflows_amostra_100"] == ["WF X", "WF Y", "WF Z"]
