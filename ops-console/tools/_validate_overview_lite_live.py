"""One-shot warm overview-lite measurement against live backends."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

OPS = Path(__file__).resolve().parents[1]
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import server  # noqa: E402
from health_probe import get_coordinator  # noqa: E402

cfg = server.load_config(Path(r"C:\PPLID\ops\config\env.config.json"))
coord = get_coordinator()
coord.reset_for_tests()

for env in ("MAIN", "DEV", "HOM"):
    if not cfg.get(env):
        continue
    t0 = time.perf_counter()
    snap = coord.get_snapshot(cfg, env, wait=True, force=True)
    ms = (time.perf_counter() - t0) * 1000
    print(
        f"warm {env}: {ms:.0f}ms class={snap.get('availabilityClass')} "
        f"status={snap.get('status')} reachable={snap.get('reachable')} "
        f"dur={snap.get('durationMs')}"
    )

samples: list[float] = []
overview = None
for _ in range(30):
    t0 = time.perf_counter()
    overview = server.build_overview(cfg, lite=True)
    samples.append((time.perf_counter() - t0) * 1000)

ordered = sorted(samples)
p50 = ordered[len(ordered) // 2]
p95 = ordered[int(len(ordered) * 0.95) - 1]
metrics = coord.metrics()
env_diag = {}
for name, env in (overview.get("environments") or {}).items():
    rt = env.get("runtime") or {}
    env_diag[name] = {
        "status": rt.get("status"),
        "availClass": rt.get("availabilityClass"),
        "ageMs": rt.get("ageMs"),
        "stale": rt.get("stale"),
        "agg": env.get("availabilityAggregate"),
        "displayPhase": env.get("displayPhase"),
        "database": rt.get("database"),
    }

print(
    json.dumps(
        {
            "overview_lite_warm": {
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "max_ms": round(max(samples), 1),
                "min_ms": round(min(samples), 1),
            },
            "envs": env_diag,
            "coord": {
                "executed": metrics.get("health_probe_executed_total"),
                "cache_hits": metrics.get("health_probe_cache_hit_total"),
                "coalesced": metrics.get("health_probe_coalesced_total"),
                "timeouts": metrics.get("health_probe_timeout_total"),
            },
            "diagnostics": overview.get("diagnostics"),
        },
        indent=2,
        ensure_ascii=False,
    )
)
