#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Baseline e guarda de paridade/performance da Qualidade Operacional.

Somente leitura no banco de negocio. Limpa/versiona apenas o cache da aplicacao
para medir reconstrucoes frias. Payloads completos ficam em backend/tmp.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def _base(module="resumo", **extra):
    params = {
        "module": module, "start_date": "2026-07-01", "end_date": "2026-07-24",
        "date_axis": "auditoria", "grain": "etapa",
    }
    params.update(extra)
    return params


SCENARIOS = {
    "resumo_jul_mtd": _base(dim="id_cliente", metric="quantidade"),
    "resumo_jun": _base(end_date="2026-06-30", start_date="2026-06-01", dim="id_cliente", metric="quantidade"),
    "resumo_cliente_83": _base(id_cliente="83", dim="id_cliente", metric="quantidade"),
    "resumo_workflow_450": _base(id_workflow="450", dim="id_workflow", metric="quantidade"),
    "resumo_tipo_g_auditoria": _base(tipo_analise="G Auditoria", dim="tipo_analise", metric="quantidade"),
    "resumo_eixo_analise": _base(date_axis="analise", dim="id_cliente", metric="quantidade"),
    "resumo_grao_protocolo": _base(grain="protocolo", dim="id_cliente", metric="quantidade"),
    "agentes_jul": _base("agentes"),
    "contestacao_jul": _base("contestacao"),
    "cliente_83_jul": _base("cliente", id_cliente="83"),
}
LIST_SCENARIOS = {
    "auditados_pagina_1": ("auditados", _base(page="1", page_size="25")),
    "falhas_pagina_1": ("falhas", _base(page="1", page_size="25")),
    "falhas_cliente_83": ("falhas", _base(id_cliente="83", page="1", page_size="25")),
}
for _, params in LIST_SCENARIOS.values():
    params.pop("module", None)
    params.pop("grain", None)


def _canonical(value: Any) -> bytes:
    from django.core.serializers.json import DjangoJSONEncoder
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _comparison_payload(name: str, payload: Any) -> Any:
    """Normaliza só os quartis empatados, instáveis antes desta mudança."""
    comparable = copy.deepcopy(payload)
    if not isinstance(comparable, dict):
        return comparable
    if name == "agentes_jul":
        for section_name in ("ranking", "facilitator_ranking"):
            section = comparable.get(section_name) or {}
            section.pop("quartis", None)
            for row in section.get("results") or []:
                row.pop("quartil", None)
    elif name == "cliente_83_jul":
        section = comparable.get("responsaveis") or {}
        section.pop("quartis", None)
        for row in section.get("results") or []:
            row.pop("quartil", None)
    return comparable

def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p / 100
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _git(*args: str, binary=False):
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True,
                          text=not binary, encoding=None if binary else "utf-8").stdout


def _environment():
    import django
    from django.conf import settings
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("select version()")
        db_version = cursor.fetchone()[0]
    return {
        "python": sys.version, "platform": platform.platform(), "django": django.get_version(),
        "database_vendor": connection.vendor, "database_version": db_version,
        "cache_backend": settings.CACHES["default"]["BACKEND"],
        "cache_ttl_seconds": settings.QUALIDADE_OPERACIONAL_CACHE_TTL,
        "max_concurrent": settings.QUALIDADE_OPERACIONAL_MAX_CONCURRENT,
        "queue_wait_ms": settings.QUALIDADE_OPERACIONAL_QUEUE_WAIT_MS,
        "build_wait_seconds": settings.QUALIDADE_OPERACIONAL_BUILD_WAIT_S,
        "git_commit": _git("rev-parse", "HEAD").strip(),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "git_diff_sha256": _sha(_git("diff", "--binary", binary=True)),
    }


def _content_fingerprint(model):
    from django.db import connection
    fields = [f.column for f in model._meta.concrete_fields
              if f.column not in {"imported_at", "source_file"}]
    quoted = [connection.ops.quote_name(field) for field in fields]
    values = ", ".join(f"coalesce({field}::text, '<NULL>')" for field in quoted)
    table = connection.ops.quote_name(model._meta.db_table)
    sql = f"""select count(*)::bigint, coalesce(sum(id), 0)::text,
        min(id)::bigint, max(id)::bigint,
        bit_xor(hashtextextended(concat_ws(chr(31), {values}), 0))::text
        from {table}"""
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(sql)
        count, id_sum, min_id, max_id, digest = cursor.fetchone()
    return {"count": count, "id_sum": id_sum, "min_id": min_id, "max_id": max_id,
            "business_columns": fields, "commutative_hash64": digest,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}


