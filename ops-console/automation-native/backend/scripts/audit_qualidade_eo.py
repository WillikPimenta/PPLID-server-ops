#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Auditoria somente-leitura do Indicador EO (fonte/banco × fórmula independente).

Uso (a partir de backend/):
  python scripts/audit_qualidade_eo.py --start 2026-07-01 --end 2026-07-31
  python scripts/audit_qualidade_eo.py --start 2026-07-01 --end 2026-07-31 --date-axis analise --grain protocolo
  python scripts/audit_qualidade_eo.py --csv documentation/qualidade-eo-reconciliacao.csv

Exit code 1 se |API − oráculo| > tolerância em auditados/falhas (default 0).
Não altera o banco.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def eo_raw(auditados: int, falhas: int) -> float | None:
    if auditados <= 0:
        return None
    return (1.0 - (falhas / auditados)) * 100.0


def eo_round(auditados: int, falhas: int) -> float | None:
    raw = eo_raw(auditados, falhas)
    if raw is None:
        return None
    return round(max(0.0, min(100.0, raw)), 1)


def oracle_counts(model, field: str, start: date, end: date, grain: str) -> int:
    qs = model.objects.filter(**{f"{field}__gte": start, f"{field}__lte": end})
    if grain == "protocolo":
        return qs.exclude(protocolo="").values("protocolo").distinct().count()
    return qs.count()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--date-axis", choices=("auditoria", "analise"), default="auditoria")
    parser.add_argument("--grain", choices=("etapa", "protocolo"), default="etapa")
    parser.add_argument("--tolerance", type=int, default=0)
    parser.add_argument("--csv", type=str, default="")
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    import django

    django.setup()

    from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
    from apps.qualidade_operacional.services.analytics import build_kpis, shift_period

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    date_field = "data" if args.date_axis == "auditoria" else "data_analise"

    aud_o = oracle_counts(QualidadeAuditado, date_field, start, end, args.grain)
    fal_o = oracle_counts(QualidadeFalha, date_field, start, end, args.grain)
    eo_o_raw = eo_raw(aud_o, fal_o)
    eo_o = eo_round(aud_o, fal_o)

    params = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "date_axis": args.date_axis,
        "grain": args.grain,
    }
    kpis = build_kpis(params)
    prev = kpis.get("previous") or {}
    p_start, p_end = shift_period(start, end)

    aud_prev_o = oracle_counts(QualidadeAuditado, date_field, p_start, p_end, args.grain)
    fal_prev_o = oracle_counts(QualidadeFalha, date_field, p_start, p_end, args.grain)
    eo_prev_o = eo_round(aud_prev_o, fal_prev_o)
    delta_o = (
        round(eo_o - eo_prev_o, 1)
        if eo_o is not None and eo_prev_o is not None
        else None
    )

    row = {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "date_axis": args.date_axis,
        "grain": args.grain,
        "oracle_auditados": aud_o,
        "oracle_falhas": fal_o,
        "oracle_eo_raw": eo_o_raw,
        "oracle_eo_pct": eo_o,
        "api_auditados": kpis.get("auditados"),
        "api_falhas": kpis.get("falhas"),
        "api_eo_pct": kpis.get("eo_pct"),
        "diff_auditados": int(kpis.get("auditados") or 0) - aud_o,
        "diff_falhas": int(kpis.get("falhas") or 0) - fal_o,
        "prev_start": p_start.isoformat(),
        "prev_end": p_end.isoformat(),
        "oracle_prev_auditados": aud_prev_o,
        "oracle_prev_falhas": fal_prev_o,
        "oracle_prev_eo_pct": eo_prev_o,
        "oracle_delta_pp": delta_o,
        "api_prev_start": prev.get("start_date"),
        "api_prev_end": prev.get("end_date"),
        "api_prev_auditados": prev.get("auditados"),
        "api_prev_falhas": prev.get("falhas"),
        "api_prev_eo_pct": prev.get("eo_pct"),
        "api_delta_pp": prev.get("delta_pp"),
        "status": (
            "OK"
            if abs(int(kpis.get("auditados") or 0) - aud_o) <= args.tolerance
            and abs(int(kpis.get("falhas") or 0) - fal_o) <= args.tolerance
            else "DIFF"
        ),
    }

    print(json.dumps(row, ensure_ascii=False, indent=2, default=str))

    if args.csv:
        out = Path(args.csv)
        if not out.is_absolute():
            # Prefer CWD-relative; fall back to repo root.
            cwd_candidate = Path.cwd() / out
            out = cwd_candidate.resolve() if cwd_candidate.parent.exists() else (REPO / out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        write_header = not out.exists()
        with out.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        print(f"Appended CSV row → {out}", file=sys.stderr)

    if args.json_out:
        outj = Path(args.json_out)
        if not outj.is_absolute():
            outj = REPO / outj
        outj.parent.mkdir(parents=True, exist_ok=True)
        outj.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return 0 if row["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
