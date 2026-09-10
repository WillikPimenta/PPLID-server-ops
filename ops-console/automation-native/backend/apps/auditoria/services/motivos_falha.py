from __future__ import annotations

import json
from pathlib import Path

from apps.auditoria.models import AuditoriaMotivoFalha

SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "motivos_falha_seed.json"


def seed_motivos_falha_defaults() -> int:
    """Garante motivos do seed no banco (cria apenas os que ainda não existem)."""
    if not SEED_FILE.is_file():
        return 0
    if AuditoriaMotivoFalha.objects.exists():
        return 0

    rows = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    existing: set[str] = set()
    last_sort = (
        AuditoriaMotivoFalha.objects.order_by("-sort_order")
        .values_list("sort_order", flat=True)
        .first()
    )
    next_sort = (last_sort if last_sort is not None else -1) + 1

    items: list[AuditoriaMotivoFalha] = []
    for row in rows:
        motivo = (row.get("motivo") or "").strip()
        if not motivo or motivo.casefold() in existing:
            continue
        criticidade = (row.get("criticidade") or "").strip()
        segmentos = (row.get("segmentos") or "").strip()
        subsegmento = (row.get("subsegmento") or "").strip()
        if not criticidade or not segmentos or not subsegmento:
            continue
        items.append(
            AuditoriaMotivoFalha(
                motivo=motivo,
                criticidade=criticidade,
                segmentos=segmentos,
                subsegmento=subsegmento,
                sort_order=next_sort,
                active=True,
            )
        )
        existing.add(motivo.casefold())
        next_sort += 1

    if items:
        AuditoriaMotivoFalha.objects.bulk_create(items)
    return len(items)


def get_motivo_falha_values(*, active_only: bool = True) -> list[str]:
    seed_motivos_falha_defaults()
    qs = AuditoriaMotivoFalha.objects.all()
    if active_only:
        qs = qs.filter(active=True)
    return list(qs.order_by("sort_order", "motivo").values_list("motivo", flat=True))
