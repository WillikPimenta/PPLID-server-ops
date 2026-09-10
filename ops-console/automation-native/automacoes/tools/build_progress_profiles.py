#!/usr/bin/env python3
"""Build progress_profiles.json from robot log files (manual calibration)."""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.constants import ROBOT_MODES, ROBOT_MODES_PRIMARY
from app.config.paths import PASTA_CONFIG
from app.services.progress_profiles import (
    CANONICAL_STEPS,
    MAX_STEP_DELTA_SECONDS,
    MIN_EXECUTIONS_FOR_CALIBRATION,
    PATTERN_DEFS,
    PROGRESS_PROFILES_FILENAME,
    build_equal_weight_profile,
    default_profiles_path,
    normalize_step_label,
    sort_steps_canonical,
)


_LOG_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(.*)$")
_PROGRESS_RE = re.compile(r"^PROGRESS\|(\d+)\|(.*)$")
_TIMING_RE = re.compile(r"^[⏱✓]\s+(.+?):\s+([\d.]+)s\s*$")


def _default_logs_dir() -> Path:
    appdata = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    default = appdata / "PLAN_IDF_SERASA_BOTS" / "logs"
    return Path(os.getenv("ROBOT_LOGS_DIR", str(default)))


def _parse_log_timestamp(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _is_run_boundary(payload: str) -> bool:
    if payload.startswith("[start]"):
        return True
    if payload.startswith("[process-finished]"):
        return True
    if payload.startswith("PROGRESS|0|"):
        return True
    return False


def _is_pattern_step(label: str, mode: str) -> bool:
    for pat in PATTERN_DEFS.get(mode, []):
        try:
            if re.match(pat["regex"], label):
                return True
        except re.error:
            continue
    return False


def parse_log_runs(log_path: Path, mode: str) -> tuple[list[list[tuple[datetime, str, str]]], int]:
    """Return list of runs; each run is [(ts, raw_msg, normalized_msg), ...]."""
    if not log_path.exists():
        return [], 0

    runs: list[list[tuple[datetime, str, str]]] = []
    current: list[tuple[datetime, str, str]] = []
    timing_count = 0

    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LOG_TS_RE.match(line.strip())
        if not m:
            continue
        ts = _parse_log_timestamp(m.group(1))
        if ts is None:
            continue
        payload = m.group(2).strip()

        timing_m = _TIMING_RE.match(payload)
        if timing_m:
            timing_count += 1
            continue

        prog_m = _PROGRESS_RE.match(payload)
        if prog_m:
            raw_msg = prog_m.group(2).strip()
            norm = normalize_step_label(raw_msg)
            try:
                pct_val = int(float(prog_m.group(1)))
            except (TypeError, ValueError):
                pct_val = None
            if _is_run_boundary(payload) and current:
                runs.append(current)
                current = []
            current.append((ts, raw_msg, norm))
            if pct_val == 100:
                runs.append(current)
                current = []
            continue

        if _is_run_boundary(payload):
            if current:
                runs.append(current)
                current = []

    if current:
        runs.append(current)

    # Drop pattern-only micro steps from duration aggregation (handled via patterns)
    filtered: list[list[tuple[datetime, str, str]]] = []
    for run in runs:
        slim = [item for item in run if not _is_pattern_step(item[1], mode)]
        if slim:
            filtered.append(slim)
    return filtered, timing_count


def _step_durations_from_run(
    run: list[tuple[datetime, str, str]],
    max_delta: float = MAX_STEP_DELTA_SECONDS,
) -> dict[str, float]:
    """Duração da fase = tempo até o próximo PROGRESS (atribuído à etapa anterior)."""
    if len(run) < 2:
        return {}
    out: dict[str, float] = {}
    for i in range(1, len(run)):
        delta = max(0.0, (run[i][0] - run[i - 1][0]).total_seconds())
        if delta > max_delta:
            continue
        label = run[i - 1][2]
        if label:
            out[label] = out.get(label, 0.0) + delta
    return out



def build_mode_profile(mode: str, runs: list[list[tuple[datetime, str, str]]]) -> dict:
    generated_at = datetime.now().isoformat(timespec="seconds")

    if len(runs) < MIN_EXECUTIONS_FOR_CALIBRATION:
        labels: list[str] = []
        for run in runs:
            for _, raw, norm in run:
                if norm and norm not in labels:
                    labels.append(norm)
        labels = sort_steps_canonical(mode, labels)
        if mode in CANONICAL_STEPS:
            ordered = [label for label in CANONICAL_STEPS[mode] if label in labels]
            for label in labels:
                if label not in ordered:
                    ordered.append(label)
            labels = ordered
        print(
            f"  [{mode}] poucas execuções ({len(runs)}); usando pesos iguais entre {len(labels)} etapa(s)",
            file=sys.stderr,
        )
        profile = build_equal_weight_profile(mode, labels, generated_at=generated_at)
        profile["patterns"] = list(PATTERN_DEFS.get(mode, []))
        return profile

    duration_samples: dict[str, list[float]] = defaultdict(list)
    seen_labels: set[str] = set()
    for run in runs:
        for _, _, norm in run:
            if norm:
                seen_labels.add(norm)
        for norm, secs in _step_durations_from_run(run).items():
            duration_samples[norm].append(secs)

    medians = {k: statistics.median(v) for k, v in duration_samples.items() if v}
    ordered_labels = sort_steps_canonical(mode, list(seen_labels))
    if mode in CANONICAL_STEPS:
        ordered_labels = [label for label in CANONICAL_STEPS[mode] if label in seen_labels]
        for label in sort_steps_canonical(mode, list(seen_labels)):
            if label not in ordered_labels:
                ordered_labels.append(label)
    if not ordered_labels:
        ordered_labels = sort_steps_canonical(mode, list(medians.keys()))

    total = sum(medians.get(label, 0.0) for label in ordered_labels) or 1.0
    cumulative = 0.0
    steps = []
    for label in ordered_labels[:-1]:
        cumulative += medians.get(label, 0.0) / total
        pct = max(1, min(99, round(cumulative * 100)))
        steps.append(
            {
                "match": label,
                "match_type": "exact",
                "avg_seconds": round(medians.get(label, 0.0), 1),
                "pct": pct,
            }
        )
    if ordered_labels:
        last = ordered_labels[-1]
        steps.append(
            {
                "match": last,
                "match_type": "exact",
                "avg_seconds": round(medians.get(last, 0.0), 1),
                "pct": 100,
            }
        )

    return {
        "generated_at": generated_at,
        "steps": steps,
        "patterns": list(PATTERN_DEFS.get(mode, [])),
    }


def build_all_profiles(
    logs_dir: Path,
    modes: list[str] | None = None,
) -> dict[str, dict]:
    target_modes = modes or list(ROBOT_MODES_PRIMARY) + ["replicacao_auditoria", "tray_ui"]
    profiles: dict[str, dict] = {}
    for mode in target_modes:
        if mode not in ROBOT_MODES:
            continue
        log_path = logs_dir / f"{mode}.log"
        runs, timing_lines = parse_log_runs(log_path, mode)
        print(f"  [{mode}] {len(runs)} execução(ões), {timing_lines} linhas de timing", file=sys.stderr)
        profiles[mode] = build_mode_profile(mode, runs)
    return profiles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera progress_profiles.json a partir dos logs dos robôs.")
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help="Diretório dos logs (default: ROBOT_LOGS_DIR ou %%APPDATA%%/PLAN_IDF_SERASA_BOTS/logs)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Arquivo de saída (default: {PASTA_CONFIG / PROGRESS_PROFILES_FILENAME})",
    )
    parser.add_argument(
        "--modes",
        type=str,
        default="",
        help="Modos separados por vírgula (default: todos os principais)",
    )
    args = parser.parse_args(argv)

    logs_dir = args.logs_dir or _default_logs_dir()
    output = args.output or default_profiles_path()
    modes = [m.strip() for m in args.modes.split(",") if m.strip()] or None

    print(f"Logs: {logs_dir}", file=sys.stderr)
    print(f"Saída: {output}", file=sys.stderr)

    profiles = build_all_profiles(logs_dir, modes)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Perfil salvo em {output} ({len(profiles)} modo(s))", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
