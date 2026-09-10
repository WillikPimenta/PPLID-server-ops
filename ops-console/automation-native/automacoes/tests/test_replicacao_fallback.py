"""Tests for explicit Parquet fallback settings."""

import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_d1_planning import (
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_CONFIG_WORKFLOW_D1,
    COLUNA_DATA_ANALISE,
    COLUNA_PROTOCOLO,
    COLUNA_WORKFLOW_PARQUET,
    _carregar_pool_retroativo_parquet,
    expandir_pool_retroativo,
)
from app.bots.replicacao_aud_planning import _parse_bool_setting


def test_parse_bool_setting_preserves_explicit_false_values():
    assert _parse_bool_setting("false", default=True) is False
    assert _parse_bool_setting("0", default=True) is False
    assert _parse_bool_setting("true", default=False) is True
    assert _parse_bool_setting("invalid-value", default=True) is True


def _retro_frame(protocol: str, workflow: str = "WF A") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                COLUNA_PROTOCOLO: protocol,
                COLUNA_WORKFLOW_PARQUET: workflow,
                COLUNA_DATA_ANALISE: "01/08/2026 10:00:00",
            }
        ]
    )


def test_retroativo_parquet_resolve_um_arquivo_por_dia_e_reusa_fallback():
    inicio = date(2026, 8, 1)
    fim = date(2026, 8, 4)
    p1 = Path("brflow-detalhado-tratado_20260801.parquet")
    fallback = Path("brflow-detalhado-tratado_20260731.parquet")
    p4 = Path("brflow-detalhado-tratado_20260804.parquet")
    resolvidos = {
        date(2026, 8, 1): p1,
        date(2026, 8, 2): fallback,
        date(2026, 8, 3): fallback,
        date(2026, 8, 4): p4,
    }
    lidos = {
        p1: _retro_frame("P1"),
        fallback: _retro_frame("PF"),
        p4: _retro_frame("P4"),
    }
    chamadas = []
    leituras = []
    plano = SimpleNamespace(warnings=[])

    def resolve(data_ref, fallback_ultimo):
        chamadas.append((data_ref.date(), fallback_ultimo))
        return resolvidos[data_ref.date()]

    def read(path, log=None):
        leituras.append(path)
        return lidos[path]

    with patch(
        "app.bots.replicacao_aud_d1_planning.resolver_parquet_d1",
        side_effect=resolve,
    ), patch(
        "app.bots.replicacao_aud_d1_planning.ler_parquet",
        side_effect=read,
    ):
        result = _carregar_pool_retroativo_parquet(
            inicio=inicio,
            fim=fim,
            workflow_d1_set={"wf a"},
            fallback_ultimo_parquet=True,
            plano=plano,
        )

    assert [item[0] for item in chamadas] == [
        inicio,
        date(2026, 8, 2),
        date(2026, 8, 3),
        fim,
    ]
    assert all(item[1] is True for item in chamadas)
    assert leituras == [p1, fallback, p4]
    assert result[COLUNA_PROTOCOLO].tolist() == ["P1", "PF", "PF", "P4"]
    assert result["_selection_reason"].tolist() == [
        "retroativo:2026-08-01",
        "retroativo:2026-08-02",
        "retroativo:2026-08-03",
        "retroativo:2026-08-04",
    ]
    assert len(plano.warnings) == 2


def test_retroativo_parquet_sem_fallback_propaga_ausencia_do_dia():
    def resolve(data_ref, fallback_ultimo):
        raise FileNotFoundError("parquet ausente")

    with patch(
        "app.bots.replicacao_aud_d1_planning.resolver_parquet_d1",
        side_effect=resolve,
    ), pytest.raises(FileNotFoundError):
        _carregar_pool_retroativo_parquet(
            inicio=date(2026, 8, 1),
            fim=date(2026, 8, 1),
            workflow_d1_set={"wf a"},
            fallback_ultimo_parquet=False,
            plano=SimpleNamespace(warnings=[]),
        )


def test_expandir_pool_retroativo_usa_parquet_quando_fonte_banco_desativada():
    settings = {
        "persistent": {
            "retroativo": {
                "retroativo_ativo": True,
                "retroativo_data_inicio": "2026-08-01",
                "retroativo_data_fim": "2026-08-01",
                "workflows": [
                    {
                        "nome_d1": "WF A",
                        "chave_d1_normalizada": "wf-a",
                        "chave_normalizada": "wf-a-config",
                        "nome_canonico": "WF A",
                        "ativo": True,
                    }
                ],
            }
        }
    }
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF A Config"],
            COLUNA_CONFIG_WORKFLOW_D1: ["WF A"],
        }
    )
    retro = _retro_frame("RETRO")
    plano = SimpleNamespace(warnings=[])

    with patch(
        "app.bots.replicacao_aud_d1_planning._carregar_pool_retroativo_parquet",
        return_value=retro,
    ) as carregar:
        result = expandir_pool_retroativo(
            pd.DataFrame(columns=retro.columns),
            config,
            settings=settings,
            data_ref=datetime(2026, 8, 1),
            plano=plano,
            database_only=False,
            fallback_ultimo_parquet=True,
        )

    carregar.assert_called_once()
    assert result[COLUNA_PROTOCOLO].tolist() == ["RETRO"]


