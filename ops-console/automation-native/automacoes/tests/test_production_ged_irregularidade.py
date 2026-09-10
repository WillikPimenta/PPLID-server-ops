"""Testes da extração GED irregularidade no bot Produtividade H/H."""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.bots.production import ged_irregularidade as ged_mod
from app.bots.production.ged_irregularidade import (
    PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX,
    _montar_nome_bruto_periodo,
    _periodo_hxh_irregularidade,
    _salvar_csv_bruto,
    executar_extracao_ged_irregularidade,
)


TZ_SP = ZoneInfo("America/Sao_Paulo")


def test_periodo_hxh_usa_d10_ate_d0_inclusive():
    periodo = _periodo_hxh_irregularidade(data_base=date(2026, 8, 28))

    assert periodo.data_inicio == date(2026, 8, 18)
    assert periodo.data_fim == date(2026, 8, 28)
    assert (periodo.data_fim - periodo.data_inicio).days == 10


def test_periodo_hxh_atravessa_mes_sem_usar_quinzenas_da_rotina():
    periodo = _periodo_hxh_irregularidade(data_base=date(2026, 3, 5))

    assert periodo.data_inicio == date(2026, 2, 23)
    assert periodo.data_fim == date(2026, 3, 5)


def test_montar_nome_bruto_periodo_inclui_intervalo_execucao_e_hash():
    periodo = _periodo_hxh_irregularidade(data_base=date(2026, 8, 28))
    nome = _montar_nome_bruto_periodo(
        periodo,
        executado_em=datetime(2026, 8, 28, 9, 15, 30, 123456, tzinfo=TZ_SP),
        content_sha256="a" * 64,
        token="1234abcd",
    )

    assert nome == (
        "ged-irregularidade-bruto_20260818_20260828_"
        "20260828T091530123456_aaaaaaaaaaaa_1234abcd.csv"
    )


def test_salvar_csv_bruto_publica_artefato_imutavel_com_hash(monkeypatch, tmp_path):
    pasta = tmp_path / "hxh-bruto"
    monkeypatch.setattr(
        "app.bots.production.ged_irregularidade.PASTA_GED_IRREGULARIDADE_HXH_BRUTO",
        pasta,
    )
    origem = tmp_path / "download.csv"
    origem.write_text("Protocolo;Descricao\n1;ok", encoding="utf-8")
    periodo = _periodo_hxh_irregularidade(data_base=date(2026, 8, 28))
    emit = MagicMock()
    monkeypatch.setattr(
        "app.bots.production.ged_irregularidade._emit_production_ged_irregularidade_saved",
        emit,
    )

    executado_em = datetime(2026, 8, 28, 9, 15, tzinfo=TZ_SP)
    primeiro = _salvar_csv_bruto(origem, periodo, executado_em=executado_em)
    segundo = _salvar_csv_bruto(origem, periodo, executado_em=executado_em)

    digest = hashlib.sha256(origem.read_bytes()).hexdigest()
    assert primeiro.exists()
    assert segundo.exists()
    assert primeiro != segundo
    assert "20260818_20260828" in primeiro.name
    assert digest[:12] in primeiro.name
    assert primeiro.read_bytes() == origem.read_bytes()
    assert segundo.read_bytes() == origem.read_bytes()
    assert not list(pasta.glob("*.tmp"))
    metadata = emit.call_args_list[0].kwargs["metadata"]
    assert metadata["period_start"] == "2026-08-18"
    assert metadata["period_end"] == "2026-08-28"
    assert metadata["content_sha256"] == digest
    assert metadata["content_size"] == origem.stat().st_size


def test_emit_marker_inclui_metadados_no_sync_drop(monkeypatch, tmp_path, capsys):
    path = tmp_path / "artefato.csv"
    path.write_text("Protocolo\n1", encoding="utf-8")
    write_drop = MagicMock()
    append_log = MagicMock()
    monkeypatch.setattr(ged_mod, "write_sync_drop", write_drop)
    monkeypatch.setattr(ged_mod, "append_marker_to_robot_log", append_log)

    metadata = {"content_sha256": "abc", "period_start": "2026-08-18"}
    ged_mod._emit_production_ged_irregularidade_saved(path, metadata=metadata)

    captured = capsys.readouterr()
    marker = f"{PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX}{path.resolve()}"
    assert marker in captured.out
    write_drop.assert_called_once_with(
        ged_mod.DOMAIN_REINSPECAO_GED,
        path.resolve(),
        extra=metadata,
    )
    append_log.assert_called_once_with("production", marker)


