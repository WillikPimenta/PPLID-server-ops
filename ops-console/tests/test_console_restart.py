"""Exercise the restart worker in Windows PowerShell without touching a server."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("failure", [None, "throw 'restart denied'", "exit 7"])
def test_restart_worker_passes_named_parameters_and_records_outcome(tmp_path, failure):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell required")
    repo = tmp_path / "ops with spaces"
    repo.mkdir()
    (repo / "lib").mkdir()
    (repo / "ops-console").mkdir()
    logs = tmp_path / "shared logs"
    logs.mkdir()
    config = repo / "env config.json"
    config.write_text("{}")
    source = Path(__file__).resolve().parents[2] / "update_ops_console.ps1"
    shutil.copyfile(source, repo / source.name)
    (repo / "lib" / "paths.ps1").write_text(
        "function Get-PplidOpsConsoleDir { param($ScriptRoot) Join-Path $ScriptRoot 'ops-console' }"
    )
    (repo / "start_ops_console.ps1").write_text(
        "param([switch]$Restart, [string]$OpsDir, [string]$ConfigPath)\n"
        "if (-not $Restart) { throw 'Restart switch missing' }\n"
        "if (-not (Test-Path -LiteralPath $OpsDir)) { throw 'Invalid OpsDir' }\n"
        "if (-not (Test-Path -LiteralPath $ConfigPath)) { throw 'Invalid ConfigPath' }\n"
        "Write-Output 'restart invoked'\n" + (failure or "exit 0")
    )
    lock = logs / "console-update.lock"
    lock.write_text("{}")
    result_path = logs / "console-update.result.json"
    result_path.write_text(json.dumps({"phase": "restarting", "targetSha": "abcdef0"}))
    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / source.name),
         "-RestartOnly", "-OpsRepoDir", str(repo), "-ConfigPath", str(config),
         "-LogDir", str(logs), "-DelaySeconds", "0"],
        capture_output=True, timeout=30,
    )
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    assert completed.returncode == (1 if failure else 0), completed.stderr
    assert result["phase"] == ("failed" if failure else "done")
    assert result["ok"] is (failure is None)
    assert result["applied"] is True
    assert result["restarting"] is False
    assert not lock.exists()
    assert b"restart invoked" in completed.stdout
    if failure:
        assert "restart denied" in result["error"] or "exit code 7" in result["error"]
    else:
        assert result["targetSha"] == "abcdef0"
