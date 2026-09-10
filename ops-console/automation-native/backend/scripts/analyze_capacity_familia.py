"""Análise QA de famílias Capacity — executar via manage.py shell."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from apps.dimensoes_processos.services.capacity import calculate_daily_capacity
from apps.dimensoes_processos.services.capacity_familia import (
    extract_familia,
    extract_familia_catalog,
    familias_differ,
    has_familia_separator,
    is_automatic_stage_name,
    is_single_word_familia,
)


def run_analysis(on_date: date | None = None) -> dict:
    on_date = on_date or date(2026, 8, 20)
    cap = calculate_daily_capacity(on_date)

    familias: dict[str, dict] = defaultdict(
        lambda: {
            "stage_count": 0,
            "agents_sum": 0,
            "sample_names": [],
            "stage_ids": set(),
        }
    )
    no_separator: list[str] = []
    single_word: set[str] = set()
    automatic_stages: list[dict] = []
    catalog_diffs: list[dict] = []
    split_candidates: dict[str, set[str]] = defaultdict(set)

    for row in cap["results"]:
        nome = row["etapa_nome"]
        fam = extract_familia(nome)
        cat = extract_familia_catalog(nome)

        bucket = familias[fam]
        if row["id_etapa"] not in bucket["stage_ids"]:
            bucket["stage_ids"].add(row["id_etapa"])
            bucket["stage_count"] += 1
            if len(bucket["sample_names"]) < 3:
                bucket["sample_names"].append(nome)

        agents = row.get("agentes_necessarios") or 0
        bucket["agents_sum"] += agents

        if not has_familia_separator(nome):
            no_separator.append(nome)
        if is_single_word_familia(fam):
            single_word.add(fam)
        if is_automatic_stage_name(nome) or row["status"] == "automatica":
            automatic_stages.append(
                {
                    "nome": nome,
                    "familia_v1": fam,
                    "familia_catalog": cat,
                    "status": row["status"],
                    "agents": agents,
                }
            )
        if familias_differ(nome):
            catalog_diffs.append(
                {
                    "nome": nome,
                    "v1": fam,
                    "catalog": cat,
                    "agents": agents,
                }
            )

        split_candidates[fam].add(cat)

    logical_splits = [
        {
            "familia_v1": fam,
            "catalog_familias": sorted(cats - {fam}),
            "stage_count": data["stage_count"],
            "agents_sum": data["agents_sum"],
        }
        for fam, cats in split_candidates.items()
        if len(cats) > 1
    ]
    logical_splits.sort(key=lambda x: -x["agents_sum"])

    all_familias = []
    for fam, data in familias.items():
        all_familias.append(
            {
                "familia": fam,
                "stage_count": data["stage_count"],
                "agents_sum": data["agents_sum"],
                "sample_names": data["sample_names"],
            }
        )
    all_familias.sort(key=lambda x: (-x["agents_sum"], x["familia"].casefold()))

    top20 = all_familias[:20]

    return {
        "on_date": on_date.isoformat(),
        "summary": cap["summary"],
        "blockers": cap["blockers"],
        "familia_count": len(all_familias),
        "all_familias": all_familias,
        "top20_by_agents": top20,
        "no_separator_count": len(no_separator),
        "no_separator_samples": sorted(set(no_separator))[:30],
        "single_word_familias": sorted(single_word),
        "automatic_stage_count": len(automatic_stages),
        "automatic_stages_sample": automatic_stages[:25],
        "catalog_diff_count": len(catalog_diffs),
        "catalog_diffs": catalog_diffs[:40],
        "logical_split_families": logical_splits[:30],
    }


if __name__ == "__main__":
    print(json.dumps(run_analysis(), ensure_ascii=False, indent=2, default=str))