def test_fallback_parquet_dias_ausentes_ativo_reads_top_level_and_snapshot():
    from app.bots.replicacao_aud_d1_planning import _ensure_d1_settings
    from app.bots.replicacao_d1_db_bridge import fallback_parquet_dias_ausentes_ativo

    assert fallback_parquet_dias_ausentes_ativo({"fallback_parquet_dias_ausentes": True}) is True
    assert fallback_parquet_dias_ausentes_ativo({"fallback_parquet_dias_ausentes": False}) is False
    assert fallback_parquet_dias_ausentes_ativo(
        {"_execution_snapshot": {"persistent": {"fallback_parquet_dias_ausentes": True}}}
    ) is True
    assert fallback_parquet_dias_ausentes_ativo({}) is False

    ensured = _ensure_d1_settings(
        {"_execution_snapshot": {"persistent": {"fallback_parquet_dias_ausentes": True}}}
    )
    assert ensured["fallback_parquet_dias_ausentes"] is True
    assert fallback_parquet_dias_ausentes_ativo(ensured) is True


def test_expandir_pool_retroativo_hybrid_when_database_only_and_flag_on():
    settings = {
        "fallback_parquet_dias_ausentes": True,
        "persistent": {
            "retroativo": {
                "retroativo_ativo": True,
                "retroativo_data_inicio": "2026-08-01",
                "retroativo_data_fim": "2026-08-01",
                "workflows": [
                    {
                        "nome_d1": "WF A",
                        "chave_d1_normalizada": "wf-a",
                        "chave_normalizada": "wf-a-config",
                        "nome_canonico": "WF A",
                        "ativo": True,
                    }
                ],
            }
        },
    }
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF A Config"],
            COLUNA_CONFIG_WORKFLOW_D1: ["WF A"],
        }
    )
    retro = _retro_frame("HYBRID")
    retro["_selection_reason"] = "retroativo:2026-08-01:parquet"
    plano = SimpleNamespace(warnings=[])

    with patch(
        "app.bots.replicacao_d1_db_bridge.load_retro_source_hybrid_db_parquet",
        return_value=retro,
    ) as carregar_hibrido:
        result = expandir_pool_retroativo(
            pd.DataFrame(columns=retro.columns),
            config,
            settings=settings,
            data_ref=datetime(2026, 8, 2),
            plano=plano,
            database_only=True,
        )

    carregar_hibrido.assert_called_once()
    assert result[COLUNA_PROTOCOLO].tolist() == ["HYBRID"]


def test_expandir_pool_retroativo_hybrid_quando_workflow_ausente_no_dia():
    settings = {
        "fallback_parquet_dias_ausentes": True,
        "persistent": {
            "retroativo": {
                "retroativo_ativo": True,
                "retroativo_data_inicio": "2026-08-02",
                "retroativo_data_fim": "2026-08-05",
                "workflows": [
                    {
                        "nome_d1": "WF Retro Only",
                        "chave_d1_normalizada": "wf retro only",
                        "chave_normalizada": "wf-retro-only",
                        "nome_canonico": "WF Retro Only",
                        "ativo": True,
                    }
                ],
            }
        },
    }
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["Outro WF"],
            COLUNA_CONFIG_WORKFLOW_D1: ["Outro WF D1"],
        }
    )
    retro = _retro_frame("HYBRID-ONLY", workflow="WF Retro Only")
    retro["_selection_reason"] = "retroativo:2026-08-02:parquet"
    plano = SimpleNamespace(warnings=[])

    with patch(
        "app.bots.replicacao_d1_db_bridge.load_retro_source_hybrid_db_parquet",
        return_value=retro,
    ) as carregar_hibrido:
        result = expandir_pool_retroativo(
            pd.DataFrame(columns=retro.columns),
            config,
            settings=settings,
            data_ref=datetime(2026, 8, 13),
            plano=plano,
            database_only=True,
        )

    carregar_hibrido.assert_called_once()
    assert result[COLUNA_PROTOCOLO].tolist() == ["HYBRID-ONLY"]
    assert any("sem registro no D-1 do dia" in w for w in plano.warnings)
