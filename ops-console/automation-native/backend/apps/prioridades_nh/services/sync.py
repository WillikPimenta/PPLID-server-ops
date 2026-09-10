# -*- coding: utf-8 -*-
"""Sync diferencial do JSON do bot → NhPrioridadeFluxo."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.prioridades_nh.models import NhPrioridadeFluxo

_COMPARE_FIELDS = (
    "prk_cliente",
    "nom_cliente",
    "prk_workflow",
    "nom_workflow",
    "nom_nivel_hierarquico",
    "nom_fluxo",
    "prk_modulo",
    "nom_modulo",
    "num_prioridade_fluxo",
    "cod_analise",
    "hierarchical_level_id",
)


def _as_int(value: Any, default: int | None = 0) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def normalize_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza uma linha do JSON BrFlow / bot para campos do modelo."""
    prk_nh = _as_int(
        raw.get("prk_nivel_hierarquico")
        or raw.get("PRK_NIVEL_HIERARQUICO")
        or raw.get("codNivelHierarquico"),
        None,
    )
    prk_fluxo = _as_int(raw.get("prk_fluxo") or raw.get("PRK_FLUXO"), None)
    if prk_nh is None or prk_fluxo is None:
        return None

    prk_modulo = _as_int(raw.get("prk_modulo") or raw.get("PRK_MODULO"), None)
    cod_analise = raw.get("cod_analise")
    if cod_analise is None:
        cod_analise = raw.get("COD_ANALISE")
    if cod_analise is None:
        cod_analise = ""
    else:
        cod_analise = _as_str(cod_analise)

    return {
        "prk_cliente": _as_int(raw.get("prk_cliente") or raw.get("PRK_CLIENTE"), 0) or 0,
        "nom_cliente": _as_str(raw.get("nom_cliente") or raw.get("NOM_CLIENTE")),
        "prk_workflow": _as_int(raw.get("prk_workflow") or raw.get("PRK_WORKFLOW"), 0) or 0,
        "nom_workflow": _as_str(raw.get("nom_workflow") or raw.get("NOM_WORKFLOW")),
        "prk_nivel_hierarquico": prk_nh,
        "nom_nivel_hierarquico": _as_str(
            raw.get("nom_nivel_hierarquico") or raw.get("NOM_NIVEL_HIERARQUICO")
        ),
        "prk_fluxo": prk_fluxo,
        "nom_fluxo": _as_str(raw.get("nom_fluxo") or raw.get("NOM_FLUXO")),
        "prk_modulo": prk_modulo,
        "nom_modulo": _as_str(raw.get("nom_modulo") or raw.get("NOM_MODULO")),
        "num_prioridade_fluxo": _as_int(
            raw.get("num_prioridade_fluxo") or raw.get("NUM_PRIORIDADE_FLUXO"), 0
        )
        or 0,
        "cod_analise": cod_analise,
        "hierarchical_level_id": _as_uuid(
            raw.get("hierarchical_level_id") or raw.get("hierarchicalLevelId")
        ),
    }


def load_rows_from_path(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("data") or data.get("items") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    out: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = normalize_row(raw)
        if row:
            out.append(row)
    return out


def _row_equal(existing: NhPrioridadeFluxo, incoming: dict[str, Any]) -> bool:
    for field in _COMPARE_FIELDS:
        if getattr(existing, field) != incoming.get(field):
            return False
    return True


def sync_prioridades_from_rows(
    rows: list[dict[str, Any]],
    *,
    now=None,
) -> dict[str, int]:
    """
    Aplica sync diferencial.
    Retorna contadores: inserted, updated, unchanged, deleted.
    """
    now = now or timezone.now()
    if isinstance(now, str):
        parsed = parse_datetime(now)
        now = parsed or timezone.now()

    by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        key = (row["prk_nivel_hierarquico"], row["prk_fluxo"])
        by_key[key] = row

    inserted = updated = unchanged = 0
    deleted = 0

    with transaction.atomic():
        existing = {
            (obj.prk_nivel_hierarquico, obj.prk_fluxo): obj
            for obj in NhPrioridadeFluxo.objects.all()
        }
        incoming_keys = set(by_key.keys())
        existing_keys = set(existing.keys())

        to_delete_pks = [existing[k].pk for k in (existing_keys - incoming_keys)]
        if to_delete_pks:
            deleted, _ = NhPrioridadeFluxo.objects.filter(pk__in=to_delete_pks).delete()

        to_create: list[NhPrioridadeFluxo] = []
        for key, row in by_key.items():
            if key not in existing:
                to_create.append(NhPrioridadeFluxo(**row, synced_at=now))
                inserted += 1
                continue
            obj = existing[key]
            if _row_equal(obj, row):
                unchanged += 1
                continue
            for field in _COMPARE_FIELDS:
                setattr(obj, field, row[field])
            obj.synced_at = now
            obj.save()
            updated += 1

        if to_create:
            NhPrioridadeFluxo.objects.bulk_create(to_create, batch_size=500)

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "deleted": int(deleted),
    }


def sync_prioridades_from_path(path: str | Path, *, now=None) -> dict[str, int]:
    return sync_prioridades_from_rows(load_rows_from_path(path), now=now)
