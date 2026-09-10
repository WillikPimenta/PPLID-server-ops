# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

from app.bots.replicacao_d1.selenium.painel_edicao import (
    _set_icm_auditoria_checkbox,
    aplicar_workflow_destino_painel_edicao,
    validar_e_corrigir_config_fila_painel_edicao,
    workflow_destino_painel_confirmado,
)
from app.config import (
    REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
    REPLICACAO_FILA_G_AUDITORIA,
    REPLICACAO_WORKFLOW_COD_DOCUMENTOSCOPIA_31,
    REPLICACAO_WORKFLOW_COD_G_AUDITORIA,
    REPLICACAO_WORKFLOW_DESTINO_DOCUMENTOSCOPIA_31,
    REPLICACAO_WORKFLOW_DESTINO_G_AUDITORIA,
)


def test_workflow_destino_painel_confirmado_bate_cod_e_texto():
    driver = MagicMock()
    driver.execute_script.return_value = {
        "value": "17047",
        "ui": "G Auditoria - G Auditoria",
    }
    assert workflow_destino_painel_confirmado(
        driver, "17047", "G Auditoria - G Auditoria"
    )


def test_workflow_destino_painel_confirmado_falha_texto_diferente():
    driver = MagicMock()
    driver.execute_script.return_value = {
        "value": "17047",
        "ui": "Documentoscopia 3.1 - Documentoscopia 3.1",
    }
    assert not workflow_destino_painel_confirmado(
        driver, "17047", "G Auditoria - G Auditoria"
    )


def test_aplicar_workflow_destino_painel_ja_correto(monkeypatch):
    driver = MagicMock()
    calls: list[str] = []

    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.aguardar_workflow_destino_painel_estavel",
        lambda *a, **k: calls.append("estavel") or True,
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.definir_workflow_destino_painel_js",
        lambda *a, **k: calls.append("js"),
    )

    aplicar_workflow_destino_painel_edicao(driver, "17047", "G Auditoria - G Auditoria")

    assert calls == ["estavel"]


def test_validar_e_corrigir_g_auditoria(monkeypatch):
    driver = MagicMock()
    aplicar = MagicMock()
    desmarcar = MagicMock()
    marcar = MagicMock()

    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.ler_workflow_destino_painel_edicao",
        lambda d: {"value": "17426", "ui": "Documentoscopia 3.1"},
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.aplicar_workflow_destino_painel_edicao",
        aplicar,
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.desmarcar_replicar_icm_auditoria",
        desmarcar,
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.marcar_replicar_icm_auditoria_painel",
        marcar,
    )

    validar_e_corrigir_config_fila_painel_edicao(
        driver, REPLICACAO_FILA_G_AUDITORIA, {}
    )

    aplicar.assert_called_once_with(
        driver,
        REPLICACAO_WORKFLOW_COD_G_AUDITORIA,
        REPLICACAO_WORKFLOW_DESTINO_G_AUDITORIA,
    )
    desmarcar.assert_called_once_with(driver, set_status=None)
    marcar.assert_not_called()


def test_validar_e_corrigir_documentoscopia_31(monkeypatch):
    driver = MagicMock()
    aplicar = MagicMock()
    desmarcar = MagicMock()
    marcar = MagicMock()

    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.ler_workflow_destino_painel_edicao",
        lambda d: {"value": "17047", "ui": "G Auditoria"},
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.aplicar_workflow_destino_painel_edicao",
        aplicar,
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.desmarcar_replicar_icm_auditoria",
        desmarcar,
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.marcar_replicar_icm_auditoria_painel",
        marcar,
    )

    validar_e_corrigir_config_fila_painel_edicao(
        driver, REPLICACAO_FILA_DOCUMENTOSCOPIA_31, {}
    )

    aplicar.assert_called_once_with(
        driver,
        REPLICACAO_WORKFLOW_COD_DOCUMENTOSCOPIA_31,
        REPLICACAO_WORKFLOW_DESTINO_DOCUMENTOSCOPIA_31,
    )
    marcar.assert_called_once_with(driver, set_status=None)
    desmarcar.assert_not_called()


def _mock_checkbox_wait(monkeypatch, checkbox):
    class FakeWait:
        def __init__(self, *args, **kwargs):
            pass

        def until(self, cond):
            return checkbox

    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.WebDriverWait",
        FakeWait,
    )
    monkeypatch.setattr("app.bots.replicacao_d1.selenium.painel_edicao.time.sleep", lambda *_: None)


def test_set_icm_auditoria_idempotente_quando_ja_desmarcado(monkeypatch):
    driver = MagicMock()
    checkbox = MagicMock()
    checkbox.is_selected.return_value = False
    _mock_checkbox_wait(monkeypatch, checkbox)

    _set_icm_auditoria_checkbox(driver, False)

    click_calls = [
        c
        for c in driver.execute_script.call_args_list
        if "click()" in str(c.args[0])
    ]
    assert not click_calls
    checkbox.click.assert_not_called()


def test_set_icm_auditoria_idempotente_quando_ja_marcado(monkeypatch):
    driver = MagicMock()
    checkbox = MagicMock()
    checkbox.is_selected.return_value = True
    _mock_checkbox_wait(monkeypatch, checkbox)

    _set_icm_auditoria_checkbox(driver, True)

    click_calls = [
        c
        for c in driver.execute_script.call_args_list
        if "click()" in str(c.args[0])
    ]
    assert not click_calls
    checkbox.click.assert_not_called()


def test_validar_e_corrigir_sem_fila_nao_faz_nada(monkeypatch):
    driver = MagicMock()
    aplicar = MagicMock()
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.painel_edicao.aplicar_workflow_destino_painel_edicao",
        aplicar,
    )

    validar_e_corrigir_config_fila_painel_edicao(driver, None, {})

    aplicar.assert_not_called()
