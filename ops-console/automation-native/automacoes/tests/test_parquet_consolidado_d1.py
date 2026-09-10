"""Testes do parquet consolidado BI da replicação D-1."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from app.bots.replicacao_aud_excel_format import ordenar_colunas_resumo

from app.bots.replicacao_aud_d1_planning import (
    PlanoReplicacao,
    _montar_dataframe_parquet_bi,
    exportar_parquet_consolidado_d1,
)
from app.bots.replicacao_aud_planning import (
    COLUNA_CONFIG_CATEGORIA,
    COLUNA_CONFIG_CLIENTE,
    COLUNA_CONFIG_SEGMENTO,
    COLUNA_DATA_ANALISE,
    COLUNA_PROTOCOLO,
    COLUNA_WORKFLOW_PARQUET,
    resolver_canal_destino,
)
from app.config import ARQUIVO_PARQUET_CONSOLIDADO_D1, REPLICACAO_FILA_DOCUMENTOSCOPIA_31, REPLICACAO_FILA_G_AUDITORIA


def test_ordenar_colunas_resumo_d1():
    df = pd.DataFrame(
        [
            {
                "Workflow": "WF1",
                "Canal Destino": "BRFlow",
                "RunId": "hidden",
                "Status": "OK",
            }
        ]
    )
    out = ordenar_colunas_resumo(df, d1=True)
    assert out.columns[0] == "Workflow"
    assert "Canal Destino" in out.columns
    assert out.columns.get_loc("Canal Destino") < out.columns.get_loc("Status")


def test_resolver_canal_destino():
    assert resolver_canal_destino(REPLICACAO_FILA_G_AUDITORIA) == "BRFlow"
    assert resolver_canal_destino(REPLICACAO_FILA_DOCUMENTOSCOPIA_31) == "Case Manager"


def _plano_minimo(run_id: str = "20260706_120000") -> PlanoReplicacao:
    return PlanoReplicacao(
        data_referencia=datetime(2026, 7, 5),
        pasta_protocolos=Path("/tmp/protocolos") / run_id,
        pasta_resumo=Path("/tmp/resumo"),
        run_id=run_id,
        workflow_fila={"WF G": REPLICACAO_FILA_G_AUDITORIA, "WF Case": REPLICACAO_FILA_DOCUMENTOSCOPIA_31},
        workflow_brflow={"WF G": "G Auditoria Origem", "WF Case": "Doc 3.1 Origem"},
        resumo=[
            {
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                COLUNA_CONFIG_CLIENTE: "Cliente A",
                COLUNA_CONFIG_SEGMENTO: "Seg",
                COLUNA_CONFIG_CATEGORIA: "Cat",
                "Canal Destino": "BRFlow",
                "Status": "OK",
                "Pct Atingido": 100.0,
                "Amostra Efetiva": 2,
            },
            {
                "Workflow": "WF Case",
                "Workflow D1": "wf_case_d1",
                COLUNA_CONFIG_CLIENTE: "Cliente B",
                COLUNA_CONFIG_SEGMENTO: "Seg2",
                COLUNA_CONFIG_CATEGORIA: "Cat2",
                "Canal Destino": "Case Manager",
                "Status": "OK",
                "Pct Atingido": 50.0,
                "Amostra Efetiva": 1,
            },
        ],
        parquet_referencia="brflow-detalhado-tratado_20260705.parquet",
        data_referencia_d1_fmt="05/07/2026",
        data_execucao_fmt="06/07/2026 12:00",
    )


def test_montar_dataframe_parquet_bi_colunas():
    plano = _plano_minimo()
    df_plano = pd.DataFrame(
        [
            {
                COLUNA_PROTOCOLO: "P001",
                COLUNA_WORKFLOW_PARQUET: "wf_g_d1",
                COLUNA_DATA_ANALISE: datetime(2026, 7, 5, 10, 30),
                "Hora": 10,
                "WorkflowConfig": "WF G",
                "Canal Destino": "BRFlow",
                "QuotaHora": 1,
            },
            {
                COLUNA_PROTOCOLO: "P002",
                COLUNA_WORKFLOW_PARQUET: "wf_case_d1",
                COLUNA_DATA_ANALISE: datetime(2026, 7, 5, 11, 0),
                "Hora": 11,
                "WorkflowConfig": "WF Case",
                "Canal Destino": "Case Manager",
            },
        ]
    )
    estado = {
        "workflows": {
            "WF G": {"status": "SALVO_OK", "atualizado_em": "2026-07-06T12:05:00", "workflow_brflow": "G Auditoria Origem"},
            "WF Case": {"status": "ERRO", "atualizado_em": "2026-07-06T12:10:00", "workflow_brflow": "Doc 3.1 Origem"},
        },
        "data_referencia_d1": "05/07/2026",
        "data_execucao": "06/07/2026 12:00",
        "parquet_referencia": "brflow-detalhado-tratado_20260705.parquet",
    }
    out = _montar_dataframe_parquet_bi(plano, estado, df_plano)
    assert len(out) == 2
    assert set(out["canal_destino"]) == {"BRFlow", "Case Manager"}
    assert out.loc[out["protocolo"] == "P001", "status_brflow"].iloc[0] == "SALVO_OK"
    assert out.loc[out["protocolo"] == "P001", "data_hora_upload_brflow"].iloc[0]
    assert out.loc[out["protocolo"] == "P002", "status_brflow"].iloc[0] == "ERRO"
    assert out["run_id"].iloc[0] == plano.run_id


def test_export_parquet_append_idempotente(tmp_path: Path, monkeypatch):
    bi_dir = tmp_path / "bi"
    destino = bi_dir / ARQUIVO_PARQUET_CONSOLIDADO_D1
    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning._caminho_parquet_consolidado_d1",
        lambda settings=None: destino,
    )

    plano = _plano_minimo("run_a")
    df_plano = pd.DataFrame(
        [
            {
                COLUNA_PROTOCOLO: "PX1",
                COLUNA_WORKFLOW_PARQUET: "wf_g_d1",
                COLUNA_DATA_ANALISE: datetime(2026, 7, 5, 9, 0),
                "Hora": 9,
                "WorkflowConfig": "WF G",
                "Canal Destino": "BRFlow",
            }
        ]
    )
    estado = {"workflows": {"WF G": {"status": "SALVO_OK", "atualizado_em": "2026-07-06T12:00:00"}}}

    path1 = exportar_parquet_consolidado_d1(plano, estado, df_plano=df_plano)
    assert path1 == destino
    assert destino.exists()
    df1 = pd.read_parquet(destino)
    assert len(df1) == 1
    assert df1["protocolo"].iloc[0] == "PX1"

    estado2 = {"workflows": {"WF G": {"status": "ERRO", "atualizado_em": "2026-07-06T13:00:00"}}}
    exportar_parquet_consolidado_d1(plano, estado2, df_plano=df_plano)
    df2 = pd.read_parquet(destino)
    assert len(df2) == 1
    assert df2["status_brflow"].iloc[0] == "ERRO"

    plano_b = _plano_minimo("run_b")
    df_plano_b = df_plano.copy()
    df_plano_b[COLUNA_PROTOCOLO] = "PY1"
    exportar_parquet_consolidado_d1(plano_b, estado, df_plano=df_plano_b)
    df3 = pd.read_parquet(destino)
    assert len(df3) == 2
    assert set(df3["run_id"]) == {"run_a", "run_b"}
