# -*- coding: utf-8 -*-
from datetime import datetime

import pandas as pd

from app.bots import replicacao_aud_planning as rap
from app.bots.replicacao_aud_d1_planning import (
    COLUNA_CONFIG_CATEGORIA,
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_CONFIG_WORKFLOW_D1,
    COLUNA_DATA_ANALISE,
    COLUNA_PROTOCOLO,
    COLUNA_WORKFLOW_PARQUET,
    carregar_config_retroativa,
    expandir_pool_retroativo,
    selecionar_protocolos_retroativo_split,
)
from types import SimpleNamespace
from unittest.mock import patch


def test_carregar_config_retroativa_returns_none_when_inactive():
    settings = {
        "_execution_snapshot": {
            "persistent": {
                "retroativo": {
                    "retroativo_ativo": False,
                    "retroativo_data_inicio": "2026-08-01",
                    "retroativo_data_fim": "2026-08-07",
                    "workflows": [
                        {
                            "id": 1,
                            "nome_canonico": "WF A",
                            "nome_d1": "WF D1",
                            "chave_normalizada": "wf-a",
                            "chave_d1_normalizada": "wf-d1",
                            "ativo": True,
                        }
                    ],
                }
            }
        }
    }
    assert carregar_config_retroativa(settings) is None


def test_carregar_config_retroativa_reads_workflows():
    settings = {
        "_execution_snapshot": {
            "persistent": {
                "retroativo": {
                    "retroativo_ativo": True,
                    "retroativo_data_inicio": "2026-08-01",
                    "retroativo_data_fim": "2026-08-07",
                    "workflows": [
                        {
                            "id": 1,
                            "nome_canonico": "WF A",
                            "nome_d1": "WF D1 Parquet",
                            "chave_normalizada": "wf-a",
                            "chave_d1_normalizada": "wf-d1-parquet",
                            "ativo": True,
                        },
                    ],
                }
            }
        }
    }
    cfg = carregar_config_retroativa(settings)
    assert cfg is not None
    assert cfg["data_inicio"] == "2026-08-01"
    assert "wf d1 parquet" in cfg["workflow_d1_keys"] or len(cfg["workflow_d1_keys"]) >= 1


def _row(protocol: str, workflow: str, data: str, *, retro_day: str | None = None) -> dict:
    row = {
        COLUNA_PROTOCOLO: protocol,
        COLUNA_WORKFLOW_PARQUET: workflow,
        COLUNA_DATA_ANALISE: data,
    }
    if retro_day:
        row["_selection_reason"] = f"retroativo:{retro_day}"
    return row


def test_selecionar_protocolos_retroativo_split_distribui_por_dia():
    workflow = "WF Retro D1"
    rows = [
        _row(f"REF{i}", workflow, f"12/08/2026 {8 + i:02d}:00:00")
        for i in range(6)
    ]
    for day in ("2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"):
        for i in range(5):
            rows.append(
                _row(
                    f"R{day[-2:]}{i}",
                    workflow,
                    f"{day[8:10]}/08/2026 {9 + i:02d}:00:00",
                    retro_day=day,
                )
            )
    df = pd.DataFrame(rows)
    sel, _, avisos = selecionar_protocolos_retroativo_split(
        df,
        workflow,
        8,
        pct_referencia=50,
        seed=42,
    )
    assert len(sel) == 8
    ref_count = len(sel[~sel["_selection_reason"].astype(str).str.startswith("retroativo:", na=False)])
    retro_sel = sel[sel["_selection_reason"].astype(str).str.startswith("retroativo:", na=False)]
    retro_days = {
        rap._extrair_dia_retroativo(row)
        for _, row in retro_sel.iterrows()
    }
    assert ref_count == 4
    assert len(retro_days) >= 3
    assert not avisos or all("referência D-1 vazia" not in a for a in avisos)


def test_selecionar_protocolos_retroativo_split_dedupes_mesmo_protocolo_normalizado():
    workflow = "WF Retro D1"
    df = pd.DataFrame([
        _row("011743", workflow, "12/08/2026 09:00:00"),
        _row("11743", workflow, "03/08/2026 10:00:00", retro_day="2026-08-03"),
    ])
    sel, _, _ = selecionar_protocolos_retroativo_split(
        df,
        workflow,
        2,
        pct_referencia=50,
        seed=42,
    )
    assert len(sel) == 1
    assert str(sel.iloc[0][COLUNA_PROTOCOLO]) in {"11743", "011743"}