def _monthly(model, field):
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    rows = (model.objects.exclude(**{f"{field}__isnull": True}).annotate(month=TruncMonth(field))
            .values("month").annotate(count=Count("id")).order_by("month"))
    return [{"month": row["month"].isoformat(), "count": row["count"]} for row in rows]


def _database_fingerprint():
    from django.db import connection
    from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
    content = {
        "qualidade_auditado": _content_fingerprint(QualidadeAuditado),
        "qualidade_falha": _content_fingerprint(QualidadeFalha),
        "auditados_by_data_month": _monthly(QualidadeAuditado, "data"),
        "auditados_by_analise_month": _monthly(QualidadeAuditado, "data_analise"),
        "falhas_by_data_month": _monthly(QualidadeFalha, "data"),
        "falhas_by_analise_month": _monthly(QualidadeFalha, "data_analise"),
    }
    physical = {}
    with connection.cursor() as cursor:
        for table in ("qualidade_auditado", "qualidade_falha"):
            cursor.execute("select pg_relation_size(%s), pg_indexes_size(%s), pg_total_relation_size(%s)",
                           [table, table, table])
            heap, indexes, total = cursor.fetchone()
            cursor.execute("""select indexname, indexdef from pg_indexes
                where schemaname=current_schema() and tablename=%s order by indexname""", [table])
            physical[table] = {"heap_bytes": heap, "indexes_bytes": indexes, "total_bytes": total,
                               "indexes": [{"name": n, "definition": d} for n, d in cursor.fetchall()]}
    return {"content": content, "physical": physical}


def _measure(name: str, params: dict, build: Callable[[], Any], payload_dir: Path, repeats: int):
    from django.core.cache import cache
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
    bump_quality_cache_version()
    cache.clear()
    started = time.perf_counter()
    with CaptureQueriesContext(connection) as queries:
        payload = build()
    cold_ms = (time.perf_counter() - started) * 1000
    canonical = _canonical(payload)
    digest = _sha(canonical)
    path = payload_dir / f"{name}.json"
    path.write_bytes(canonical)
    warm = []
    for _ in range(repeats):
        warm_started = time.perf_counter()
        warm_payload = build()
        warm.append((time.perf_counter() - warm_started) * 1000)
        if _sha(_canonical(warm_payload)) != digest:
            raise RuntimeError(f"Payload instavel no cache quente: {name}")
    return {
        "params": params,
        "sha256": digest,
        "comparison_sha256": _sha(_canonical(_comparison_payload(name, payload))),
        "payload_bytes": len(canonical),
        "payload_file": str(path.relative_to(ROOT)), "cold_ms": round(cold_ms, 1),
        "cold_queries": len(queries),
        "cold_query_db_ms": round(sum(float(q.get("time") or 0) for q in queries) * 1000, 1),
        "warm_samples_ms": [round(v, 3) for v in warm],
        "warm_p50_ms": round(_pct(warm, 50), 3), "warm_p95_ms": round(_pct(warm, 95), 3),
        "warm_mean_ms": round(statistics.mean(warm), 3),
    }


def _list_payload(kind: str, params: dict):
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIRequestFactory, force_authenticate
    from apps.qualidade_operacional.views import AuditadosView, FalhasView
    users = get_user_model().objects
    user = users.filter(is_superuser=True).order_by("pk").first() or users.order_by("pk").first()
    if user is None:
        raise RuntimeError("Nenhum usuario existente para snapshot read-only das listas")
    path = f"/api/v1/qualidade/operacional/{kind}/"
    request = APIRequestFactory().get(path, params)
    force_authenticate(request, user=user)
    view = AuditadosView if kind == "auditados" else FalhasView
    response = view.as_view()(request)
    if response.status_code != 200:
        raise RuntimeError(f"Snapshot {kind} retornou HTTP {response.status_code}")
    return response.data


