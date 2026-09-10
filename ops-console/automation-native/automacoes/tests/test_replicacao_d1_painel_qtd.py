# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

import pytest

from app.bots.replicacao_d1.selenium.painel_qtd import (
    STATUS_SEM_ALTERACAO,
    _parse_qtd_brflow,
    configurar_workflow_qtd_replicada,
    desmarcar_replicar_protocolos_especificos,
    confirmar_qtd_replicada_pos_save,
)


def test_parse_qtd_brflow():
    assert _parse_qtd_brflow("42") == 42
    assert _parse_qtd_brflow("1.234") == 1234
    assert _parse_qtd_brflow("") is None


@patch("app.bots.replicacao_d1.selenium.painel_qtd.aguardar_fechamento_painel_edicao")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.fechar_painel_edicao_sem_salvar")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.ler_qtd_replicada_painel")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.desmarcar_replicar_protocolos_especificos")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.validar_e_corrigir_config_fila_painel_edicao")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.aguardar_painel_edicao")
def test_configurar_qtd_sem_alteracao_quando_igual(
    mock_aguardar,
    mock_validar,
    mock_desmarcar,
    mock_ler,
    mock_fechar,
    mock_aguardar_fechamento,
):
    mock_desmarcar.return_value = False
    mock_ler.return_value = 10
    driver = MagicMock()
    status = configurar_workflow_qtd_replicada(
        driver,
        "WF Bio",
        10,
        fila="Bio",
        settings={},
    )
    assert status.status == STATUS_SEM_ALTERACAO
    assert status.resultado == "sem_alteracao"
    assert status.motivo_codigo == "QTD_JA_CONFIGURADA"
    assert status.quantidade_alvo == status.quantidade_encontrada == 10
    mock_fechar.assert_called_once_with(driver, set_status=None)


@patch("app.bots.replicacao_d1.selenium.painel_qtd.aguardar_fechamento_painel_edicao")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.verificar_salvamento_replicacao")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.confirmar_modal_replicacao_se_existir")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.clicar_salvar_replicacao")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.preencher_qtd_replicada_painel")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.ler_qtd_replicada_painel")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.desmarcar_replicar_protocolos_especificos")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.validar_e_corrigir_config_fila_painel_edicao")
@patch("app.bots.replicacao_d1.selenium.painel_qtd.aguardar_painel_edicao")
def test_configurar_qtd_salva_quando_diferente(
    mock_aguardar,
    mock_validar,
    mock_desmarcar,
    mock_ler,
    mock_preencher,
    mock_salvar,
    mock_confirmar,
    mock_verificar,
    mock_aguardar_fechamento,
):
    mock_desmarcar.return_value = False
    mock_ler.return_value = 5
    driver = MagicMock()
    status = configurar_workflow_qtd_replicada(
        driver,
        "WF Bio",
        10,
        fila="Bio",
        settings={},
    )
    assert status.status == "SALVO_OK"
    assert status.quantidade_alvo == 10
    assert status.quantidade_encontrada is None
    mock_preencher.assert_called_once()
    mock_salvar.assert_called_once()


@patch("app.bots.replicacao_d1.selenium.painel_qtd.WebDriverWait")
def test_desmarcar_protocolos_especificos_quando_modo_qtd(mock_wait):
    checkbox = MagicMock()
    checkbox.is_selected.return_value = True
    mock_wait.return_value.until.side_effect = [checkbox, True]
    driver = MagicMock()

    alterado = desmarcar_replicar_protocolos_especificos(driver)

    assert alterado is True
    driver.execute_script.assert_any_call("arguments[0].click();", checkbox)


def test_configurar_qtd_salva_checkbox_desmarcado_mesmo_quando_qtd_igual():
    driver = MagicMock()
    with patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.aguardar_painel_edicao"
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.validar_e_corrigir_config_fila_painel_edicao"
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.desmarcar_replicar_protocolos_especificos",
        return_value=True,
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.ler_qtd_replicada_painel",
        return_value=10,
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.preencher_qtd_replicada_painel"
    ) as preencher, patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.clicar_salvar_replicacao"
    ) as salvar, patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.confirmar_modal_replicacao_se_existir"
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.aguardar_fechamento_painel_edicao"
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.verificar_salvamento_replicacao"
    ):
        status = configurar_workflow_qtd_replicada(driver, "WF G", 10, fila="G auditoria")

    assert status.status == "SALVO_OK"
    assert status.quantidade_encontrada is None
    preencher.assert_called_once_with(driver, 10, set_status=None)
    salvar.assert_called_once()


def test_confirmar_qtd_pos_save_reabre_e_rele_valor():
    driver = MagicMock()
    abrir = MagicMock()
    with patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.aguardar_painel_edicao"
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.ler_qtd_replicada_painel",
        return_value=25,
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.fechar_painel_edicao_sem_salvar"
    ):
        encontrada = confirmar_qtd_replicada_pos_save(
            driver, 25, abrir_edicao=abrir
        )
    assert encontrada == 25
    abrir.assert_called_once()


def test_confirmar_qtd_pos_save_rejeita_divergencia():
    from app.bots.replicacao_d1.selenium.workflow_upload import SaveNotConfirmedError

    with patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.aguardar_painel_edicao"
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.ler_qtd_replicada_painel",
        return_value=24,
    ), patch(
        "app.bots.replicacao_d1.selenium.painel_qtd.fechar_painel_edicao_sem_salvar"
    ):
        with pytest.raises(SaveNotConfirmedError):
            confirmar_qtd_replicada_pos_save(
                MagicMock(), 25, abrir_edicao=MagicMock()
            )