@patch("app.bots.production.ged_irregularidade.safe_close_driver")
@patch("app.bots.production.ged_irregularidade.login_ged")
@patch("app.bots.production.ged_irregularidade.criar_driver_ged")
@patch("app.bots.production.ged_irregularidade.download_quinzena_irregularidade")
def test_executar_extracao_baixa_um_periodo_d10_d0(
    mock_download,
    mock_driver,
    mock_login,
    mock_close,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "app.bots.production.ged_irregularidade._cfg_executar_ged_irregularidade",
        lambda settings=None: True,
    )
    monkeypatch.setattr(
        "app.bots.production.ged_irregularidade._salvar_csv_bruto",
        lambda arquivo, periodo: tmp_path / "artefato.csv",
    )

    raw = tmp_path / "raw.csv"
    raw.write_text("Protocolo\n1", encoding="utf-8")
    mock_download.return_value = raw
    mock_driver.return_value = MagicMock()

    registrar_status = MagicMock()
    ok = executar_extracao_ged_irregularidade(
        set_status=lambda msg: None,
        registrar_status=registrar_status,
        data_base=date(2026, 8, 28),
    )

    assert ok is True
    assert mock_download.call_count == 1
    periodo = mock_download.call_args.args[2]
    assert periodo.data_inicio == date(2026, 8, 18)
    assert periodo.data_fim == date(2026, 8, 28)
    assert mock_download.call_args.args[3:5] == (1, 1)
    registrar_status.assert_called_with("ged_irregularidade", True, "")
    assert not hasattr(ged_mod, "tratar_irregularidade")


def test_orquestrador_executa_ged_mesmo_quando_extracao_principal_falha():
    from app.bots import bot_production

    erro = bot_production.FalhaAmbosSistemasDownload("confer", "brflow")
    with (
        patch.object(bot_production, "_executar_extracao", side_effect=erro) as principal,
        patch.object(bot_production, "_maybe_run_ged_irregularidade_reinspecao") as ged,
        patch.object(bot_production, "_set_progress") as progress,
        patch.object(bot_production, "_enviar_notificacao_ciclo") as notify,
    ):
        with pytest.raises(bot_production.FalhaAmbosSistemasDownload):
            bot_production._executar_extracao_principal_e_ged(
                1,
                None,
                MagicMock(),
                TZ_SP,
                {"executar_ged_irregularidade": True},
            )

    principal.assert_called_once()
    ged.assert_called_once_with({"executar_ged_irregularidade": True})
    progress.assert_called_once_with(100, "Producao: ciclo concluido")
    notify.assert_called_once_with()


def test_orquestrador_notifica_uma_vez_somente_depois_do_ged():
    from app.bots import bot_production

    events: list[str] = []
    with (
        patch.object(bot_production, "_executar_extracao", side_effect=lambda *args: events.append("principal")),
        patch.object(
            bot_production,
            "_maybe_run_ged_irregularidade_reinspecao",
            side_effect=lambda settings: events.append("ged"),
        ),
        patch.object(
            bot_production,
            "_set_progress",
            side_effect=lambda *args: events.append("progress-100"),
        ),
        patch.object(
            bot_production,
            "_enviar_notificacao_ciclo",
            side_effect=lambda: events.append("notify"),
        ) as notify,
    ):
        bot_production._executar_extracao_principal_e_ged(
            1,
            None,
            MagicMock(),
            TZ_SP,
            {"executar_ged_irregularidade": True},
        )

    assert events == ["principal", "ged", "progress-100", "notify"]
    notify.assert_called_once_with()


def test_executar_ged_desabilitado_nao_abre_driver(monkeypatch):
    monkeypatch.setattr(ged_mod, "_cfg_executar_ged_irregularidade", lambda settings=None: False)
    status = MagicMock()

    with patch.object(ged_mod, "criar_driver_ged") as criar_driver:
        ok = executar_extracao_ged_irregularidade(set_status=status)

    assert ok is True
    criar_driver.assert_not_called()
    status.assert_called_once()


@patch("app.bots.production.ged_irregularidade.safe_close_driver")
@patch("app.bots.production.ged_irregularidade.login_ged")
@patch("app.bots.production.ged_irregularidade.criar_driver_ged")
@patch("app.bots.production.ged_irregularidade.download_quinzena_irregularidade")
def test_executar_ged_relatorio_vazio_registra_falha(
    mock_download,
    mock_driver,
    mock_login,
    mock_close,
    monkeypatch,
):
    monkeypatch.setattr(ged_mod, "_cfg_executar_ged_irregularidade", lambda settings=None: True)
    mock_driver.return_value = MagicMock()
    mock_download.return_value = None
    registrar_status = MagicMock()
    registrar_erro = MagicMock()

    ok = executar_extracao_ged_irregularidade(
        set_status=MagicMock(),
        registrar_status=registrar_status,
        registrar_erro=registrar_erro,
    )

    assert ok is False
    registrar_status.assert_called_once_with("ged_irregularidade", False, "relatório vazio")
    registrar_erro.assert_called_once()


@patch("app.bots.production.ged_irregularidade.safe_close_driver")
@patch("app.bots.production.ged_irregularidade.criar_driver_ged", side_effect=RuntimeError("GED offline"))
def test_executar_ged_excecao_registra_falha(mock_driver, mock_close, monkeypatch):
    monkeypatch.setattr(ged_mod, "_cfg_executar_ged_irregularidade", lambda settings=None: True)
    registrar_status = MagicMock()
    registrar_erro = MagicMock()

    ok = executar_extracao_ged_irregularidade(
        set_status=MagicMock(),
        registrar_status=registrar_status,
        registrar_erro=registrar_erro,
    )

    assert ok is False
    registrar_status.assert_called_once_with("ged_irregularidade", False, "GED offline")
    registrar_erro.assert_called_once()
