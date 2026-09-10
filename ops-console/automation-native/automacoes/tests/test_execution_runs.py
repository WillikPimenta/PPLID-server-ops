"""Testes do histórico de ciclos para o Pulso da frota."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config.constants import ROBOT_MODES
from app.services.robot_manager import CYCLE_RUNS_DEDUP_SECONDS, EXECUTION_RUNS_MAX, RobotProcessManager


def _make_manager(tmp_path: Path) -> RobotProcessManager:
    with patch.object(RobotProcessManager, "__init__", lambda self: None):
        manager = RobotProcessManager()
    manager.config_dir = tmp_path
    manager.execution_history_file = tmp_path / "execution_history.json"
    manager.cycle_runs_file = tmp_path / "cycle_runs.json"
    manager._lock = __import__("threading").Lock()
    manager._execution = {
        mode: {
            "started_at": None,
            "ended_at": None,
            "duration_seconds": None,
            "result": None,
        }
        for mode in ROBOT_MODES
    }
    manager._cycle_runs = {mode: [] for mode in ROBOT_MODES}
    manager._cycle_state = {
        mode: {"started_at": None, "last_progress": 0, "last_cycle_recorded_at": None}
        for mode in ROBOT_MODES
    }
    manager._runtime_ui = {
        mode: {
            "progress": 0,
            "status": "-",
            "updated_at": None,
            "cycle_started_at": None,
        }
        for mode in ROBOT_MODES
    }
    manager._stop_requested = {mode: False for mode in ROBOT_MODES}
    return manager


def test_progress_100_records_cycle_duration(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "nivel"
    started = datetime(2026, 6, 24, 10, 0, 0)
    ended = started + timedelta(seconds=60)

    manager._reset_cycle_state(mode, started.isoformat(timespec="seconds"))
    manager._record_cycle_from_progress(mode, "Nivel: ciclo concluido", ended.isoformat(timespec="seconds"))

    assert len(manager._cycle_runs[mode]) == 1
    assert manager._cycle_runs[mode][0]["duration_seconds"] == 60.0
    assert manager._cycle_runs[mode][0]["result"] == "sucesso"
    assert manager._cycle_runs[mode][0]["label"] == "Nivel: ciclo concluido"
    assert manager.cycle_runs_file.exists()
    assert manager._runtime_ui[mode]["cycle_started_at"] is None
    assert manager._cycle_state[mode]["started_at"] is None


def test_idle_after_progress_100_clears_cycle_started_at(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "nivel"
    started = datetime(2026, 6, 24, 10, 0, 0)
    ended = started + timedelta(seconds=45)

    manager._reset_cycle_state(mode, started.isoformat(timespec="seconds"))
    assert manager._runtime_ui[mode]["cycle_started_at"] is not None

    manager._record_cycle_from_progress(mode, "Nivel: ciclo concluido", ended.isoformat(timespec="seconds"))

    assert manager._runtime_ui[mode]["cycle_started_at"] is None
    assert manager._cycle_state[mode]["started_at"] is None


def test_second_progress_100_records_new_cycle(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "monitor"
    first_start = datetime(2026, 6, 24, 10, 0, 0)
    first_end = first_start + timedelta(seconds=60)
    second_start = first_end + timedelta(minutes=30)
    second_end = second_start + timedelta(seconds=45)

    manager._reset_cycle_state(mode, first_start.isoformat(timespec="seconds"))
    manager._record_cycle_from_progress(mode, "Monitor: ciclo concluido", first_end.isoformat(timespec="seconds"))
    manager._maybe_start_cycle_from_progress(mode, 35, second_start.isoformat(timespec="seconds"))
    manager._record_cycle_from_progress(mode, "Monitor: ciclo concluido", second_end.isoformat(timespec="seconds"))

    assert len(manager._cycle_runs[mode]) == 2
    assert manager._cycle_runs[mode][0]["duration_seconds"] == 60.0
    assert manager._cycle_runs[mode][1]["duration_seconds"] == 45.0


def test_wait_between_cycles_not_counted_in_duration(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "production"
    first_start = datetime(2026, 6, 24, 10, 0, 0)
    first_end = first_start + timedelta(seconds=60)
    second_start = first_end + timedelta(minutes=30)
    second_end = second_start + timedelta(seconds=55)

    manager._reset_cycle_state(mode, first_start.isoformat(timespec="seconds"))
    manager._record_cycle_from_progress(mode, "Producao: ciclo concluido", first_end.isoformat(timespec="seconds"))
    assert manager._cycle_state[mode]["started_at"] is None

    manager._maybe_start_cycle_from_progress(mode, 20, second_start.isoformat(timespec="seconds"))
    manager._record_cycle_from_progress(mode, "Producao: ciclo concluido", second_end.isoformat(timespec="seconds"))

    assert len(manager._cycle_runs[mode]) == 2
    assert manager._cycle_runs[mode][1]["duration_seconds"] == 55.0
    assert manager._cycle_runs[mode][1]["duration_seconds"] < 120.0


def test_progress_100_dedup_within_window(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "production"
    started = datetime(2026, 6, 24, 11, 0, 0)
    first_end = started + timedelta(seconds=30)
    duplicate_end = first_end + timedelta(seconds=1)

    manager._reset_cycle_state(mode, started.isoformat(timespec="seconds"))
    manager._record_cycle_from_progress(mode, "Producao: ciclo concluido", first_end.isoformat(timespec="seconds"))
    manager._record_cycle_from_progress(mode, "Producao: ciclo concluido", duplicate_end.isoformat(timespec="seconds"))

    assert len(manager._cycle_runs[mode]) == 1
    assert duplicate_end - first_end < timedelta(seconds=CYCLE_RUNS_DEDUP_SECONDS)


def test_append_cycle_run_persists_and_caps(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "rotina"

    for i in range(EXECUTION_RUNS_MAX + 5):
        manager._append_cycle_run(
            mode,
            60 + i,
            "sucesso",
            f"Ciclo {i}",
            f"2026-06-01T10:05:{i:02d}",
        )

    assert len(manager._cycle_runs[mode]) == EXECUTION_RUNS_MAX
    assert manager._cycle_runs[mode][0]["duration_seconds"] == 60 + 5
    assert manager._cycle_runs[mode][-1]["duration_seconds"] == 60 + EXECUTION_RUNS_MAX + 4

    saved = json.loads(manager.cycle_runs_file.read_text(encoding="utf-8"))
    assert len(saved[mode]) == EXECUTION_RUNS_MAX


def test_finalize_execution_does_not_append_total_duration(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "ged"
    started = datetime(2026, 6, 24, 12, 0, 0)
    ended = started + timedelta(hours=2)

    manager._execution[mode] = {
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": None,
        "duration_seconds": None,
        "result": "em execução",
    }
    manager._reset_cycle_state(mode, started.isoformat(timespec="seconds"))
    manager._cycle_state[mode]["last_progress"] = 50

    manager._maybe_append_interrupted_cycle(mode, ended, 0)

    assert manager._cycle_runs[mode] == []


def test_finalize_execution_records_interrupted_cycle(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "nivel"
    started = datetime(2026, 6, 24, 13, 0, 0)
    ended = started + timedelta(seconds=120)

    manager._execution[mode] = {
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": None,
        "duration_seconds": None,
        "result": "em execução",
    }
    manager._reset_cycle_state(mode, started.isoformat(timespec="seconds"))
    manager._cycle_state[mode]["last_progress"] = 40
    manager._stop_requested[mode] = True

    manager._maybe_append_interrupted_cycle(mode, ended, None)

    assert len(manager._cycle_runs[mode]) == 1
    assert manager._cycle_runs[mode][0]["duration_seconds"] == 120.0
    assert manager._cycle_runs[mode][0]["result"] == "interrompido"


def test_execution_trends_returns_cycle_series(tmp_path):
    manager = _make_manager(tmp_path)
    manager._cycle_runs["ged"] = [
        {
            "ended_at": "2026-06-22T12:00:00",
            "duration_seconds": 120.0,
            "result": "sucesso",
            "label": "GED: ciclo diurno concluido",
        },
        {
            "ended_at": "2026-06-23T12:00:00",
            "duration_seconds": 90.0,
            "result": "erro",
            "label": "GED: falha no download",
        },
    ]

    trends = manager.execution_trends(limite=10, modes=["ged", "invalid"])

    assert trends["limite"] == 10
    assert "ged" in trends["series"]
    assert len(trends["series"]["ged"]["points"]) == 2
    assert trends["series"]["ged"]["points"][1]["label"] == "GED: falha no download"
    assert "invalid" not in trends["series"]


def test_infer_cycle_result():
    assert RobotProcessManager._infer_cycle_result("Monitor: ciclo concluido") == "sucesso"
    assert RobotProcessManager._infer_cycle_result("Monitor: falha no download") == "erro"
    assert RobotProcessManager._infer_cycle_result("Erro Selenium") == "erro"
