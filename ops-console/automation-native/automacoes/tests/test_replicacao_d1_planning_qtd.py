# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from app.bots.replicacao_aud_d1_planning import (
    _append_resumo_modo_qtd,
    _calcular_amostra_base_qtd,
    _protocolos_exportaveis_por_modo,
    eh_fila_modo_qtd,
    inicializar_estado_execucao,
)
from app.bots.replicacao_aud_planning import (
    PlanoReplicacao,
    REPLICACAO_MODO_PROTOCOLOS,
    REPLICACAO_MODO_QTD,
)


def test_calcular_amostra_base_qtd_sem_override():
    assert _calcular_amostra_base_qtd(amostra=42, amostra_override_pct=None, total_disponivel=100) == 42


def test_calcular_amostra_base_qtd_override_pct():
    assert _calcular_amostra_base_qtd(amostra=100, amostra_override_pct=50, total_disponivel=200) == 50


def test_calcular_amostra_base_qtd_override_100_usa_disponivel():
    assert _calcular_amostra_base_qtd(amostra=10, amostra_override_pct=100, total_disponivel=77) == 77


def test_append_resumo_modo_qtd_popula_resumo_sem_protocolos():
    plano = PlanoReplicacao(
        run_id="test_qtd",
        data_referencia=datetime(2026, 8, 10),
        pasta_protocolos=Path("."),
        pasta_resumo=Path("."),
    )
    plano.workflow_fila = {"WF Bio": "Bio"}
    item = {
        "workflow": "WF Bio",
        "workflow_d1": "WF Bio D1",
        "amostra_diaria": 40,
        "amostra": 42,
        "row": pd.Series({"Workflow": "WF Bio", "Cliente": "Cliente A"}),
    }
    info = SimpleNamespace(
        usar_escala=True,
        data_escala=None,
        auditores_ativos=2,
        auditores_ativos_case=1,
    )
    _append_resumo_modo_qtd(
        plano,
        workflow="WF Bio",
        workflow_d1="WF Bio D1",
        item=item,
        amostra_efetiva_final=45,
        amostra_solicitada=42,
        amostra_redistribuida=3,
        total_disponivel=100,
        meta_base={"RunId": "test_qtd"},
        info_capacidade=info,
        parquet_ref="",
    )
    assert len(plano.resumo) == 1
    assert plano.resumo[0]["Amostra Efetiva"] == 45
    assert plano.resumo[0]["Protocolos Salvos"] == 0
    assert plano.resumo[0]["Arquivo CSV"] == "— (modo qtd)"
    assert "Modo quantidade" in plano.resumo[0]["Observacao"]
    assert eh_fila_modo_qtd("Bio")
    assert eh_fila_modo_qtd("Redoc")
    assert not eh_fila_modo_qtd("G auditoria")


def test_estado_e_exportacao_usam_modo_explicito_independente_da_fila(monkeypatch):
    plano = PlanoReplicacao(
        run_id="test_misto",
        data_referencia=datetime(2026, 8, 10),
        pasta_protocolos=Path("."),
        pasta_resumo=Path("."),
        estado_execucao_path=Path("estado_test_misto.json"),
    )
    plano.workflows = ["WF G QTD", "WF 31 QTD", "WF Bio CSV", "WF Redoc CSV"]
    plano.workflow_fila = {
        "WF G QTD": "G auditoria",
        "WF 31 QTD": "3.1",
        "WF Bio CSV": "Bio",
        "WF Redoc CSV": "Redoc",
    }
    plano.workflow_modo_replicacao = {
        "WF G QTD": REPLICACAO_MODO_QTD,
        "WF 31 QTD": REPLICACAO_MODO_QTD,
        "WF Bio CSV": REPLICACAO_MODO_PROTOCOLOS,
        "WF Redoc CSV": REPLICACAO_MODO_PROTOCOLOS,
    }
    plano.qtd_por_workflow = {"WF G QTD": 17, "WF 31 QTD": 8}
    plano.protocolos_por_workflow = {
        "WF G QTD": [],
        "WF 31 QTD": [],
        "WF Bio CSV": ["B1"],
        "WF Redoc CSV": ["R1"],
    }
    plano.workflow_regra_brflow = {"WF Redoc CSV": "Regra Redoc"}

    monkeypatch.setattr(
        "app.bots.replicacao_aud_d1_planning.salvar_estado_execucao",
        lambda *_args, **_kwargs: None,
    )
    estado = inicializar_estado_execucao(plano)

    assert estado["workflows"]["WF G QTD"]["modo"] == REPLICACAO_MODO_QTD
    assert estado["workflows"]["WF G QTD"]["qtd_calculada"] == 17
    assert estado["workflows"]["WF G QTD"]["status"] == "PENDENTE"
    assert estado["workflows"]["WF 31 QTD"]["modo"] == REPLICACAO_MODO_QTD
    assert estado["workflows"]["WF 31 QTD"]["qtd_calculada"] == 8
    assert estado["workflows"]["WF Bio CSV"]["modo"] == REPLICACAO_MODO_PROTOCOLOS
    assert estado["workflows"]["WF Redoc CSV"]["nome_regra_brflow"] == "Regra Redoc"
    assert _protocolos_exportaveis_por_modo(plano) == {
        "WF Bio CSV": ["B1"],
        "WF Redoc CSV": ["R1"],
    }
