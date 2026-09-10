"""Testes do fallback sync_drop (stdout quebrado → marker em disco)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.infrastructure.bot_sync_drop import (
    DOMAIN_PRODUTIVIDADE,
    DOMAIN_REINSPECAO_GED,
    drain_sync_drops,
    write_sync_drop,
)
from app.bots.bot_production import _emit_production_detalhado_saved
from app.services.robot_manager import RobotProcessManager


def test_write_and_drain_sync_drop(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_SYNC_DROP_DIR", str(tmp_path / "drop"))
    dest = tmp_path / "relatorio_produtividade_detalhado_2026-08-05.xlsx"
    dest.write_text("x", encoding="utf-8")

    drop = write_sync_drop(DOMAIN_PRODUTIVIDADE, dest, extra={"row_count": 10})
    assert drop is not None
    assert drop.exists()
    payload = json.loads(drop.read_text(encoding="utf-8"))
    assert payload["domain"] == DOMAIN_PRODUTIVIDADE
    assert Path(payload["source_path"]) == dest.resolve()

    received: list[str] = []
    n = drain_sync_drops({DOMAIN_PRODUTIVIDADE: received.append})
    assert n == 1
    assert received == [str(dest.resolve())]
    assert not drop.exists()


def test_emit_production_detalhado_writes_drop_when_print_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_SYNC_DROP_DIR", str(tmp_path / "drop"))
    monkeypatch.setenv("ROBOT_LOGS_DIR", str(tmp_path / "logs"))
    dest = tmp_path / "detalhado.xlsx"
    dest.write_bytes(b"PK")

    with patch("builtins.print", side_effect=OSError(22, "Invalid argument")):
        _emit_production_detalhado_saved(dest, row_count=42)

    drops = list((tmp_path / "drop").glob("*.json"))
    assert len(drops) == 1
    payload = json.loads(drops[0].read_text(encoding="utf-8"))
    assert payload["domain"] == DOMAIN_PRODUTIVIDADE
    assert payload["extra"]["row_count"] == 42

    log_file = tmp_path / "logs" / "production.log"
    assert log_file.exists()
    assert "PRODUCTION_DETALHADO_SAVED|" in log_file.read_text(encoding="utf-8")


def test_robot_manager_drains_sync_drop(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_SYNC_DROP_DIR", str(tmp_path / "drop"))
    dest = tmp_path / "det.xlsx"
    dest.write_text("ok", encoding="utf-8")
    write_sync_drop(DOMAIN_PRODUTIVIDADE, dest)

    mgr = RobotProcessManager.__new__(RobotProcessManager)
    mgr._production_saved_callbacks = []
    mgr._monitor_eventos_saved_callbacks = []
    mgr._append_log = MagicMock()
    received: list[str] = []
    mgr.register_production_saved_callback(received.append)

    n = mgr._drain_bot_sync_drops()
    assert n == 1
    assert received == [str(dest.resolve())]


def test_drain_mantem_marker_quando_callback_falha_e_remove_no_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_SYNC_DROP_DIR", str(tmp_path / "drop"))
    dest = tmp_path / "ged.csv"
    dest.write_text("ok", encoding="utf-8")
    drop = write_sync_drop(DOMAIN_REINSPECAO_GED, dest)
    assert drop is not None

    failing = MagicMock(side_effect=RuntimeError("fila indisponivel"))
    assert drain_sync_drops({DOMAIN_REINSPECAO_GED: failing}) == 0
    assert drop.exists()

    received: list[str] = []
    assert drain_sync_drops({DOMAIN_REINSPECAO_GED: received.append}) == 1
    assert received == [str(dest.resolve())]
    assert not drop.exists()


def test_robot_manager_mantem_marker_se_callback_ged_falhar(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_SYNC_DROP_DIR", str(tmp_path / "drop"))
    dest = tmp_path / "ged.csv"
    dest.write_text("ok", encoding="utf-8")
    drop = write_sync_drop(DOMAIN_REINSPECAO_GED, dest)
    assert drop is not None

    mgr = RobotProcessManager.__new__(RobotProcessManager)
    mgr._production_saved_callbacks = []
    mgr._monitor_eventos_saved_callbacks = []
    mgr._production_ged_irregularidade_saved_callbacks = [
        MagicMock(side_effect=RuntimeError("enqueue falhou"))
    ]
    mgr._append_log = MagicMock()

    assert mgr._drain_bot_sync_drops() == 0
    assert drop.exists()


def test_notificacao_ged_normal_absorve_falha_do_callback():
    mgr = RobotProcessManager.__new__(RobotProcessManager)
    mgr._production_ged_irregularidade_saved_callbacks = [
        MagicMock(side_effect=RuntimeError("enqueue falhou"))
    ]
    mgr._append_log = MagicMock()

    mgr._notify_production_ged_irregularidade_saved("ged.csv")

    mgr._append_log.assert_called_once()


def test_drain_mantem_marker_sem_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_SYNC_DROP_DIR", str(tmp_path / "drop"))
    dest = tmp_path / "ged.csv"
    dest.write_text("ok", encoding="utf-8")
    drop = write_sync_drop(DOMAIN_REINSPECAO_GED, dest)
    assert drop is not None

    assert drain_sync_drops({}) == 0
    assert drop.exists()
