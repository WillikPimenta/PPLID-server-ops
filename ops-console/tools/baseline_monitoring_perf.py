"""Baseline timings for monitoring builders (Fase 0 / verify-accept).

Runs in-process against ops config (no HTTP auth required).

  python tools/baseline_monitoring_perf.py
  python tools/baseline_monitoring_perf.py --cycles 10 --out tools/baseline_after.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

OPS_CONSOLE = Path(__file__).resolve().parents[1]
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server_monitoring as sm  # noqa: E402


def p95(samples: list[float]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95)))
    return ordered[idx]


def summarize(label: str, samples: list[float], extras: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "endpoint": label,
        "n": len(samples),
        "avg_ms": round(statistics.mean(samples), 1) if samples else 0,
        "p95_ms": round(p95(samples), 1),
        "max_ms": round(max(samples), 1) if samples else 0,
        "min_ms": round(min(samples), 1) if samples else 0,
    }
    if extras:
        row.update(extras)
    return row


def timed(fn: Callable[[], Any], cycles: int) -> tuple[list[float], Any]:
    samples: list[float] = []
    last: Any = None
    for _ in range(cycles):
        t0 = time.perf_counter()
        last = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return samples, last


def load_config() -> dict[str, Any]:
    cfg_path = Path(r"C:\PPLID\ops\config\env.config.json")
    if cfg_path.is_file():
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    return {"logDir": r"C:\PPLID\logs", "ops": {"opsStorePath": r"C:\PPLID\ops\data\ops-store.db"}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    config = load_config()

    # Clear short caches so first sample is cold, rest warm
    sm._DASHBOARD_CACHE.clear()
    sm._LOGS_CACHE.clear()
    sm._API_ROUTES_CACHE.clear()

    runners: list[tuple[str, Callable[[], Any]]] = [
        ("grouped_lite", lambda: sm.build_monitoring_grouped_events_lite(config, hours=24, limit=200)),
        ("grouped_full", lambda: sm.build_monitoring_grouped_events(config, hours=24)),
        (
            "dashboard_summary",
            lambda: sm.build_monitoring_dashboard(
                config, env_names=["DEV", "HOM", "MAIN"], tab="summary", include_deploy=True
            ),
        ),
        (
            "dashboard_latency",
            lambda: sm.build_monitoring_dashboard(
                config,
                env_names=["DEV", "HOM", "MAIN"],
                tab="latency",
                include_health_series=True,
                series_hours=24,
            ),
        ),
        (
            "dashboard_incidents",
            lambda: sm.build_monitoring_dashboard(
                config, env_names=["DEV", "HOM", "MAIN"], tab="incidents", event_hours=24
            ),
        ),
        ("summary_lite_dev", lambda: sm.build_monitoring_summary_lite(config, "DEV")),
        ("summary_full_dev", lambda: sm.build_monitoring_summary(config, "DEV")),
        ("logs_dev", lambda: sm.build_monitoring_service_logs(config, "DEV", limit=200)),
        ("api_routes_main", lambda: sm.build_monitoring_api_routes(config, "MAIN", window="6h")),
        (
            "series_latency_7d",
            lambda: sm.build_monitoring_series(config, "MAIN", "health_latency_ms", hours=168),
        ),
    ]

    rows: list[dict[str, Any]] = []
    for label, fn in runners:
        samples, last = timed(fn, args.cycles)
        extras: dict[str, Any] = {}
        if isinstance(last, dict):
            if "lite" in last:
                extras["lite"] = last.get("lite")
            if "timingMs" in last:
                extras["server_timingMs"] = last.get("timingMs")
            if "groups" in last:
                extras["groups"] = len(last.get("groups") or [])
            if "points" in last:
                extras["points"] = len(last.get("points") or [])
            if "cacheHit" in last:
                extras["last_cacheHit"] = last.get("cacheHit")
        row = summarize(label, samples, extras)
        rows.append(row)
        print(
            f"{label:22} avg={row['avg_ms']:7.1f}ms  p95={row['p95_ms']:7.1f}ms  "
            f"max={row['max_ms']:7.1f}ms  extras={extras}"
        )

    by = {r["endpoint"]: r for r in rows}
    gl, gf, ds = by["grouped_lite"], by["grouped_full"], by["dashboard_summary"]
    print("\nAcceptance:")
    print(f"  grouped lite p95={gl['p95_ms']}ms vs full p95={gf['p95_ms']}ms")
    print(f"  dashboard summary p95={ds['p95_ms']}ms (target <800 warm)")
    if gl["p95_ms"] and gf["p95_ms"] and gl["p95_ms"] < gf["p95_ms"]:
        print("  OK: lite faster than full")
    else:
        print("  WARN: lite not faster than full")

    out = args.out or str(OPS_CONSOLE / "tools" / "baseline_monitoring_perf_results.json")
    Path(out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
