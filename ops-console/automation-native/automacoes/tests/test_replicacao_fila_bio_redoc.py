# -*- coding: utf-8 -*-
from app.bots.replicacao_aud_planning import (
    REPLICACAO_FILA_BIO,
    REPLICACAO_FILA_REDOC,
    REPLICACAO_MODO_QTD,
    REPLICACAO_MODO_PROTOCOLOS,
    _coluna_escala_por_fila,
    _normalizar_fila,
    agrupar_workflows_por_fila,
    eh_fila_bio,
    eh_fila_modo_qtd,
    eh_fila_redoc,
    resolver_canal_destino,
    resolver_filtros_brflow_por_fila,
    resolver_modo_replicacao,
)


def test_normalizar_fila_bio_redoc():
    assert _normalizar_fila("Bio") == REPLICACAO_FILA_BIO
    assert _normalizar_fila("biometria") == REPLICACAO_FILA_BIO
    assert _normalizar_fila("Redoc") == REPLICACAO_FILA_REDOC
    assert _normalizar_fila("auditoria redoc") == REPLICACAO_FILA_REDOC


def test_modo_replicacao_qtd_apenas_bio_redoc():
    assert resolver_modo_replicacao(REPLICACAO_FILA_BIO) == REPLICACAO_MODO_QTD
    assert resolver_modo_replicacao(REPLICACAO_FILA_REDOC) == REPLICACAO_MODO_QTD
    assert resolver_modo_replicacao("G auditoria") == "protocolos"
    assert eh_fila_modo_qtd(REPLICACAO_FILA_BIO)
    assert eh_fila_redoc("redoc")
    assert eh_fila_bio("Bio")


def test_modo_explicito_vence_fila():
    assert resolver_modo_replicacao(True, REPLICACAO_FILA_BIO) == REPLICACAO_MODO_PROTOCOLOS
    assert resolver_modo_replicacao(True, REPLICACAO_FILA_REDOC) == REPLICACAO_MODO_PROTOCOLOS
    assert resolver_modo_replicacao(False, "G auditoria") == REPLICACAO_MODO_QTD
    assert resolver_modo_replicacao(False, "3.1") == REPLICACAO_MODO_QTD


def test_modo_legado_sem_toggle_mantem_fallback_por_fila():
    assert resolver_modo_replicacao(None, REPLICACAO_FILA_BIO) == REPLICACAO_MODO_QTD
    assert resolver_modo_replicacao(None, REPLICACAO_FILA_REDOC) == REPLICACAO_MODO_QTD
    assert resolver_modo_replicacao(None, "G auditoria") == REPLICACAO_MODO_PROTOCOLOS


def test_matriz_modo_independente_da_fila():
    matriz = [
        (False, "G auditoria", REPLICACAO_MODO_QTD),
        (False, "3.1", REPLICACAO_MODO_QTD),
        (True, REPLICACAO_FILA_BIO, REPLICACAO_MODO_PROTOCOLOS),
        (True, REPLICACAO_FILA_REDOC, REPLICACAO_MODO_PROTOCOLOS),
        (False, REPLICACAO_FILA_REDOC, REPLICACAO_MODO_QTD),
    ]
    assert [resolver_modo_replicacao(toggle, fila) for toggle, fila, _ in matriz] == [
        esperado for _, _, esperado in matriz
    ]


def test_resolver_filtros_brflow_bio_redoc():
    settings = {
        "replicacao_workflow_cod_bio": "99001",
        "replicacao_workflow_destino_bio": "Auditoria Biometria - Auditoria Biometria",
        "replicacao_workflow_cod_redoc": "99002",
        "replicacao_workflow_destino_redoc": "Auditoria Redoc - Auditoria Redoc",
    }
    bio = resolver_filtros_brflow_por_fila(REPLICACAO_FILA_BIO, settings)
    redoc = resolver_filtros_brflow_por_fila(REPLICACAO_FILA_REDOC, settings)
    assert bio["replicacao_workflow_cod"] == "99001"
    assert redoc["replicacao_workflow_cod"] == "99002"


