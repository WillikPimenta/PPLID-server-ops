"""Testes do bootstrap independente do runtime de automações."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server
import server_automations as automations


def config_for(tmp_path: Path, *, with_ops_source: bool = True) -> dict:
    ops_native = tmp_path / "automation-native"
    if with_ops_source:
        (ops_native / "automacoes").mkdir(parents=True)
        (ops_native / "backend").mkdir(parents=True)
        (ops_native / "backend" / "requirements.txt").write_text("django>=5.0\n", encoding="utf-8")
        (ops_native / "automacoes" / "requirements.txt").write_text("selenium==4.25.0\n", encoding="utf-8")
        (ops_native / "automacoes" / "pyproject.toml").write_text(
            '[project]\nname = "automacoes"\nversion = "0.0.1"\n',
            encoding="utf-8",
        )
    return {
        "logDir": str(tmp_path / "logs"),
        "opsConsoleDir": str(tmp_path),
        "automationOpsDir": str(ops_native),
        "automationRuntime": {"root": str(tmp_path / "runtime"), "oktaHeadless": True},
        "MAIN": {"enabled": True, "postgresDb": "pplid_main"},
        "DEV": {"enabled": True, "postgresDb": "pplid_dev"},
        "HOM": {"enabled": False, "postgresDb": "pplid_hom"},
    }


@pytest.fixture(autouse=True)
def _reset_runtime_cache():
    automations._RUNTIME_READINESS = None
    automations._RUNTIME_BOOTSTRAP_STARTED = False
    yield
    automations._RUNTIME_READINESS = None
    automations._RUNTIME_BOOTSTRAP_STARTED = False


def test_resolve_automation_ops_dir_defaults_to_ops_console(tmp_path: Path):
    config = {"opsConsoleDir": str(tmp_path / "ops-console")}
    resolved = automations.resolve_automation_ops_dir(config)
    assert resolved == tmp_path / "ops-console" / "automation-native"


def test_resolve_config_paths_sets_automation_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server, "load_machine_config", lambda: {"baseDir": str(tmp_path)})
    config = server.resolve_config_paths({"opsConsoleDir": str(tmp_path / "ops-console")})
    assert config["automationOpsDir"] == str(tmp_path / "ops-console" / "automation-native")
    assert config["automationRuntime"]["oktaHeadless"] is True
    assert "automation-runtime" in config["automationRuntime"]["root"]


def test_ensure_automation_runtime_creates_venv(tmp_path: Path):
    config = config_for(tmp_path)
    fake_python = tmp_path / "runtime" / "native-bundle" / ".venv" / (
        "Scripts/python.exe" if automations.os.name == "nt" else "bin/python"
    )

    def fake_install(bundle_dir: Path) -> Path:
        python = automations._venv_python(bundle_dir)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")
        return python

    with patch.object(automations, "_install_bundle_venv", side_effect=fake_install):
        with patch.object(automations, "_smoke_test_bundle_python", return_value=(True, "")):
            result = automations.ensure_automation_runtime(config)

    assert result["ready"] is True
    assert result["depsInstalled"] is True
    assert Path(result["python"]) == fake_python
    assert (tmp_path / "runtime" / "native-bundle" / "automacoes").is_dir()
    assert (tmp_path / "runtime" / "native-bundle" / "backend").is_dir()
    assert (tmp_path / "runtime" / "native-bundle" / ".deps-hash").is_file()


def test_ensure_automation_runtime_reports_missing_source(tmp_path: Path):
    config = config_for(tmp_path, with_ops_source=False)
    result = automations.ensure_automation_runtime(config)
    assert result["ready"] is False
    assert "não encontrado" in result["reason"]


def test_validate_credentials_requires_ready_runtime(tmp_path: Path):
    config = config_for(tmp_path)
    with patch.object(
        automations,
        "ensure_automation_runtime",
        return_value={"ready": False, "reason": "Dependências ausentes"},
    ):
        with pytest.raises(automations.AutomationRuntimeNotReady, match="Dependências ausentes"):
            automations.validate_credentials(config, {"matricula": "user", "senha": "secret"}, "op")


def test_validate_credentials_surfaces_module_error(tmp_path: Path):
    config = config_for(tmp_path)
    native = tmp_path / "runtime" / "native-bundle"
    python = automations._venv_python(native)
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (native / "automacoes").mkdir(parents=True)
    (native / "backend").mkdir(parents=True)
    automations._atomic_json(
        automations._settings_path(config),
        {"activeBundle": {"id": "ops-native"}, "targetEnvironments": ["MAIN"]},
    )

    completed = automations.subprocess.CompletedProcess(
        [],
        1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'django'\n",
    )
    with patch.object(automations, "ensure_automation_runtime", return_value={"ready": True}):
        with patch.object(automations.subprocess, "run", return_value=completed):
            with pytest.raises(ValueError, match="Dependências do runtime ausentes"):
                automations.validate_credentials(
                    config, {"matricula": "user", "senha": "secret"}, "op"
                )


def test_okta_validate_headless_defaults_true():
    assert automations.okta_validate_headless({"automationRuntime": {}}) is True
    assert automations.okta_validate_headless({"automationRuntime": {"oktaHeadless": False}}) is False


def test_overview_includes_runtime_readiness(tmp_path: Path):
    config = config_for(tmp_path)
    automations._RUNTIME_READINESS = {
        "ready": True,
        "reason": "",
        "bundleId": "ops-native",
        "python": "x",
        "depsInstalled": True,
        "installing": False,
        "checkedAt": "now",
    }
    overview = automations.overview(config)
    assert overview["runtimeReadiness"]["ready"] is True
