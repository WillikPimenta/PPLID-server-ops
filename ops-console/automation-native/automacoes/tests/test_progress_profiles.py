"""Testes de perfis de progresso calibrados por logs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.bot_runtime import BotRuntime
from app.services.progress_profiles import (
    ProgressProfileStore,
    build_equal_weight_profile,
    fallback_linear_pct,
    normalize_step_label,
    resolve_pct,
)
from tools.build_progress_profiles import build_mode_profile, parse_log_runs


def test_normalize_step_label_dynamic():
    assert normalize_step_label("Monitor NH: c93123a concluido") == "Monitor NH: {user} concluido"
    assert normalize_step_label("Tarefa 3/8: produtividade") == "Tarefa {i}/{n}: produtividade"
    assert normalize_step_label("GED: download 47%") == "GED: download {pct}%"
    assert normalize_step_label("Nivel: ciclo concluido") == "Nivel: ciclo concluido"


def test_build_equal_weight_profile():
    profile = build_equal_weight_profile("nivel", ["A", "B", "C", "D"])
    pcts = [s["pct"] for s in profile["steps"]]
    assert pcts == [25, 50, 75, 100]


def test_resolve_pct_exact_match(tmp_path):
    path = tmp_path / "progress_profiles.json"
    path.write_text(
        json.dumps(
            {
                "nivel": {
                    "generated_at": "2026-01-01",
                    "steps": [
                        {"match": "Infra: driver iniciado", "pct": 20},
                        {"match": "Nivel: ciclo concluido", "pct": 100},
                    ],
                    "patterns": [],
                }
            }
        ),
        encoding="utf-8",
    )
    store = ProgressProfileStore(path)
    assert store.resolve_pct("nivel", "Infra: driver iniciado", 10) == 20
    assert store.resolve_pct("nivel", "desconhecido", 55) == 55


def test_fallback_linear_rotina_task():
    pct = fallback_linear_pct("rotina", "Tarefa 4/8: produtividade")
    assert pct is not None
    assert 70 <= pct <= 80


def test_fallback_linear_ged_download():
    pct = fallback_linear_pct("ged", "GED: download 50%")
    assert pct is not None
    assert 50 <= pct <= 55


def test_bot_runtime_uses_profile(tmp_path):
    path = tmp_path / "progress_profiles.json"
    path.write_text(
        json.dumps(
            {
                "monitor": {
                    "generated_at": "2026-01-01",
                    "steps": [{"match": "Monitor: ciclo concluido", "pct": 100}],
                    "patterns": [],
                }
            }
        ),
        encoding="utf-8",
    )

    import app.services.progress_profiles as pp

    pp._store = ProgressProfileStore(path)
    emitted: list[tuple[int, str]] = []
    runtime = BotRuntime(mode="monitor")
    runtime.set_progress_callback(lambda pct, msg: emitted.append((pct, msg)))
    try:
        runtime.set_progress(30, "Monitor: ciclo concluido")
    finally:
        pp._store = None

    assert emitted[-1][0] == 100


def test_parse_log_runs_wait_after_100_not_in_step_duration(tmp_path):
    log_path = tmp_path / "nivel.log"
    t0 = datetime(2026, 6, 24, 10, 0, 0)
    lines = [
        f"[{(t0 + timedelta(seconds=6)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(seconds=9)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
        f"[{(t0 + timedelta(minutes=45)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(minutes=45, seconds=3)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
        f"[{(t0 + timedelta(minutes=90)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(minutes=90, seconds=4)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    runs, _ = parse_log_runs(log_path, "nivel")
    assert len(runs) == 3
    profile = build_mode_profile("nivel", runs)
    infra = next(s for s in profile["steps"] if "Infra" in s["match"])
    assert infra["avg_seconds"] <= 10


def test_begin_cycle_resets_monotonic_progress():
    runtime = BotRuntime(mode="unknown_mode")
    seen: list[int] = []
    runtime.set_progress_callback(lambda pct, msg: seen.append(pct))
    runtime.set_progress(100, "Nivel: ciclo concluido")
    runtime.begin_cycle()
    runtime.set_progress(10, "Infra: driver iniciado")
    assert seen == [100, 10]


def test_parse_log_runs_splits_cycles(tmp_path):
    log_path = tmp_path / "nivel.log"
    t0 = datetime(2026, 6, 24, 10, 0, 0)
    lines = [
        f"[{t0.strftime('%Y-%m-%d %H:%M:%S')}] [start] iniciando robô 'nivel'",
        f"[{(t0 + timedelta(seconds=3)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|5|Nivel: iniciando loop",
        f"[{(t0 + timedelta(seconds=6)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(seconds=9)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|20|Autenticacao: acessando Okta",
        f"[{(t0 + timedelta(seconds=12)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
        f"[{(t0 + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|5|Nivel: iniciando loop",
        f"[{(t0 + timedelta(minutes=30, seconds=3)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(minutes=30, seconds=6)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
        f"[{(t0 + timedelta(minutes=31)).strftime('%Y-%m-%d %H:%M:%S')}] [start] iniciando robô 'nivel'",
        f"[{(t0 + timedelta(minutes=31, seconds=3)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|5|Nivel: iniciando loop",
        f"[{(t0 + timedelta(minutes=31, seconds=6)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(minutes=31, seconds=9)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|20|Autenticacao: acessando Okta",
        f"[{(t0 + timedelta(minutes=31, seconds=12)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    runs, _ = parse_log_runs(log_path, "nivel")
    assert len(runs) >= 3
    profile = build_mode_profile("nivel", runs)
    infra = next(s for s in profile["steps"] if "Infra" in s["match"])
    okta = next(s for s in profile["steps"] if "Okta" in s["match"] and "acessando" in s["match"])
    assert infra["avg_seconds"] < 60
    assert infra["pct"] < 80
    assert okta["pct"] > infra["pct"]


def test_sanity_check_rejects_bad_profile(tmp_path):
    path = tmp_path / "progress_profiles.json"
    path.write_text(
        json.dumps(
            {
                "nivel": {
                    "generated_at": "2026-01-01",
                    "steps": [{"match": "Infra: driver iniciado", "pct": 97}],
                    "patterns": [],
                }
            }
        ),
        encoding="utf-8",
    )
    store = ProgressProfileStore(path)
    assert store.resolve_pct("nivel", "Infra: driver iniciado", 10) == 10


@pytest.mark.parametrize("bad_pct", [99, 100])
def test_sanity_check_rejects_jump_from_zero_to_99(tmp_path, bad_pct):
    path = tmp_path / "progress_profiles.json"
    path.write_text(
        json.dumps(
            {
                "nivel": {
                    "generated_at": "2026-01-01",
                    "steps": [{"match": "Inicializando", "pct": bad_pct}],
                    "patterns": [],
                }
            }
        ),
        encoding="utf-8",
    )
    store = ProgressProfileStore(path)
    assert store.resolve_pct("nivel", "Inicializando", 0) == 0


def test_replicacao_d1_usa_marcos_deterministicos_mesmo_com_perfil_ruim(tmp_path):
    path = tmp_path / "progress_profiles.json"
    path.write_text(
        json.dumps(
            {
                "replicacao_auditoria_d1": {
                    "generated_at": "2026-01-01",
                    "steps": [{"match": "Inicializando", "pct": 99}],
                    "patterns": [],
                }
            }
        ),
        encoding="utf-8",
    )

    import app.services.progress_profiles as pp

    pp._store = ProgressProfileStore(path)
    emitted: list[int] = []
    runtime = BotRuntime(mode="replicacao_auditoria_d1")
    runtime.set_progress_callback(lambda pct, _msg: emitted.append(pct))
    try:
        runtime.set_progress(0, "Inicializando")
        runtime.set_progress(18, "Acessando Okta")
        runtime.set_progress(0, "Workflow 1/4: WF A")
        runtime.set_progress(0, "Workflow 4/4: WF D")
    finally:
        pp._store = None

    assert emitted == [0, 18, 50, 95]


def test_parse_log_runs_and_build_profile(tmp_path):
    log_path = tmp_path / "nivel.log"
    t0 = datetime(2026, 6, 24, 10, 0, 0)
    lines = [
        f"[{t0.strftime('%Y-%m-%d %H:%M:%S')}] [start] iniciando robô 'nivel'",
        f"[{(t0 + timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(seconds=70)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
        f"[{(t0 + timedelta(seconds=80)).strftime('%Y-%m-%d %H:%M:%S')}] [process-finished] exit=0",
        f"[{(t0 + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')}] [start] iniciando robô 'nivel'",
        f"[{(t0 + timedelta(minutes=5, seconds=12)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|10|Infra: driver iniciado",
        f"[{(t0 + timedelta(minutes=6, seconds=2)).strftime('%Y-%m-%d %H:%M:%S')}] PROGRESS|100|Nivel: ciclo concluido",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    runs, _ = parse_log_runs(log_path, "nivel")
    assert len(runs) == 2
    profile = build_mode_profile("nivel", runs)
    assert profile["steps"]
    assert profile["steps"][-1]["pct"] == 100
    infra = next(s for s in profile["steps"] if "Infra" in s["match"])
    assert infra["avg_seconds"] > 0


def test_resolve_pct_monotonic_via_runtime():
    runtime = BotRuntime(mode="nivel")
    seen: list[int] = []

    def capture(pct, msg):
        seen.append(pct)

    runtime.set_progress_callback(capture)
    runtime.set_progress(10, "A")
    runtime.set_progress(5, "B")
    assert seen[-1] >= seen[0]
