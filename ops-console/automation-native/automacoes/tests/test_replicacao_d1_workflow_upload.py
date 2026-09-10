# -*- coding: utf-8 -*-
from pathlib import Path

import pytest
from selenium.common.exceptions import TimeoutException

from app.bots.replicacao_d1.selenium.workflow_upload import (
    SaveNotConfirmedError,
    aguardar_fechamento_painel_edicao,
    configurar_workflow_replicacao,
    csv_protocolos_vazio,
    enviar_csv_protocolos,
)
from app.config import REPLICACAO_CSV_PLACEHOLDER_LIMPEZA


def test_csv_protocolos_vazio_placeholder(tmp_path):
    path = tmp_path / "limpeza.csv"
    path.write_text(REPLICACAO_CSV_PLACEHOLDER_LIMPEZA, encoding="utf-8")
    assert csv_protocolos_vazio(path) is True


def test_csv_protocolos_vazio_com_protocolos(tmp_path):
    path = tmp_path / "protocolos.csv"
    path.write_text("12345678901\n98765432109\n", encoding="utf-8")
    assert csv_protocolos_vazio(path) is False


def test_enviar_csv_protocolos_arquivo_inexistente():
    with pytest.raises(FileNotFoundError):
        enviar_csv_protocolos(None, Path("/nao/existe/protocolos.csv"))


def test_fechamento_forcado_apos_salvar_nao_confirma_persistencia(monkeypatch):
    class Wait:
        def until(self, predicate):
            raise TimeoutException()

    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.WebDriverWait",
        lambda *args, **kwargs: Wait(),
    )
    fallback = []
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.tentar_fechar_painel_edicao",
        lambda driver: fallback.append(True),
    )

    with pytest.raises(SaveNotConfirmedError):
        aguardar_fechamento_painel_edicao(object())

    assert fallback == []


def test_configurar_workflow_replicacao_chama_sequencia(monkeypatch, tmp_path):
    csv_path = tmp_path / "wf.csv"
    csv_path.write_text("123\n", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.aguardar_painel_edicao",
        lambda *a, **k: calls.append("painel"),
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.validar_e_corrigir_config_fila_painel_edicao",
        lambda *a, **k: calls.append("validar_fila"),
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.marcar_replicar_protocolos_especificos",
        lambda *a, **k: calls.append("checkbox"),
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.enviar_csv_protocolos",
        lambda *a, **k: calls.append("csv"),
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.clicar_salvar_replicacao",
        lambda *a, **k: calls.append("salvar"),
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.confirmar_modal_replicacao_se_existir",
        lambda *a, **k: calls.append("modal"),
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.aguardar_fechamento_painel_edicao",
        lambda *a, **k: calls.append("fechar"),
    )
    monkeypatch.setattr(
        "app.bots.replicacao_d1.selenium.workflow_upload.verificar_salvamento_replicacao",
        lambda *a, **k: calls.append("confirmar_save"),
    )

    refilter_called = {"ok": False}

    def refilter(driver, settings, motivo=""):
        refilter_called["ok"] = True

    status_msgs: list[str] = []

    result = configurar_workflow_replicacao(
        object(),
        "WF Teste",
        csv_path,
        set_status=status_msgs.append,
        refilter_after_save=refilter,
    )

    assert calls == ["painel", "validar_fila", "checkbox", "csv", "salvar", "modal", "fechar", "confirmar_save"]
    assert result.status == "SALVO_OK"
    assert result.resultado == "salvo"
    assert refilter_called["ok"] is True
    assert status_msgs[-1] == "Salvo: WF Teste"