def _compare(current: dict, baseline_path: Path):
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    differences = []
    known_unstable = []
    for name, expected in baseline["scenarios"].items():
        actual = current["scenarios"].get(name)
        expected_comparison = expected.get("comparison_sha256")
        if expected_comparison is None:
            payload_path = ROOT / expected["payload_file"]
            expected_payload = json.loads(payload_path.read_text(encoding="utf-8"))
            expected_comparison = _sha(
                _canonical(_comparison_payload(name, expected_payload))
            )
        actual_comparison = actual and actual.get("comparison_sha256")
        if not actual or actual_comparison != expected_comparison:
            differences.append({"scenario": name, "before": expected_comparison,
                                "after": actual_comparison})
        elif actual["sha256"] != expected["sha256"]:
            fields = (
                ["responsaveis.results[].quartil", "responsaveis.quartis"]
                if name == "cliente_83_jul"
                else [
                    "ranking.results[].quartil",
                    "ranking.quartis",
                    "facilitator_ranking.results[].quartil",
                    "facilitator_ranking.quartis",
                ]
            )
            known_unstable.append({
                "scenario": name,
                "fields": fields,
                "before_raw": expected["sha256"],
                "after_raw": actual["sha256"],
            })
    for key, expected in baseline["database"]["content"].items():
        actual = current["database"]["content"].get(key)
        if key.startswith(("auditados_by_", "falhas_by_")):
            if actual != expected:
                differences.append({"database": key, "reason": "monthly_counts"})
            continue
        fields = ("count", "id_sum", "min_id", "max_id", "commutative_hash64")
        changed = {field: {"before": expected.get(field), "after": (actual or {}).get(field)}
                   for field in fields if (actual or {}).get(field) != expected.get(field)}
        if changed:
            differences.append({"database": key, "reason": "content", "changed": changed})
    return {
        "baseline": str(baseline_path),
        "ok": not differences,
        "differences": differences,
        "known_preexisting_instability": known_unstable,
    }

def _filter_counts(params: dict):
    from apps.qualidade_operacional.services.analytics import filtered_auditados, filtered_falhas
    return {
        "auditados": filtered_auditados(params).count(),
        "falhas": filtered_falhas(params).count(),
    }


def snapshot(args):
    import django
    django.setup()
    from apps.qualidade_operacional.services.dashboard import build_dashboard
    out_dir = ROOT / "tmp" / "qualidade_performance" / args.run_id / args.phase
    payload_dir = out_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": args.run_id, "phase": args.phase,
                "measured_at_utc": datetime.now(timezone.utc).isoformat(),
                "environment": _environment(), "database": _database_fingerprint(), "scenarios": {}}
    dashboard_scenarios = SCENARIOS.items()
    for name, params in dashboard_scenarios:
        print(f"[{args.phase}] {name} ...", flush=True)
        manifest["scenarios"][name] = _measure(
            name, dict(params), lambda p=dict(params): build_dashboard(p), payload_dir, args.warm_repeats)
        metric = manifest["scenarios"][name]
        print(f"  cold={metric['cold_ms']}ms warm_p95={metric['warm_p95_ms']}ms "
              f"queries={metric['cold_queries']} sha={metric['sha256'][:12]}", flush=True)
    list_scenarios = LIST_SCENARIOS.items()
    for name, (kind, params) in list_scenarios:
        print(f"[{args.phase}] {name} ...", flush=True)
        manifest["scenarios"][name] = _measure(
            name, dict(params), lambda k=kind, p=dict(params): _list_payload(k, p),
            payload_dir, args.warm_repeats)
    if args.compare_to:
        manifest["parity"] = _compare(manifest, Path(args.compare_to).resolve())
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifesto: {manifest_path}")
    if args.compare_to and not manifest["parity"]["ok"]:
        print(json.dumps(manifest["parity"], ensure_ascii=False, indent=2))
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--run-id", required=True)
    snap.add_argument("--phase", choices=("before", "after"), required=True)
    snap.add_argument("--warm-repeats", type=int, default=10)
    snap.add_argument("--compare-to", default="")
    snap.set_defaults(func=snapshot)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
