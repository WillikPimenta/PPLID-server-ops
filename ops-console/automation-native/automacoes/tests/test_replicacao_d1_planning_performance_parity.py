# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from app.bots import replicacao_aud_planning as rap
from app.bots.replicacao_aud_d1_planning import planejamento_otimizado_ativo


def _pool() -> pd.DataFrame:
    return pd.DataFrame(
        {
            rap.COLUNA_PROTOCOLO: ["001", "002", "003", "010", "011", "099"],
            rap.COLUNA_WORKFLOW_PARQUET: [
                "Fluxo Á",
                "Fluxo Á",
                "Fluxo Á",
                "Fluxo B",
                "Fluxo B",
                "Outro",
            ],
            rap.COLUNA_DATA_ANALISE: [
                "2026-08-30 08:10:00",
                "2026-08-30 08:20:00",
                "2026-08-30 09:00:00",
                "2026-08-30 08:00:00",
                "2026-08-30 10:00:00",
                "inválida",
            ],
        }
    )


def test_pool_preparado_preserva_contagem_e_amostra_ordenada():
    raw = _pool()
    prepared = rap.preparar_pool_planejamento(raw)

    assert rap.preparar_pool_planejamento(prepared) is prepared
    assert rap.contar_por_hora(raw, "fluxo a") == rap.contar_por_hora(prepared, "fluxo a")

    alocacao = {8: 1, 9: 1}
    legacy, legacy_excl = rap.selecionar_protocolos(
        raw, "FLUXO Á", alocacao, seed=42, historico={"0002"}
    )
    optimized, optimized_excl = rap.selecionar_protocolos(
        prepared, "FLUXO Á", alocacao, seed=42, historico={"0002"}
    )

    assert optimized_excl == legacy_excl
    assert optimized[rap.COLUNA_PROTOCOLO].tolist() == legacy[rap.COLUNA_PROTOCOLO].tolist()
    assert optimized["_hora"].tolist() == legacy["_hora"].tolist()


def test_preparo_global_nao_reparseia_pool_por_workflow():
    raw = _pool()
    parse_original = rap._parse_data_analise
    normalize_original = rap._normalizar_workflow

    with patch.object(rap, "_parse_data_analise", wraps=parse_original) as parse_spy, patch.object(
        rap, "_normalizar_workflow", wraps=normalize_original
    ) as normalize_spy:
        prepared = rap.preparar_pool_planejamento(raw)
        rap.preparar_pool_planejamento(prepared)
        rap.contar_por_hora(prepared, "Fluxo Á")
        rap.contar_por_hora(prepared, "Fluxo B")
        rap.selecionar_protocolos(prepared, "Fluxo Á", {8: 1}, seed=7)

    assert parse_spy.call_count == 1
    # N normalizações na preparação + uma por lookup; nunca N por lookup.
    assert normalize_spy.call_count <= len(raw) + 3


def test_historico_preparado_preserva_valores_e_reusa_chaves_normalizadas():
    historico = rap.preparar_historico_planejamento({"0001", "ABC"})
    assert historico == {"0001", "ABC"}
    assert rap._historico_chaves_plano(historico) == {"1", "abc"}

    with patch.object(
        rap, "_protocolo_chave_plano", wraps=rap._protocolo_chave_plano
    ) as key_spy:
        rap._historico_chaves_plano(historico)
        rap._historico_chaves_plano(historico)
    assert key_spy.call_count == 0

    copia = historico.copy()
    copia.add("NOVO")
    copia |= {"MAIS-UM"}
    assert "NOVO" not in historico
    assert "novo" in copia.chaves
    assert "mais-um" in copia.chaves


def test_flag_otimizada_e_opt_in_com_alias_compativel():
    assert planejamento_otimizado_ativo({}) is False
    assert planejamento_otimizado_ativo({"optimized_planning": True}) is True
    assert planejamento_otimizado_ativo(
        {"replicacao_d1_planejamento_otimizado": "true"}
    ) is True
    assert planejamento_otimizado_ativo(
        {"optimized_planning": False, "replicacao_d1_planejamento_otimizado": True}
    ) is False
