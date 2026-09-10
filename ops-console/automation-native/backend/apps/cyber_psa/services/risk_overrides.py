# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractBaseUser

from apps.cyber_psa.models import CyberRiskOverride


def load_overrides_map() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in CyberRiskOverride.objects.select_related("updated_by").all():
        result[row.risk_id] = {
            "status": row.status,
            "note": row.note,
            "updated_by": getattr(row.updated_by, "username", None) if row.updated_by else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    return result


def apply_overrides_to_risks(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overrides = load_overrides_map()
    merged: list[dict[str, Any]] = []
    for risk in risks:
        row = dict(risk)
        override = overrides.get(str(row.get("id", "")))
        if override:
            row["status"] = override["status"]
            if override.get("note"):
                row["override_note"] = override["note"]
            row["override_updated_by"] = override.get("updated_by")
            row["override_updated_at"] = override.get("updated_at")
        merged.append(row)
    return merged


def upsert_override(
    risk_id: str,
    *,
    status: str,
    note: str,
    user: AbstractBaseUser,
) -> CyberRiskOverride:
    obj, _ = CyberRiskOverride.objects.update_or_create(
        risk_id=risk_id,
        defaults={"status": status, "note": note, "updated_by": user},
    )
    return obj
