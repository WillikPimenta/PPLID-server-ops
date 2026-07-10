"""Testes de logs de deploy e parsing de falhas."""
from __future__ import annotations

import json
from pathlib import Path

import server_ops as so


def test_parse_log_offsets():
    assert so.parse_log_offsets("build.log:10,validate.log:0") == {
        "build.log": {"file": 10, "sqlite": 0},
        "validate.log": {"file": 0, "sqlite": 0},
    }
    assert so.parse_log_offsets("build.log:f1200/s45")["build.log"] == {"file": 1200, "sqlite": 45}
    assert so.parse_log_offsets("") == {}


def test_compute_deploy_progress_pct():
    steps = [
        {"id": "prepare", "status": "success"},
        {"id": "git_fetch", "status": "success"},
        {"id": "deps_backend", "status": "running"},
    ]
    pct = so.compute_deploy_progress_pct(steps)
    assert 10 < pct < 40


def test_build_failure_payload_from_summary(tmp_path: Path):
    base = tmp_path
    env = "DEV"
    run_id = "test-run"
    run_dir = base / "deploy" / env / "logs" / "runs" / run_id
    run_dir.mkdir(parents=True)
    summary = {
        "result": "failed",
        "failedStep": "deps_backend",
        "lastError": "pip install falhou.",
        "errorDetail": {
            "rootCause": "timeout",
            "message": "pip install excedeu o tempo limite (600s).",
            "command": "pip install -r requirements.txt",
            "recommendation": "Reexecute o deploy.",
        },
    }
    (run_dir / "run-summary.json").write_text(json.dumps(summary), encoding="utf-8")
    steps = [
        {"id": "deps_backend", "label": "Instalacao dependencias", "status": "error", "error": "pip install falhou."}
    ]
    (run_dir / "steps.json").write_text(json.dumps(steps), encoding="utf-8")

    payload = so.build_failure_payload(summary, steps, {"lastError": {"step": "deps_backend", "message": "pip install falhou."}})
    assert payload is not None
    assert payload["step"] == "deps_backend"
    assert payload["rootCause"] == "timeout"
    assert "requirements.txt" in str(payload.get("command"))


def test_load_run_log_chunk_from_file(tmp_path: Path):
    base = tmp_path
    env = "DEV"
    run_id = "chunk-run"
    run_dir = base / "deploy" / env / "logs" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log_path = run_dir / "build.log"
    log_path.write_text("[2026-01-01 00:00:00] [INFO] linha1\n[2026-01-01 00:00:01] [INFO] linha2\n", encoding="utf-8")

    first = so.load_run_log_chunk(base, env, run_id, "build.log", offset=0, limit=10)
    assert len(first["lines"]) == 2
    assert first["nextOffset"] == 2

    second = so.load_run_log_chunk(base, env, run_id, "build.log", offset=first["nextOffset"], limit=10)
    assert second["lines"] == []


def test_extract_deploy_log_for_sha_returns_empty_when_not_found(tmp_path: Path):
    from server import extract_deploy_log_for_sha

    log_path = tmp_path / "deploy.log"
    log_path.write_text(
        "[2026-01-01 00:00:00] DEPLOY INICIADO abc123\n"
        "[2026-01-01 00:00:05] DEPLOY CONCLUIDO\n"
        "[2026-01-01 00:01:00] DEPLOY INICIADO def456\n"
        "[2026-01-01 00:01:05] DEPLOY CONCLUIDO\n",
        encoding="utf-8",
    )
    assert extract_deploy_log_for_sha(log_path, "zzz999") == []
    found = extract_deploy_log_for_sha(log_path, "abc123")
    assert found and "abc123" in found[0]
