# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from django.db.models import Q

from apps.suporte_claro.models import SuporteClaroRegistro, SuporteClaroVinculoIncidente

TIPO_INCIDENTE_LABELS = dict(SuporteClaroRegistro.TIPO_INCIDENTE_CHOICES)
CATEGORIA_LABELS = dict(SuporteClaroRegistro.CATEGORIA_CHOICES)
MAX_VINCULOS_REGISTRO = 30


def ordered_pair(a_id: int, b_id: int) -> tuple[int, int]:
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def parse_vinculos_ids(data) -> tuple[list[int] | None, str | None]:
    """Return None if key absent; empty list clears; list of ints otherwise."""
    if "vinculos_ids" not in data and "vinculosIds" not in data:
        return None, None
    raw = data.get("vinculos_ids")
    if raw is None and "vinculosIds" in data:
        raw = data.get("vinculosIds")
    if raw is None or raw == "":
        return [], None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return [], None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None, "vinculos_ids inválido (JSON)."
    if not isinstance(raw, (list, tuple)):
        return None, "vinculos_ids deve ser uma lista de ids."
    ids: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            return None, "vinculos_ids deve conter apenas ids numéricos."
        if value <= 0:
            return None, "vinculos_ids inválido."
        if value in seen:
            continue
        seen.add(value)
        ids.append(value)
    if len(ids) > MAX_VINCULOS_REGISTRO:
        return None, f"Máximo de {MAX_VINCULOS_REGISTRO} registros vinculados."
    return ids, None


def serialize_vinculo_resumo(registro: SuporteClaroRegistro) -> dict:
    return {
        "id": registro.id,
        "titulo": registro.titulo or "",
        "display_titulo": registro.titulo or registro.protocolo or f"#{registro.id}",
        "status": registro.status,
        "status_label": dict(SuporteClaroRegistro.STATUS_CHOICES).get(
            registro.status, registro.status
        ),
        "categoria": registro.categoria,
        "tipo_incidente": registro.tipo_incidente or "",
        "tipo_incidente_label": TIPO_INCIDENTE_LABELS.get(
            registro.tipo_incidente, registro.tipo_incidente or ""
        ),
    }


def list_vinculos_for(registro: SuporteClaroRegistro) -> list[dict]:
    """Prefer prefetched vinculos_from / vinculos_to when present (list endpoints)."""
    cache = getattr(registro, "_prefetched_objects_cache", None) or {}
    if "vinculos_from" in cache and "vinculos_to" in cache:
        links = list(registro.vinculos_from.all()) + list(registro.vinculos_to.all())
        links.sort(key=lambda link: (link.created_at, link.id), reverse=True)
    else:
        links = list(
            SuporteClaroVinculoIncidente.objects.filter(
                Q(from_registro=registro) | Q(to_registro=registro)
            )
            .select_related("from_registro", "to_registro")
            .order_by("-created_at", "id")
        )
    result: list[dict] = []
    for link in links:
        other = link.to_registro if link.from_registro_id == registro.id else link.from_registro
        result.append(serialize_vinculo_resumo(other))
    return result


def set_vinculos(
    registro: SuporteClaroRegistro,
    target_ids: list[int],
    *,
    user=None,
) -> str | None:
    """Replace all vinculos for registro. Returns error message or None."""
    if registro.id in target_ids:
        return "Não é possível vincular um registro a si mesmo."

    if len(target_ids) > MAX_VINCULOS_REGISTRO:
        return f"Máximo de {MAX_VINCULOS_REGISTRO} registros vinculados."

    targets = list(
        SuporteClaroRegistro.objects.filter(id__in=target_ids).only(
            "id", "categoria", "titulo", "protocolo", "status", "tipo_incidente"
        )
    )
    found = {r.id for r in targets}
    missing = [i for i in target_ids if i not in found]
    if missing:
        return f"Registro(s) não encontrado(s): {', '.join(str(i) for i in missing)}."

    different_categories = [r.id for r in targets if r.categoria != registro.categoria]
    if different_categories:
        return (
            "Só é possível vincular registros do mesmo tipo "
            f"(ids inválidos: {', '.join(str(i) for i in different_categories)})."
        )

    desired_pairs = {ordered_pair(registro.id, tid) for tid in target_ids}
    existing = list(
        SuporteClaroVinculoIncidente.objects.filter(
            Q(from_registro=registro) | Q(to_registro=registro)
        )
    )
    existing_pairs = {(link.from_registro_id, link.to_registro_id) for link in existing}

    for link in existing:
        pair = (link.from_registro_id, link.to_registro_id)
        if pair not in desired_pairs:
            link.delete()

    for a_id, b_id in desired_pairs - existing_pairs:
        SuporteClaroVinculoIncidente.objects.create(
            from_registro_id=a_id,
            to_registro_id=b_id,
            created_by=user,
        )
    _clear_vinculos_prefetch(registro)
    return None


def _clear_vinculos_prefetch(registro: SuporteClaroRegistro) -> None:
    cache = getattr(registro, "_prefetched_objects_cache", None)
    if not cache:
        return
    cache.pop("vinculos_from", None)
    cache.pop("vinculos_to", None)
