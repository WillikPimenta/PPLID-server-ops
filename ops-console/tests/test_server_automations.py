from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server_automations as automations


def config_for(tmp_path: Path) -> dict:
    return {
        "logDir": str(tmp_path / "logs"),
        "automationRuntime": {"root": str(tmp_path / "runtime")},
        "MAIN": {"enabled": True, "postgresDb": "pplid_main"},
        "DEV": {"enabled": True, "postgresDb": "pplid_dev"},
        "HOM": {"enabled": False, "postgresDb": "pplid_hom"},
    }


def write_backend_env(tmp_path: Path, environment: str, database: str) -> Path:
    path = tmp_path / "deploy" / environment / "shared" / "backend.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"POSTGRES_DB={database}\n", encoding="utf-8")
    return path


def setup_bundle(tmp_path: Path, config: dict, bundle_id: str = "main-abc123") -> Path:
    root = automations.runtime_root(config)
    bundle = root / "bundles" / bundle_id
    python = bundle / ".venv" / ("Scripts/python.exe" if automations.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (bundle / "automacoes").mkdir(parents=True)
    automations._atomic_json(
        root / "settings.json",
        {"targetEnvironment": "MAIN", "targetEnvironments": ["MAIN"], "activeBundle": {"id": bundle_id}},
    )
    return bundle


def test_target_defaults_to_main_and_persists(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    settings = automations.get_settings(config)
    assert settings["targetEnvironment"] == "MAIN"
    assert settings["targetEnvironments"] == ["MAIN"]
    write_backend_env(tmp_path, "DEV", "pplid_dev")
    with patch.object(automations, "_validate_target_database"):
        saved = automations.update_settings(config, {"targetEnvironment": "DEV"}, "operator")
    assert saved["targetEnvironment"] == "DEV"
    assert saved["targetEnvironments"] == ["DEV"]
    assert automations.get_settings(config)["targetEnvironments"] == ["DEV"]


def test_settings_migration_from_single_target(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    automations._atomic_json(
        automations._settings_path(config),
        {"targetEnvironment": "MAIN", "activeBundle": None},
    )
    settings = automations.get_settings(config)
    assert settings["targetEnvironments"] == ["MAIN"]
    assert settings["targetEnvironment"] == "MAIN"


def test_disabled_target_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="desativado"):
        automations.update_settings(config_for(tmp_path), {"targetEnvironment": "HOM"}, "operator")
    with pytest.raises(ValueError, match="desativado"):
        automations.update_settings(config_for(tmp_path), {"targetEnvironments": ["HOM"]}, "operator")


def test_update_settings_accepts_two_targets(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    write_backend_env(tmp_path, "MAIN", "pplid_main")
    write_backend_env(tmp_path, "DEV", "pplid_dev")
    with patch.object(automations, "_validate_target_database"):
        saved = automations.update_settings(
            config, {"targetEnvironments": ["MAIN", "DEV"]}, "operator"
        )
    assert saved["targetEnvironments"] == ["MAIN", "DEV"]
    assert saved["targetEnvironment"] == "MAIN"


def test_update_settings_rejects_invalid_targets(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    write_backend_env(tmp_path, "MAIN", "pplid_main")
    write_backend_env(tmp_path, "DEV", "pplid_dev")
    with patch.object(automations, "_validate_target_database"):
        with pytest.raises(ValueError, match="no máximo"):
            automations.update_settings(
                config, {"targetEnvironments": ["MAIN", "DEV", "HOM"]}, "operator"
            )
        with pytest.raises(ValueError, match="no mínimo|ao menos"):
            automations.update_settings(config, {"targetEnvironments": []}, "operator")


def test_probe_target_availability(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    hom = automations.probe_target_availability(config, "HOM")
    assert hom["available"] is False
    assert "desativado" in hom["reason"]

    missing = automations.probe_target_availability(config, "MAIN")
    assert missing["available"] is False
    assert "backend.env ausente" in missing["reason"]

    write_backend_env(tmp_path, "MAIN", "pplid_main")
    with patch.object(automations, "_validate_target_database"):
        ok = automations.probe_target_availability(config, "MAIN")
    assert ok["available"] is True
    assert ok["database"] == "pplid_main"


def test_config_rejects_secrets_and_database_routing(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    with pytest.raises(ValueError, match="sensíveis"):
        automations.update_config(config, "production", {"config": {"senha": "secret"}})
    with pytest.raises(ValueError, match="sensíveis"):
        automations.update_config(config, "production", {"config": {"POSTGRES_DB": "wrong"}})


def test_start_persists_no_credentials(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    setup_bundle(tmp_path, config)
    write_backend_env(tmp_path, "DEV", "pplid_dev")
    automations._atomic_json(
        automations._settings_path(config),
        {
            "targetEnvironment": "DEV",
            "targetEnvironments": ["DEV"],
            "activeBundle": {"id": "main-abc123", "sha": "abc123", "sourceEnvironment": "MAIN"},
        },
    )
    fake_proc = MagicMock(pid=4321)
    automations._VALIDATED_CREDENTIALS["global"] = (
        automations._credential_digest("user1", "very-secret"),
        time.time() + 60,
    )
    with patch.object(automations, "_find_global_runner", return_value=None), patch.object(
        automations, "_validate_target_database"
    ), patch.object(automations, "_pid_alive", return_value=True), patch.object(
        automations.subprocess, "Popen", return_value=fake_proc
    ):
        result = automations.start_bot(
            config,
            "production",
            {"matricula": "user1", "senha": "very-secret"},
            "operator",
        )
    root = automations.runtime_root(config)
    persisted = (root / "state" / "production__DEV.json").read_text(encoding="utf-8")
    command = (root / "commands" / "production__DEV.json").read_text(encoding="utf-8")
    assert "very-secret" not in persisted + command
    assert result["bot"]["targetEnvironment"] == "DEV"
    assert result["bot"]["controller"] == "ops"


def test_start_with_two_targets_spawns_two_instances(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    setup_bundle(tmp_path, config)
    write_backend_env(tmp_path, "MAIN", "pplid_main")
    write_backend_env(tmp_path, "DEV", "pplid_dev")
    automations._atomic_json(
        automations._settings_path(config),
        {
            "targetEnvironment": "MAIN",
            "targetEnvironments": ["MAIN", "DEV"],
            "activeBundle": {"id": "main-abc123"},
        },
    )
    automations._VALIDATED_CREDENTIALS["global"] = (
        automations._credential_digest("user1", "secret"),
        time.time() + 60,
    )
    fake_proc = MagicMock(pid=7777)
    with patch.object(automations, "_find_global_runner", return_value=None), patch.object(
        automations, "_validate_target_database"
    ), patch.object(automations.subprocess, "Popen", return_value=fake_proc) as popen:
        result = automations.start_bot(
            config, "production", {"matricula": "user1", "senha": "secret"}, "operator"
        )
    assert popen.call_count == 2
    root = automations.runtime_root(config)
    assert (root / "state" / "production__MAIN.json").is_file()
    assert (root / "state" / "production__DEV.json").is_file()
    assert result["bot"]["targetEnvironments"] == ["MAIN", "DEV"]
    assert len(result["instances"]) == 2


def test_credential_validation_is_kept_only_in_memory(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    setup_bundle(tmp_path, config)
    completed = automations.subprocess.CompletedProcess([], 0, stdout='{"ok": true, "message": "OK"}\n', stderr="")
    captured_env = {}

    def fake_run(*_args, **kwargs):
        captured_env.update(kwargs["env"])
        return completed

    with patch.object(automations.subprocess, "run", side_effect=fake_run):
        result = automations.validate_credentials(
            config, {"matricula": "user1", "senha": "very-secret"}, "operator"
        )
    assert result["ok"] is True
    assert captured_env["OPS_BOT_PASSWORD"] == "very-secret"
    persisted = "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in automations.runtime_root(config).rglob("*.json")
    )
    assert "very-secret" not in persisted


def test_starting_supervisor_is_considered_running(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    automations._atomic_json(
        automations._state_path(config, "production", "MAIN"),
        {
            "mode": "production",
            "targetEnvironment": "MAIN",
            "status": "starting",
            "supervisorPid": 22,
            "runnerPid": None,
        },
    )
    with patch.object(automations, "_pid_alive", side_effect=lambda pid: pid == 22):
        status = automations.mode_status(config, "production")
        assert status["running"] is True
        assert status["instances"][0]["environment"] == "MAIN"


def test_mode_status_aggregates_error_over_running(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    automations._atomic_json(
        automations._state_path(config, "production", "MAIN"),
        {"mode": "production", "targetEnvironment": "MAIN", "status": "running", "supervisorPid": 11, "runnerPid": 12},
    )
    automations._atomic_json(
        automations._state_path(config, "production", "DEV"),
        {
            "mode": "production",
            "targetEnvironment": "DEV",
            "status": "error",
            "error": "falha de conexão",
            "supervisorPid": None,
            "runnerPid": None,
        },
    )
    with patch.object(automations, "_pid_alive", return_value=True):
        status = automations.mode_status(config, "production")
    assert status["running"] is True
    assert status["status"] == "error"
    assert "DEV" in (status.get("error") or "")


def test_legacy_runner_is_visible_and_blocks_duplicate(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    with patch.object(automations, "_find_global_runner", return_value=987):
        status = automations.mode_status(config, "production")
        assert status["running"] is True
        assert status["controller"] == "portal"
        assert status["runnerPid"] == 987
        with pytest.raises(ValueError, match="já está em execução"):
            automations.start_bot(config, "production", {"matricula": "u", "senha": "s"}, "operator")


def test_publish_copies_release_and_activates_atomically(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    current = tmp_path / "deploy" / "DEV" / "current"
    (current / "automacoes").mkdir(parents=True)
    (current / "backend").mkdir()
    (current / "automacoes" / "pyproject.toml").write_text("", encoding="utf-8")
    (current / "automacoes" / ".env").write_text("SECRET=do-not-copy", encoding="utf-8")
    (current / "backend" / "requirements.txt").write_text("", encoding="utf-8")
    (current / "meta.json").write_text(json.dumps({"sha": "abc123", "shaFull": "abc123full"}), encoding="utf-8")
    with patch.object(automations, "_find_global_runner", return_value=None), patch.object(automations, "_run_checked"):
        result = automations.publish_runtime(config, "DEV", "operator")
    assert result["bundle"]["id"] == "dev-abc123"
    assert (automations.runtime_root(config) / "bundles" / "dev-abc123" / "bundle.json").is_file()
    assert not (automations.runtime_root(config) / "bundles" / "dev-abc123" / "automacoes" / ".env").exists()
    assert automations.get_settings(config)["activeBundle"]["id"] == "dev-abc123"


def test_default_config_matches_portal_seed(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    production = automations.get_config(config, "production")["config"]
    rotina = automations.get_config(config, "rotina")["config"]
    assert production == automations.DEFAULT_PRODUCTION_CONFIG
    assert rotina == automations.DEFAULT_ROTINA_CONFIG


def test_publish_and_rollback_blocked_while_running(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    automations._atomic_json(
        automations._state_path(config, "production", "MAIN"),
        {"mode": "production", "targetEnvironment": "MAIN", "status": "running", "supervisorPid": 11, "runnerPid": 12},
    )
    with patch.object(automations, "_pid_alive", return_value=True):
        with pytest.raises(ValueError, match="Pare Production e Rotina"):
            automations.publish_runtime(config, "MAIN", "operator")
        with pytest.raises(ValueError, match="Pare Production e Rotina"):
            automations.rollback_runtime(config, "operator")


def test_start_freezes_target_environment_in_command(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    setup_bundle(tmp_path, config)
    write_backend_env(tmp_path, "MAIN", "pplid_main")
    write_backend_env(tmp_path, "DEV", "pplid_dev")
    automations._VALIDATED_CREDENTIALS["global"] = (
        automations._credential_digest("user1", "secret"),
        time.time() + 60,
    )
    fake_proc = MagicMock(pid=5555)
    with patch.object(automations, "_find_global_runner", return_value=None), patch.object(
        automations, "_validate_target_database"
    ), patch.object(automations.subprocess, "Popen", return_value=fake_proc) as popen:
        automations.start_bot(config, "production", {"matricula": "user1", "senha": "secret"}, "operator")
        write_backend_env(tmp_path, "DEV", "pplid_dev")
        with patch.object(automations, "_validate_target_database"):
            automations.update_settings(config, {"targetEnvironments": ["DEV"]}, "operator")
    command = json.loads((automations.runtime_root(config) / "commands" / "production__MAIN.json").read_text(encoding="utf-8"))
    assert command["targetEnvironment"] == "MAIN"
    args = popen.call_args[0][0]
    assert "--pplid-supervised" in args


def test_stop_creates_stop_files_for_all_instances(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    root = automations.runtime_root(config)
    for env, pid in (("MAIN", 44), ("DEV", 55)):
        automations._atomic_json(
            automations._state_path(config, "rotina", env),
            {
                "mode": "rotina",
                "targetEnvironment": env,
                "status": "running",
                "supervisorPid": pid,
                "runnerPid": pid + 1,
            },
        )
    with patch.object(automations, "_pid_alive", return_value=True):
        result = automations.stop_bot(config, "rotina")
    assert (root / "commands" / "rotina__MAIN.stop").is_file()
    assert (root / "commands" / "rotina__DEV.stop").is_file()
    assert result["bot"]["status"] == "stopping"


def test_read_logs_merges_instance_tails(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    main_log = automations._log_path(config, "production", "MAIN")
    dev_log = automations._log_path(config, "production", "DEV")
    main_log.parent.mkdir(parents=True, exist_ok=True)
    main_log.write_text("main-line\n", encoding="utf-8")
    dev_log.write_text("dev-line\n", encoding="utf-8")
    automations._atomic_json(
        automations._settings_path(config),
        {"targetEnvironment": "MAIN", "targetEnvironments": ["MAIN", "DEV"]},
    )
    logs = automations.read_logs(config, "production", tail=10)
    merged = "\n".join(logs["lines"])
    assert "[MAIN]" in merged
    assert "[DEV]" in merged
    assert "main-line" in merged
    assert "dev-line" in merged


def test_normalize_production_bounds(tmp_path: Path) -> None:
    normalized = automations.normalize_bot_config("production", {"tempo_espera_minutos": 999, "dias_download_brflow": 0})
    assert normalized["tempo_espera_minutos"] == 180
    assert normalized["dias_download_brflow"] == 1


def test_normalize_rotina_filters_invalid_tasks(tmp_path: Path) -> None:
    normalized = automations.normalize_bot_config("rotina", {"tarefas": ["produtividade_d1", "invalida", "produtividade_d1"]})
    assert normalized["tarefas"] == ["produtividade_d1"]


def test_update_config_blocked_only_for_ops_running(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    automations._atomic_json(
        automations._state_path(config, "production", "MAIN"),
        {
            "mode": "production",
            "targetEnvironment": "MAIN",
            "status": "running",
            "controller": "ops",
            "supervisorPid": 11,
            "runnerPid": 12,
        },
    )
    with patch.object(automations, "_pid_alive", return_value=True):
        with pytest.raises(ValueError, match="Pare o bot Ops"):
            automations.update_config(config, "production", {"config": {"tempo_espera_minutos": 30}})


def test_update_config_allowed_with_portal_runner(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    with patch.object(automations, "_find_global_runner", return_value=987):
        result = automations.update_config(config, "production", {"config": {"tempo_espera_minutos": 45}})
    assert result["config"]["tempo_espera_minutos"] == 45


def test_import_portal_config_seeds_runtime_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_for(tmp_path)
    portal_dir = tmp_path / "portal-config"
    portal_dir.mkdir()
    portal_file = portal_dir / "robot_config.json"
    portal_file.write_text(
        json.dumps({"production": {"tempo_espera_minutos": 90, "dias_download_brflow": 5}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROBOT_CONFIG_DIR", str(portal_dir))
    loaded = automations.get_config(config, "production")["config"]
    assert loaded["tempo_espera_minutos"] == 90
    assert loaded["dias_download_brflow"] == 5
    assert (automations.runtime_root(config) / "config" / "production.json").is_file()
