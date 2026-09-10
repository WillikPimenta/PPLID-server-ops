"""Testes do mix Manual × Automático (matrícula 0/1) na seleção D-1."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.bots.replicacao_aud_d1_planning import (
    _alocar_cotas_mix,
    _resolver_mix_manual_automatico,
    selecionar_protocolos_com_mix_matricula,
)
from app.bots.replicacao_aud_planning import COLUNA_DATA_ANALISE, COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET


def test_alocar_cotas_mix_70_30():
    assert _alocar_cotas_mix(10, 70, 30) == (7, 3)
    assert _alocar_cotas_mix(1, 100, 0) == (1, 0)
    assert _alocar_cotas_mix(5, 0, 100) == (0, 5)


def test_resolver_mix_desligado_por_default():
    usar, m, a = _resolver_mix_manual_automatico({})
    assert usar is False
    assert m == 70
    assert a == 30


def test_selecionar_mix_respeita_proporcao():
    rows = []
    # 8 manuais (flag 0) + 8 automáticos (flag 1) no mesmo workflow/hora
    for i in range(8):
        rows.append(
            {
                COLUNA_PROTOCOLO: f"M{i}",
                COLUNA_WORKFLOW_PARQUET: "WF Mix",
                COLUNA_DATA_ANALISE: datetime(2026, 8, 1, 10, 0),
                "matrícula": 0,
            }
        )
    for i in range(8):
        rows.append(
            {
                COLUNA_PROTOCOLO: f"A{i}",
                COLUNA_WORKFLOW_PARQUET: "WF Mix",
                COLUNA_DATA_ANALISE: datetime(2026, 8, 1, 10, 0),
                "matrícula": 1,
            }
        )
    df = pd.DataFrame(rows)
    sel, excl, det = selecionar_protocolos_com_mix_matricula(
        df,
        "WF Mix",
        10,
        pct_manual=70,
        pct_automatico=30,
        seed=42,
    )
    assert excl == 0
    assert len(sel) == 10
    assert det["cota_manual"] == 7
    assert det["cota_automatico"] == 3
    flags = sel["matrícula"].astype(int)
    assert int((flags == 0).sum()) == 7
    assert int((flags == 1).sum()) == 3


def test_selecionar_mix_fallback_quando_pool_insuficiente():
    rows = []
    for i in range(2):
        rows.append(
            {
                COLUNA_PROTOCOLO: f"M{i}",
                COLUNA_WORKFLOW_PARQUET: "WF Mix",
                COLUNA_DATA_ANALISE: datetime(2026, 8, 1, 10, 0),
                "matrícula": 0,
            }
        )
    for i in range(20):
        rows.append(
            {
                COLUNA_PROTOCOLO: f"A{i}",
                COLUNA_WORKFLOW_PARQUET: "WF Mix",
                COLUNA_DATA_ANALISE: datetime(2026, 8, 1, 10, 0),
                "matrícula": 1,
            }
        )
    df = pd.DataFrame(rows)
    sel, _, det = selecionar_protocolos_com_mix_matricula(
        df,
        "WF Mix",
        10,
        pct_manual=70,
        pct_automatico=30,
        seed=7,
    )
    assert len(sel) == 10
    # Só 2 manuais → completa preferência com automático
    assert det["salvos_manual"] == 2
    assert det["salvos_automatico"] == 8
    assert det["fallback_automatico"] >= 1


def test_selecionar_mix_completa_com_manual_quando_automatico_insuficiente():
    rows = []
    for i in range(20):
        rows.append(
            {
                COLUNA_PROTOCOLO: f"M{i}",
                COLUNA_WORKFLOW_PARQUET: "WF Mix",
                COLUNA_DATA_ANALISE: datetime(2026, 8, 1, 10, 0),
                "matrícula": 0,
            }
        )
    for i in range(2):
        rows.append(
            {
                COLUNA_PROTOCOLO: f"A{i}",
                COLUNA_WORKFLOW_PARQUET: "WF Mix",
                COLUNA_DATA_ANALISE: datetime(2026, 8, 1, 10, 0),
                "matrícula": 1,
            }
        )
    df = pd.DataFrame(rows)
    sel, _, det = selecionar_protocolos_com_mix_matricula(
        df,
        "WF Mix",
        10,
        pct_manual=70,
        pct_automatico=30,
        seed=11,
    )
    assert len(sel) == 10
    # Só 2 automáticos → completa preferência com manual
    assert det["salvos_automatico"] == 2
    assert det["salvos_manual"] == 8
    assert det["fallback_manual"] >= 1
