# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

from app.bots.replicacao_d1.selenium.filtros_pesquisa import (
    filtro_select2_confirmado,
    ler_valor_select_filtro_pesquisa,
    validar_filtros_replicacao_antes_pesquisar,
    workflow_destino_pesquisa_vazio,
)
from app.bots.replicacao_d1.settings import filtrar_workflow_destino_habilitado


def test_ler_valor_select_filtro_pesquisa_via_js():
    driver = MagicMock()
    driver.execute_script.return_value = "751"
    assert ler_valor_select_filtro_pesquisa(driver, "codClienteDestino") == "751"
    assert driver.execute_script.called


def test_filtro_select2_confirmado_bate_valor_e_ui(monkeypatch):
    driver = MagicMock()
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.ler_valor_select_filtro_pesquisa",
        lambda *a, **k: "17047",
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.ler_texto_select2_filtro_pesquisa",
        lambda *a, **k: "G Auditoria - G Auditoria",
    )
    assert filtro_select2_confirmado(
        driver,
        "codWorkFlowDestino",
        "17047",
        "G Auditoria - G Auditoria",
    )


def test_filtrar_workflow_destino_desligado_por_padrao():
    assert filtrar_workflow_destino_habilitado({}) is False
    assert filtrar_workflow_destino_habilitado({"replicacao_filtrar_workflow_destino": True}) is True
    assert filtrar_workflow_destino_habilitado({"replicacao_filtrar_workflow_destino": False}) is False


def test_validar_filtros_busca_ampla_nao_exige_workflow_destino(monkeypatch):
    driver = MagicMock()
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.aguardar_filtro_select2_estavel",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.ler_valor_select_filtro_pesquisa",
        lambda *a, **k: "",
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.ler_texto_select2_filtro_pesquisa",
        lambda *a, **k: "Selecione",
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.workflow_destino_pesquisa_vazio",
        lambda *a, **k: True,
    )

    validar_filtros_replicacao_antes_pesquisar(
        driver,
        {
            "replicacao_filtrar_workflow_destino": False,
            "replicacao_cliente_cod": "751",
        },
    )


def test_workflow_destino_pesquisa_vazio(monkeypatch):
    driver = MagicMock()
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.ler_texto_select2_filtro_pesquisa",
        lambda *a, **k: "Selecione",
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.filtros_pesquisa.ler_valor_select_filtro_pesquisa",
        lambda *a, **k: "",
    )
    assert workflow_destino_pesquisa_vazio(driver) is True