def test_expandir_pool_retroativo_preserva_mesmo_protocolo_em_dias_diferentes():
    base = pd.DataFrame(columns=[COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET, COLUNA_DATA_ANALISE])
    retro = pd.DataFrame(
        [
            _row("999", "WF A", "02/08/2026 10:00:00", retro_day="2026-08-02"),
            _row("999", "WF A", "05/08/2026 11:00:00", retro_day="2026-08-05"),
        ]
    )
    settings = {
        "persistent": {
            "retroativo": {
                "retroativo_ativo": True,
                "retroativo_data_inicio": "2026-08-02",
                "retroativo_data_fim": "2026-08-05",
                "workflows": [
                    {
                        "nome_d1": "WF A",
                        "chave_d1_normalizada": "wf a",
                        "chave_normalizada": "wf-a",
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
    plano = SimpleNamespace(warnings=[], run_id="test")

    with patch(
        "app.bots.replicacao_aud_d1_planning._carregar_pool_retroativo_parquet",
        return_value=retro,
    ):
        result = expandir_pool_retroativo(
            base,
            config,
            settings=settings,
            data_ref=datetime(2026, 8, 12),
            plano=plano,
            database_only=False,
        )

    assert len(result) == 2
    retro_days = {
        rap._extrair_dia_retroativo(row)
        for _, row in result.iterrows()
    }
    assert retro_days == {"2026-08-02", "2026-08-05"}


def test_expandir_pool_retroativo_continua_quando_workflow_ausente_no_dia():
    """Workflow retroativo sem registro no D-1 de referência ainda carrega o intervalo."""
    base = pd.DataFrame(columns=[COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET, COLUNA_DATA_ANALISE])
    retro = pd.DataFrame(
        [
            _row("101", "WF Retro Only", "02/08/2026 10:00:00", retro_day="2026-08-02"),
            _row("102", "WF Retro Only", "05/08/2026 11:00:00", retro_day="2026-08-05"),
        ]
    )
    settings = {
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
        }
    }
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["Outro WF"],
            COLUNA_CONFIG_WORKFLOW_D1: ["Outro WF D1"],
        }
    )
    plano = SimpleNamespace(warnings=[], run_id="test")

    with patch(
        "app.bots.replicacao_aud_d1_planning._carregar_pool_retroativo_parquet",
        return_value=retro,
    ) as carregar:
        result = expandir_pool_retroativo(
            base,
            config,
            settings=settings,
            data_ref=datetime(2026, 8, 13),
            plano=plano,
            database_only=False,
        )

    carregar.assert_called_once()
    assert len(result) == 2
    assert any("sem registro no D-1 do dia" in w for w in plano.warnings)
    assert any("pool será carregado do intervalo retroativo" in w for w in plano.warnings)


def test_complementar_itens_config_retroativo_inclui_workflow_somente_retroativo():
    from app.bots.replicacao_aud_d1_planning import (
        COLUNA_CONFIG_CATEGORIA,
        COLUNA_CONFIG_CLIENTE,
        COLUNA_CONFIG_FILA,
        COLUNA_CONFIG_SEGMENTO,
        _complementar_itens_config_retroativo,
    )

    df = pd.DataFrame(
        [
            _row("201", "WF Retro Only", "02/08/2026 10:00:00", retro_day="2026-08-02"),
            _row("202", "WF Retro Only", "03/08/2026 11:00:00", retro_day="2026-08-03"),
            _row("203", "WF Retro Only", "04/08/2026 12:00:00", retro_day="2026-08-04"),
            _row("204", "WF Retro Only", "05/08/2026 13:00:00", retro_day="2026-08-05"),
            _row("205", "WF Retro Only", "06/08/2026 14:00:00", retro_day="2026-08-06"),
        ]
    )
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["Outro WF"],
            COLUNA_CONFIG_WORKFLOW_D1: ["Outro WF D1"],
        }
    )
    retro_cfg = {
        "workflow_d1_keys": {"wf retro only"},
        "workflows_nomes": {"1": "WF Retro Only"},
    }
    mapa = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF Retro Config"],
            COLUNA_CONFIG_WORKFLOW_D1: ["WF Retro Only"],
            COLUNA_CONFIG_CLIENTE: ["Cliente Retro"],
            COLUNA_CONFIG_FILA: ["G auditoria"],
            "_wf_key": ["wf-retro-config"],
        }
    )
    categorias = pd.DataFrame(
        {
            "_cli_key": ["cliente retro"],
            COLUNA_CONFIG_SEGMENTO: ["Seg"],
            COLUNA_CONFIG_CATEGORIA: ["Cat A"],
        }
    )
    plano = SimpleNamespace(warnings=[])

    with patch(
        "app.bots.replicacao_d1_db_bridge.is_fonte_banco_ativa",
        return_value=True,
    ), patch(
        "app.bots.replicacao_d1_db_bridge.load_planning_dataframes",
        return_value={
            "mapa_workflow_d1": mapa,
            "categoria_clientes": categorias,
            "calculadora_params": {
                "confianca": 0.95,
                "margem_erro": 0.05,
                "proporcao": 0.5,
                "meta_produ": 100.0,
            },
        },
    ):
        result = _complementar_itens_config_retroativo(
            [],
            config,
            df,
            settings={"run_id": "test"},
            retro_cfg=retro_cfg,
            plano=plano,
        )

    assert len(result) == 1
    assert result[0]["workflow_d1"] == "WF Retro Only"
    assert result[0]["tem_d1"] is True
    assert result[0]["amostra"] > 0
    assert result[0].get("somente_retroativo") is True


