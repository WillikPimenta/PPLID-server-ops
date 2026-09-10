"""Reentrada na UI: logs/progresso devem voltar do disco após restart do worker."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

from app.config.constants import ROBOT_MODES
from app.services.robot_manager import RobotProcessManager


def _make_manager(tmp_path: Path) -> RobotProcessManager:
    with patch.object(RobotProcessManager, "__init__", lambda self: None):
        manager = RobotProcessManager()
    manager.config_dir = tmp_path
    manager.logs_dir = tmp_path / "logs"
    manager.pids_dir = tmp_path / "pids"
    manager.logs_dir.mkdir(parents=True, exist_ok=True)
    manager.pids_dir.mkdir(parents=True, exist_ok=True)
    manager.execution_history_file = tmp_path / "execution_history.json"
    manager.cycle_runs_file = tmp_path / "cycle_runs.json"
    manager.robot_config_file = tmp_path / "robot_config.json"
    manager._lock = threading.Lock()
    manager._processes = {}
    manager._logs = {mode: __import__("collections").deque(maxlen=400) for mode in ROBOT_MODES}
    manager._log_files = {mode: manager.logs_dir / f"{mode}.log" for mode in ROBOT_MODES}
    manager._pid_files = {mode: manager.pids_dir / f"{mode}.pid" for mode in ROBOT_MODES}
    manager._execution = {
        mode: {
            "started_at": None,
            "ended_at": None,
            "duration_seconds": None,
            "result": None,
            "output_paths": None,
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
            "status": "Aguardando início",
            "updated_at": None,
            "cycle_started_at": None,
            "modo_segundo_plano": False,
        }
        for mode in ROBOT_MODES
    }
    manager._stop_requested = {mode: False for mode in ROBOT_MODES}
    manager._production_saved_callbacks = []
    manager._rotina_bruto_saved_callbacks = []
    manager._monitor_eventos_saved_callbacks = []
    return manager


def test_logs_fallback_to_disk_when_memory_empty(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "production"
    log_path = manager._log_files[mode]
    log_path.write_text(
        "\n".join(
            [
                "[2026-07-16 15:20:59] [start] iniciando robô 'production'",
                "[2026-07-16 15:21:00] STATUS|Processando lote",
                "[2026-07-16 15:21:01] PROGRESS|42|Quase na metade",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert list(manager._logs[mode]) == []
    payload = manager.logs(mode=mode, tail=40)

    assert payload[mode] == [
        "[start] iniciando robô 'production'",
        "STATUS|Processando lote",
        "PROGRESS|42|Quase na metade",
    ]
    # buffer aquecido para polls seguintes
    assert list(manager._logs[mode]) == payload[mode]


def test_status_rehydrates_runtime_from_disk_for_running_bot(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "tray_ui"
    manager._log_files[mode].write_text(
        "\n".join(
            [
                "[2026-07-16 15:20:59] STATUS|Monitorando bandeja",
                "[2026-07-16 15:21:10] PROGRESS|15|Ciclo em andamento",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manager._execution[mode]["result"] = "em execução"
    manager._execution[mode]["started_at"] = "2026-07-16T15:20:59"

    with patch.object(manager, "_mode_runtime", return_value=(True, 4369)):
        status = manager.status()

    assert status[mode]["running"] is True
    assert status[mode]["pid"] == 4369
    assert status[mode]["runtime"]["progress"] == 15
    assert status[mode]["runtime"]["status"] == "Ciclo em andamento"
    assert status[mode]["runtime"]["updated_at"] is not None


def test_status_rehydrates_plan_event_v1_without_breaking_legacy_progress(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "replicacao_auditoria_d1"
    event = {
        "version": 1,
        "run_id": "run-20260831",
        "phase": "planning_allocate",
        "phase_label": "Alocando protocolos",
        "state": "progress",
        "message": "Workflow 2 de 5",
        "progress_pct": 40,
        "current": 2,
        "total": 5,
        "elapsed_ms": 1250,
        "occurred_at": "2026-08-31T10:00:00.000+00:00",
    }
    manager._log_files[mode].write_text(
        "[2026-08-31 10:00:00] PLAN_EVENT|" + json.dumps(event) + "\n"
        "[2026-08-31 10:00:01] PROGRESS|9|Workflow 2 de 5\n",
        encoding="utf-8",
    )
    manager._execution[mode]["result"] = "em execução"

    with patch.object(manager, "_mode_runtime", return_value=(True, 999)):
        payload = manager.status()[mode]["runtime"]

    assert payload["progress"] == 9
    assert payload["phase_progress"] == 40
    assert payload["run_id"] == "run-20260831"
    assert payload["phase"] == "planning_allocate"
    assert payload["phase_state"] == "progress"
    assert payload["current"] == 2
    assert payload["total"] == 5


def test_status_reconciles_stale_em_execucao_when_process_dead(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "nivel"
    manager._execution[mode]["result"] = "em execução"
    manager._execution[mode]["started_at"] = "2026-07-16T15:20:59"
    manager._execution[mode]["ended_at"] = None

    with patch.object(manager, "_mode_runtime", return_value=(False, None)):
        status = manager.status()

    assert status[mode]["running"] is False
    assert status[mode]["execution"]["result"] == "interrompido"
    assert status[mode]["execution"]["ended_at"] is not None
    assert status[mode]["runtime"]["status"] == "Parada manual"
    saved = json.loads(manager.execution_history_file.read_text(encoding="utf-8"))
    assert saved[mode]["result"] == "interrompido"


def test_hydrate_from_disk_on_init_path(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "ged"
    manager._log_files[mode].write_text(
        "[2026-07-16 10:00:00] PROGRESS|80|Quase pronto\n",
        encoding="utf-8",
    )
    manager._execution[mode]["result"] = "em execução"
    manager._execution[mode]["started_at"] = "2026-07-16T10:00:00"

    with patch.object(manager, "_mode_runtime", return_value=(True, 111)):
        manager._hydrate_runtime_from_disk()

    assert manager._runtime_ui[mode]["progress"] == 80
    assert "Quase pronto" in manager._runtime_ui[mode]["status"]
    assert list(manager._logs[mode])[-1] == "PROGRESS|80|Quase pronto"


def test_rehydrate_prefers_manual_stop_over_last_status(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "nivel"
    manager._log_files[mode].write_text(
        "\n".join(
            [
                "[2026-07-17 09:39:00] [start] iniciando robô 'nivel'",
                "[2026-07-17 09:40:00] STATUS|Autenticacao: acessando Okta",
                "[2026-07-17 09:41:00] PROGRESS|65|Autenticacao: acessando Okta",
                "[2026-07-17 09:42:00] [stop] parada solicitada para 'nivel'",
                "[2026-07-17 09:42:01] [process-finished] exit=None",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manager._execution[mode]["result"] = "interrompido"
    manager._execution[mode]["started_at"] = "2026-07-17T09:40:00"
    manager._execution[mode]["ended_at"] = "2026-07-17T09:42:01"

    with patch.object(manager, "_mode_runtime", return_value=(False, None)):
        manager._hydrate_runtime_from_disk()

    assert manager._runtime_ui[mode]["status"] == "Parada manual"
    assert manager._runtime_ui[mode]["progress"] == 0


def test_rehydrate_ignores_stop_from_previous_run(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "nivel"
    manager._log_files[mode].write_text(
        "\n".join(
            [
                "[2026-07-17 08:00:00] [start] iniciando robô 'nivel'",
                "[2026-07-17 08:01:00] [stop] parada solicitada para 'nivel'",
                "[2026-07-17 09:00:00] [start] iniciando robô 'nivel'",
                "[2026-07-17 09:30:00] STATUS|Rotina concluída - Robô finalizado",
                "[2026-07-17 09:30:01] PROGRESS|100|ok",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manager._execution[mode]["result"] = "sucesso"
    manager._runtime_ui[mode]["updated_at"] = None

    with patch.object(manager, "_mode_runtime", return_value=(False, None)):
        manager._hydrate_runtime_from_disk()

    assert manager._runtime_ui[mode]["status"] == "Concluído com sucesso"
    assert manager._runtime_ui[mode]["progress"] == 100


def test_status_overrides_stale_status_when_interrupted(tmp_path):
    manager = _make_manager(tmp_path)
    mode = "ged"
    manager._runtime_ui[mode]["status"] = "GED: aguardando próxima execução diurna"
    manager._runtime_ui[mode]["progress"] = 42
    manager._runtime_ui[mode]["updated_at"] = "2026-07-17T09:40:00"
    manager._execution[mode]["result"] = "interrompido"

    with patch.object(manager, "_mode_runtime", return_value=(False, None)):
        payload = manager.status()

    assert payload[mode]["runtime"]["status"] == "Parada manual"
    assert payload[mode]["runtime"]["progress"] == 0
