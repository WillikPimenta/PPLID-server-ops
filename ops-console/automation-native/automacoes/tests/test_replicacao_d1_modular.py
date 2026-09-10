# -*- coding: utf-8 -*-
from app.bots.replicacao_d1.settings import (
    get_headless,
    normalize_replicacao_settings,
    parse_bool_setting,
    upload_csv_vazio_habilitado,
)
from app.bots.replicacao_d1.selenium.select2_utils import (
    normalizar_texto_select2_ui,
    termo_digitacao_select2,
    termos_busca_select2,
    texto_select2_bate,
    ui_select2_vazia,
)


def test_termos_busca_inclui_curto_e_cod():
    termos = termos_busca_select2("17047", "G Auditoria - G Auditoria")
    assert termos[0] == "G Auditoria - G Auditoria"
    assert "G Auditoria" in termos
    assert "17047" in termos


def test_termo_digitacao_prioriza_texto_curto():
    assert termo_digitacao_select2("17047", "G Auditoria - G Auditoria") == "G Auditoria"
    assert termo_digitacao_select2("751", "GAQ") == "GAQ"
    assert termo_digitacao_select2("17047", "") == "17047"


def test_normalizar_ui_remove_botao_limpar():
    assert normalizar_texto_select2_ui("\u00d7G Auditoria - G Auditoria") == (
        "G Auditoria - G Auditoria"
    )
    assert normalizar_texto_select2_ui("  Selecione  ") == "Selecione"


def test_ui_vazia_reconhece_placeholder():
    assert ui_select2_vazia("Selecione") is True
    assert ui_select2_vazia("Adicione o WorkFlow Destino") is True
    assert ui_select2_vazia("") is True
    assert ui_select2_vazia("G Auditoria - G Auditoria") is False


def test_texto_select2_bate_g_auditoria():
    assert texto_select2_bate("G Auditoria - G Auditoria", "G Auditoria - G Auditoria")
    assert texto_select2_bate("\u00d7G Auditoria - G Auditoria", "G Auditoria - G Auditoria")
    assert texto_select2_bate("G Auditoria - G Auditoria", "G Auditoria")
    assert not texto_select2_bate("Selecione", "G Auditoria - G Auditoria")
    assert not texto_select2_bate("", "G Auditoria - G Auditoria")


def test_parse_bool_setting():
    assert parse_bool_setting("yes") is True
    assert parse_bool_setting("0") is False
    assert parse_bool_setting(None, default=True) is True


def test_normalize_settings_injects_run_env(monkeypatch):
    monkeypatch.setenv("REPLICACAO_D1_RUN_ID", "20260810_120000")
    monkeypatch.setattr(
        "app.bots.replicacao_d1.settings.try_inject_execution_snapshot",
        lambda cfg: cfg,
    )
    cfg = normalize_replicacao_settings({})
    assert cfg.get("run_id") == "20260810_120000"
