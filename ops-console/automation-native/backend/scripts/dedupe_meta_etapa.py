"""Remove MetaEtapa duplicadas (mesma etapa, vigência, meta e serviço).

Uso:
  python backend/scripts/dedupe_meta_etapa.py           # dry-run
  python backend/scripts/dedupe_meta_etapa.py --apply     # executa

Mantém o menor id de cada grupo.
"""

from __future__ import annotations

import argparse
import os
import sys

import django

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.db.models import Count, Min

from apps.dimensoes_processos.models import MetaEtapa


def dedupe_meta_etapa(*, apply: bool) -> dict:
    groups = (
        MetaEtapa.objects.values("etapa_id", "data_inicio", "data_fim", "meta_dia", "servico_id")
        .annotate(count=Count("id"), keep_id=Min("id"))
        .filter(count__gt=1)
        .order_by("etapa_id")
    )

    to_delete: list[int] = []
    for group in groups:
        qs = MetaEtapa.objects.filter(
            etapa_id=group["etapa_id"],
            data_inicio=group["data_inicio"],
            data_fim=group["data_fim"],
            meta_dia=group["meta_dia"],
            servico_id=group["servico_id"],
        ).exclude(pk=group["keep_id"])
        to_delete.extend(qs.values_list("pk", flat=True))

    stats = {
        "duplicate_groups": groups.count(),
        "rows_to_delete": len(to_delete),
        "applied": apply,
    }

    if apply and to_delete:
        with transaction.atomic():
            deleted, _ = MetaEtapa.objects.filter(pk__in=to_delete).delete()
        stats["deleted"] = deleted
    else:
        stats["deleted"] = 0

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplica MetaEtapa idênticas.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Executa a exclusão (sem flag = apenas simulação).",
    )
    args = parser.parse_args()
    stats = dedupe_meta_etapa(apply=args.apply)

    mode = "APLICADO" if args.apply else "DRY-RUN"
    print(f"[{mode}] grupos duplicados: {stats['duplicate_groups']}")
    print(f"[{mode}] linhas a remover: {stats['rows_to_delete']}")
    if args.apply:
        print(f"[{mode}] linhas removidas: {stats['deleted']}")
    else:
        print("Rode com --apply para executar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