def test_resolver_filtros_brflow_bio_redoc_usa_snapshot_persistent():
    settings = {
        "_execution_snapshot": {
            "persistent": {
                "replicacao_workflow_cod_bio": "17761",
                "replicacao_workflow_destino_bio": "Auditoria Biometria - Auditoria Biometria",
                "replicacao_workflow_cod_redoc": "18013",
                "replicacao_workflow_destino_redoc": "Auditoria Redoc - Auditoria Redoc",
            }
        }
    }
    bio = resolver_filtros_brflow_por_fila(REPLICACAO_FILA_BIO, settings)
    redoc = resolver_filtros_brflow_por_fila(REPLICACAO_FILA_REDOC, settings)
    assert bio["replicacao_workflow_cod"] == "17761"
    assert redoc["replicacao_workflow_cod"] == "18013"


def test_resolver_canal_destino_bio_redoc():
    assert resolver_canal_destino(REPLICACAO_FILA_BIO) == "BRFlow Bio"
    assert resolver_canal_destino(REPLICACAO_FILA_REDOC) == "BRFlow Redoc"


def test_coluna_escala_por_fila():
    from app.config import (
        COLUNA_ESCALA_AUDITORES,
        COLUNA_ESCALA_AUDITORES_BIO,
        COLUNA_ESCALA_AUDITORES_CASE,
        COLUNA_ESCALA_AUDITORES_REDOC,
    )

    assert _coluna_escala_por_fila(REPLICACAO_FILA_BIO) == COLUNA_ESCALA_AUDITORES_BIO
    assert _coluna_escala_por_fila(REPLICACAO_FILA_REDOC) == COLUNA_ESCALA_AUDITORES_REDOC
    assert _coluna_escala_por_fila("3.1") == COLUNA_ESCALA_AUDITORES_CASE
    assert _coluna_escala_por_fila("G auditoria") == COLUNA_ESCALA_AUDITORES


def test_agrupar_workflows_ordem_bio_redoc():
    from types import SimpleNamespace

    plano = SimpleNamespace(workflow_fila={"W1": "Bio", "W2": "G auditoria", "W3": "Redoc"})
    grupos = agrupar_workflows_por_fila(["W1", "W2", "W3"], plano)
    assert list(grupos.keys()) == ["G auditoria", "Bio", "Redoc"]


def test_agrupar_workflows_ignora_filas_desativadas():
    from types import SimpleNamespace

    plano = SimpleNamespace(workflow_fila={"W1": "Bio", "W2": "G auditoria", "W3": "Redoc"})
    settings = {"replicacao_destino_bio_ativo": False, "replicacao_destino_redoc_ativo": False}
    grupos = agrupar_workflows_por_fila(["W1", "W2", "W3"], plano, settings=settings)
    assert list(grupos.keys()) == ["G auditoria"]
    assert grupos["G auditoria"] == ["W2"]


def test_filtrar_config_destinos_desabilitados():
    import pandas as pd
    from app.bots.replicacao_aud_planning import (
        COLUNA_CONFIG_FILA,
        filtrar_config_destinos_desabilitados,
    )

    df = pd.DataFrame(
        {
            COLUNA_CONFIG_FILA: ["G auditoria", "Bio", "Redoc"],
            "workflow": ["W1", "W2", "W3"],
        }
    )
    out = filtrar_config_destinos_desabilitados(
        df,
        {"replicacao_destino_bio_ativo": False},
    )
    assert list(out[COLUNA_CONFIG_FILA]) == ["G auditoria", "Redoc"]


def test_filtrar_config_destinos_desabilitados_explica_plano_totalmente_vazio():
    import pandas as pd
    import pytest

    from app.bots.replicacao_aud_planning import (
        COLUNA_CONFIG_FILA,
        filtrar_config_destinos_desabilitados,
    )

    config = pd.DataFrame(
        {
            COLUNA_CONFIG_FILA: ["G auditoria", "3.1"],
            "workflow": ["WF G", "WF 31"],
        }
    )

    with pytest.raises(ValueError, match="3.1, G auditoria"):
        filtrar_config_destinos_desabilitados(
            config,
            settings={
                "replicacao_destino_brflow_ativo": False,
                "replicacao_destino_case31_ativo": False,
            },
        )
