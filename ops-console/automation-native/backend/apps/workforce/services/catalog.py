"""Catálogos de dimensões do ciclo de Headcount."""

from __future__ import annotations

from typing import Any

from apps.workforce.models import AgentHistory, HeadcountCatalogItem

CATALOG_META: dict[str, dict[str, str]] = {
    HeadcountCatalogItem.CATALOG_TEAM: {
        "slug": "time",
        "title": "Time",
        "description": "Opções do campo Time no ciclo de HC.",
    },
    HeadcountCatalogItem.CATALOG_JOB_TITLE: {
        "slug": "cargo",
        "title": "Cargo",
        "description": "Opções do campo Cargo no ciclo de HC.",
    },
    HeadcountCatalogItem.CATALOG_JOB_ACTIVITY: {
        "slug": "atividade",
        "title": "Atividade",
        "description": "Opções do campo Atividade no ciclo de HC.",
    },
    HeadcountCatalogItem.CATALOG_LOCATION: {
        "slug": "local",
        "title": "Local",
        "description": "Opções do campo Local no ciclo de HC.",
    },
    HeadcountCatalogItem.CATALOG_TEAM_SECTOR: {
        "slug": "setor-time",
        "title": "Setor do time",
        "description": "Opções do campo Setor do time no ciclo de HC.",
    },
    HeadcountCatalogItem.CATALOG_JOB_TITLE_SECTOR: {
        "slug": "setor-cargo",
        "title": "Setor do cargo",
        "description": "Opções do campo Setor do cargo no ciclo de HC.",
    },
    HeadcountCatalogItem.CATALOG_JOB_TITLE_ACTIVITY: {
        "slug": "atividade-cargo",
        "title": "Atividade do cargo",
        "description": "Opções do campo Atividade do cargo no ciclo de HC.",
    },
    HeadcountCatalogItem.CATALOG_BAND: {
        "slug": "band",
        "title": "Band",
        "description": "Opções do campo Band no ciclo de HC.",
    },
}

SLUG_TO_CATALOG = {meta["slug"]: key for key, meta in CATALOG_META.items()}

# Campo do payload/ciclo → chave do catálogo
CYCLE_FIELD_TO_CATALOG: dict[str, str] = {
    "team": HeadcountCatalogItem.CATALOG_TEAM,
    "job_title": HeadcountCatalogItem.CATALOG_JOB_TITLE,
    "job_activity": HeadcountCatalogItem.CATALOG_JOB_ACTIVITY,
    "location": HeadcountCatalogItem.CATALOG_LOCATION,
    "team_sector": HeadcountCatalogItem.CATALOG_TEAM_SECTOR,
    "job_title_sector": HeadcountCatalogItem.CATALOG_JOB_TITLE_SECTOR,
    "job_title_activity": HeadcountCatalogItem.CATALOG_JOB_TITLE_ACTIVITY,
    "band": HeadcountCatalogItem.CATALOG_BAND,
}

HISTORY_FIELD_BY_CATALOG = {v: k for k, v in CYCLE_FIELD_TO_CATALOG.items()}


def resolve_catalog(catalog_or_slug: str) -> str | None:
    key = (catalog_or_slug or "").strip()
    if key in CATALOG_META:
        return key
    return SLUG_TO_CATALOG.get(key)


def serialize_catalog_item(item: HeadcountCatalogItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "catalog": item.catalog,
        "value": item.value,
        "label": item.label or item.value,
        "active": item.active,
        "sort_order": item.sort_order,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def seed_catalog_from_history() -> int:
    """Insere valores distintos já usados em AgentHistory (sem duplicar)."""
    created = 0
    for catalog_key, history_field in HISTORY_FIELD_BY_CATALOG.items():
        existing = set(
            HeadcountCatalogItem.objects.filter(catalog=catalog_key).values_list(
                "value", flat=True
            )
        )
        distinct = (
            AgentHistory.objects.exclude(**{f"{history_field}__isnull": True})
            .exclude(**{history_field: ""})
            .values_list(history_field, flat=True)
            .distinct()
        )
        last = (
            HeadcountCatalogItem.objects.filter(catalog=catalog_key)
            .order_by("-sort_order")
            .values_list("sort_order", flat=True)
            .first()
        )
        sort_order = last or 0
        for raw in distinct:
            value = str(raw or "").strip()
            if not value or value in existing:
                continue
            sort_order += 1
            HeadcountCatalogItem.objects.create(
                catalog=catalog_key,
                value=value,
                label=value,
                sort_order=sort_order,
                active=True,
            )
            existing.add(value)
            created += 1
    return created


def active_options_by_catalog() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {key: [] for key in CATALOG_META}
    qs = (
        HeadcountCatalogItem.objects.filter(active=True)
        .order_by("catalog", "sort_order", "value")
        .only("catalog", "value", "label")
    )
    for item in qs:
        out.setdefault(item.catalog, []).append(
            {"value": item.value, "label": item.label or item.value}
        )
    return out


def assert_cycle_catalog_values(cycle_values: dict[str, Any]) -> None:
    """Rejeita texto livre quando o catálogo já tem opções cadastradas."""
    from apps.workforce.services.cycle_change import CycleChangeError

    errors: dict[str, list[str]] = {}
    for field, catalog_key in CYCLE_FIELD_TO_CATALOG.items():
        text = str(cycle_values.get(field) or "").strip()
        if not text:
            continue
        known = HeadcountCatalogItem.objects.filter(catalog=catalog_key)
        if not known.exists():
            continue
        if not known.filter(value=text).exists():
            title = CATALOG_META[catalog_key]["title"]
            errors[field] = [
                f"Valor inválido para {title}. Selecione uma opção do catálogo "
                f"(Configurações → Catálogos de Headcount)."
            ]
    if errors:
        raise CycleChangeError(errors)