def test_build_retro_workflow_alias_map_inclui_variantes_nome():
    from app.bots.replicacao_aud_d1_planning import (
        _aplicar_aliases_workflow_retroativo,
        _build_retro_workflow_alias_map,
    )

    settings = {
        "_execution_snapshot": {
            "persistent": {
                "workflows": [
                    {
                        "id": 10,
                        "nome_canonico": "AMX - PREVENDA",
                        "nome_d1": "AMX - PREVENDA",
                        "nome_selenium": "AMX - PRE VENDA",
                        "chave_d1_normalizada": "amx - prevenda",
                        "chave_normalizada": "amx - prevenda",
                    }
                ],
                "retroativo": {
                    "workflows": [{"id": 10, "nome_d1": "AMX - PREVENDA", "nome_canonico": "AMX - PREVENDA"}],
                },
            }
        }
    }
    retro = {"workflow_d1_keys": {"amx - prevenda"}}
    alias_map = _build_retro_workflow_alias_map(settings, retro)
    assert "amx - pre venda" in alias_map
    assert alias_map["amx - pre venda"] == "AMX - PREVENDA"

    frame = pd.DataFrame(
        [
            {
                COLUNA_PROTOCOLO: "1",
                COLUNA_WORKFLOW_PARQUET: "AMX - PRE VENDA",
                COLUNA_DATA_ANALISE: "02/08/2026 10:00:00",
                "_selection_reason": "retroativo:2026-08-02",
            },
            {
                COLUNA_PROTOCOLO: "2",
                COLUNA_WORKFLOW_PARQUET: "AMX - PRE VENDA",
                COLUNA_DATA_ANALISE: "02/08/2026 11:00:00",
                "_selection_reason": "retroativo:2026-08-02",
            },
        ]
    )
    normalized = _aplicar_aliases_workflow_retroativo(frame, alias_map)
    assert normalized[COLUNA_WORKFLOW_PARQUET].tolist() == ["AMX - PREVENDA", "AMX - PREVENDA"]


def test_resolver_amostra_retroativa_usa_volume_disponivel_quando_amostra_100():
    from app.bots.replicacao_aud_d1_planning import _resolver_amostra_retroativa_workflow

    amostra = _resolver_amostra_retroativa_workflow(
        wf_dto={"amostra_100": True},
        wf_calc_row={"_wf_key": "wf", COLUNA_CONFIG_CATEGORIA: "", "Total": 50},
        calc_params={"confianca": 0.99},
        total_disponivel=120,
    )
    assert amostra == 120


def test_parse_data_analise_aceita_iso_com_timezone():
    from app.bots.replicacao_aud_planning import _parse_data_analise, contar_por_hora, COLUNA_DATA_ANALISE, COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET

    serie = pd.Series(
        [
            "2026-08-13 03:00:01+00:00",
            "02/08/2026 10:00:00",
        ]
    )
    parsed = _parse_data_analise(serie)
    assert parsed.notna().all()

    frame = pd.DataFrame(
        [
            {
                COLUNA_PROTOCOLO: "1",
                COLUNA_WORKFLOW_PARQUET: "AMX - PreVenda",
                COLUNA_DATA_ANALISE: "2026-08-13 03:00:01+00:00",
                "_selection_reason": "retroativo:2026-08-02",
            },
            {
                COLUNA_PROTOCOLO: "2",
                COLUNA_WORKFLOW_PARQUET: "AMX - PreVenda",
                COLUNA_DATA_ANALISE: "2026-08-13 04:00:01+00:00",
                "_selection_reason": "retroativo:2026-08-02",
            },
        ]
    )
    contagens = contar_por_hora(frame, "AMX - PreVenda")
    assert sum(contagens.values()) == 2


def test_parse_data_analise_iso_utc_vai_para_horario_brasil():
    from app.bots.replicacao_aud_planning import _parse_data_analise

    parsed = _parse_data_analise(pd.Series(["2026-08-13 03:00:01+00:00"]))
    assert parsed.notna().all()
    assert parsed.iloc[0].hour == 0


def test_enriquecer_horarios_retroativo_parquet():
    from app.bots.replicacao_d1_db_bridge import _enriquecer_horarios_retroativo_parquet

    db_df = pd.DataFrame(
        [
            {
                COLUNA_PROTOCOLO: "123",
                COLUNA_WORKFLOW_PARQUET: "WF",
                COLUNA_DATA_ANALISE: "2026-08-02 03:00:00+00:00",
                "_selection_reason": "retroativo:2026-08-02",
            }
        ]
    )
    parquet_df = pd.DataFrame(
        [
            {
                COLUNA_PROTOCOLO: "123",
                COLUNA_WORKFLOW_PARQUET: "WF",
                COLUNA_DATA_ANALISE: "02/08/2026 14:30:00",
            }
        ]
    )
    enriched = _enriquecer_horarios_retroativo_parquet(db_df, parquet_df)
    from app.bots.replicacao_aud_planning import _parse_data_analise

    hora = _parse_data_analise(enriched[COLUNA_DATA_ANALISE]).iloc[0].hour
    assert hora == 14
