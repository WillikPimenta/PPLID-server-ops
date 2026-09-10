#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Harness de performance do Indicador EO (somente leitura).

Mede cache frio/quente, decomposição por fase e concorrência local.

Uso (a partir de backend/):
  python scripts/bench_qualidade_eo.py
  python scripts/bench_qualidade_eo.py --scenario jul_mtd --repeats 5
  python scripts/bench_qualidade_eo.py --all --json-out ../documentation/qualidade-eo-performance-benchmarks.json

Não altera dados. Resultados grandes/JSON devem ficar fora do commit se sensíveis;
o CSV resumido em documentation/ é o artefato versionável.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import threading
import time
import tracemalloc
from datetime import date
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

SCENARIOS: dict[str, dict[str, str]] = {
    "jul_full": {
        "label": "Jul/2026 completo — resumo",
        "module": "resumo",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "date_axis": "auditoria",
        "grain": "etapa",
    },
    "jul_mtd": {
        "label": "Jul/2026 MTD 1–24 — resumo",
        "module": "resumo",
        "start_date": "2026-07-01",
        "end_date": "2026-07-24",
        "date_axis": "auditoria",
        "grain": "etapa",
    },
    "jun_full": {
        "label": "Jun/2026 completo — resumo",
        "module": "resumo",
        "start_date": "2026-06-01",
        "end_date": "2026-06-30",
        "date_axis": "auditoria",
        "grain": "etapa",
    },
    "jul_agentes": {
        "label": "Jul/2026 — agentes (workforce + weighted)",
        "module": "agentes",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "date_axis": "auditoria",
        "grain": "etapa",
    },
    "jul_kpis_only": {
        "label": "Jul/2026 — só KPIs",
        "route": "kpis",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "date_axis": "auditoria",
        "grain": "etapa",
    },
    "jul_localidade": {
        "label": "Jul/2026 — filtro localidade (Brasília se existir)",
        "module": "resumo",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "date_axis": "auditoria",
        "grain": "etapa",
        "localidade": "Brasília",
    },
}


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _time_call(fn: Callable[[], Any]) -> tuple[Any, float, int]:
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed_ms, peak


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="jul_mtd", choices=sorted(SCENARIOS) + ["all"])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--csv", default="")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--explain", action="store_true", help="Grava EXPLAIN ANALYZE anonimizado")
    args = parser.parse_args()

    import django

    django.setup()

    from django.conf import settings
    from django.core.cache import cache
    from django.db import connection, reset_queries

    from apps.qualidade_operacional.services.analytics import (
        build_kpis,
        filtered_auditados,
        filtered_falhas,
    )
    from apps.qualidade_operacional.services.dashboard import build_dashboard
    from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version

    if args.all or args.scenario == "all":
        names = list(SCENARIOS)
    else:
        names = [args.scenario]
    rows: list[dict[str, Any]] = []
    cache_backend = settings.CACHES["default"]["BACKEND"]

    print(f"CACHE_BACKEND={cache_backend}")
    print(f"DB={settings.DATABASES['default'].get('NAME')}")

    for name in names:
        spec = dict(SCENARIOS[name])
        label = spec.pop("label")
        route = spec.pop("route", "dashboard")
        params = {k: v for k, v in spec.items() if v}

        def build():
            if route == "filter_counts":
                aud = filtered_auditados(params).count()
                fal = filtered_falhas(params).count()
                return {"auditados": aud, "falhas": fal}
            if route == "kpis":
                return build_kpis(params)
            return build_dashboard(params)

        # --- cold ---
        bump_quality_cache_version()
        cache.clear()
        reset_queries()
        settings.DEBUG = True  # enable connection.queries for count only
        payload, cold_ms, peak_cold = _time_call(build)
        n_queries_cold = len(connection.queries)
        settings.DEBUG = False
        payload_bytes = len(json.dumps(payload, default=str).encode("utf-8"))

        # --- warm repeats ---
        warm_ms: list[float] = []
        for _ in range(args.repeats):
            _, ms, _ = _time_call(build)
            warm_ms.append(ms)

        # --- rebuild cold phases (instrumented once) ---
        bump_quality_cache_version()
        cache.clear()
        phase_ms: dict[str, float] = {}
        t_all = time.perf_counter()
        t0 = time.perf_counter()
        aud_qs = filtered_auditados(params)
        fal_qs = filtered_falhas(params)
        _ = aud_qs.count()
        _ = fal_qs.count()
        phase_ms["filter_count"] = (time.perf_counter() - t0) * 1000
        if route == "filter_counts":
            phase_ms["build_kpis"] = 0.0
        else:
            t0 = time.perf_counter()
            _ = build_kpis(params)
            phase_ms["build_kpis"] = (time.perf_counter() - t0) * 1000
            if route != "kpis":
                t0 = time.perf_counter()
                _ = build_dashboard(params)
                phase_ms["build_dashboard_cold"] = (time.perf_counter() - t0) * 1000
        phase_ms["wall"] = (time.perf_counter() - t_all) * 1000

        conc: dict[str, Any] = {}
        if not args.skip_concurrency:
            bump_quality_cache_version()
            cache.clear()
            errors: list[str] = []
            times: list[float] = []

            def worker():
                try:
                    _, ms, _ = _time_call(build)
                    times.append(ms)
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))

            threads = [threading.Thread(target=worker) for _ in range(args.concurrency)]
            t0 = time.perf_counter()
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            wall = (time.perf_counter() - t0) * 1000
            conc = {
                "n": args.concurrency,
                "wall_ms": round(wall, 1),
                "per_request_ms": [round(x, 1) for x in times],
                "errors": errors[:3],
                "note": "Single-flight local ao processo; wall≈1 build se cache/gate ok",
            }

        row = {
            "scenario": name,
            "label": label,
            "route": route,
            "params": params,
            "cache_backend": cache_backend,
            "cold_ms": round(cold_ms, 1),
            "warm_p50_ms": round(_pct(warm_ms, 50), 1),
            "warm_p95_ms": round(_pct(warm_ms, 95), 1),
            "warm_mean_ms": round(statistics.mean(warm_ms), 1) if warm_ms else None,
            "warm_samples_ms": [round(x, 1) for x in warm_ms],
            "queries_cold": n_queries_cold,
            "payload_bytes": payload_bytes,
            "peak_tracemalloc_bytes_cold": peak_cold,
            "phase_ms": {k: round(v, 1) for k, v in phase_ms.items()},
            "eo_pct": (payload.get("kpis") or payload).get("eo_pct")
            if isinstance(payload, dict)
            else None,
            "auditados": (payload.get("kpis") or payload).get("auditados")
            if isinstance(payload, dict)
            else None,
            "concurrency": conc,
            "measured_at": date.today().isoformat(),
        }
        rows.append(row)
        print(
            f"[{name}] cold={row['cold_ms']}ms warm_p50={row['warm_p50_ms']}ms "
            f"warm_p95={row['warm_p95_ms']}ms queries={n_queries_cold} "
            f"payload={payload_bytes}B phases={row['phase_ms']}"
        )

        explain_targets = {
            "jul_mtd",
            "jul_full",
            "jun_full",
        }
        if args.explain and name in explain_targets:
            _write_explain(REPO, name, params)

    if args.csv:
        out = Path(args.csv)
        if not out.is_absolute():
            out = (Path.cwd() / out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        flat_fields = [
            "scenario",
            "label",
            "route",
            "cold_ms",
            "warm_p50_ms",
            "warm_p95_ms",
            "warm_mean_ms",
            "queries_cold",
            "payload_bytes",
            "cache_backend",
            "measured_at",
        ]
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=flat_fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"CSV -> {out}")

    if args.json_out:
        outj = Path(args.json_out)
        if not outj.is_absolute():
            outj = (Path.cwd() / outj).resolve()
        outj.parent.mkdir(parents=True, exist_ok=True)
        outj.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"JSON -> {outj} (nao versionar se contiver ambiente interno)")

    return 0


def _write_explain(repo: Path, name: str, params: dict) -> None:
    from django.db import connection

    from apps.qualidade_operacional.services.analytics import filtered_auditados, filtered_falhas

    out_dir = repo / "documentation" / "qualidade-eo-performance-query-plans"
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind, qs in (
        ("auditados", filtered_auditados(params)),
        ("falhas", filtered_falhas(params)),
    ):
        sql, sql_params = qs.query.sql_with_params()
        with connection.cursor() as cursor:
            cursor.execute(f"EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {sql}", sql_params)
            plan = "\n".join(r[0] for r in cursor.fetchall())
        # Anonimiza literals longos
        text = (
            f"# EXPLAIN {kind} — scenario={name}\n"
            f"# params keys={sorted(params.keys())}\n"
            f"# Gerado por bench_qualidade_eo.py --explain\n\n"
            f"{plan}\n"
        )
        (out_dir / f"{name}_{kind}.txt").write_text(text, encoding="utf-8")
        print(f"EXPLAIN -> {out_dir / f'{name}_{kind}.txt'}")


if __name__ == "__main__":
    raise SystemExit(main())
