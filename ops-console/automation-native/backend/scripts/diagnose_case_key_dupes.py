#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Diagnóstico funcional de deduplicação case_key (Qualidade EO)."""
from __future__ import annotations

import os
import sys
from datetime import date

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from apps.psa.services.qualidade_case_key_dupes import check_qualidade_case_key_dupes  # noqa: E402
from apps.qualidade_operacional.models import QualidadeFalha  # noqa: E402
from apps.qualidade_operacional.services.case_key import find_canonical_falha  # noqa: E402
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE  # noqa: E402


def main() -> int:
    payload = check_qualidade_case_key_dupes(sample_limit=10)
    print("=== Portal Ops case_key dupes ===")
    print(f"status={payload['status']} groups={payload['duplicate_groups']} rows={payload['duplicate_rows']}")
    if payload["sample"]:
        sample = payload["sample"][0]
        print(f"sample kept_id={sample['kept_id']} removed={sample['removed_ids']}")

    dup_count = (
        QualidadeFalha.objects.exclude(case_key="")
        .values("case_key")
        .distinct()
        .count()
    )
    print(f"Distinct case_keys in DB: {dup_count}")

    # Spot-check canonical resolver on first duplicate sample
    if payload["sample"]:
        key = payload["sample"][0]["case_key"]
        canonical = find_canonical_falha(case_key=key)
        print(f"Canonical for {key}: id={canonical.pk if canonical else None} data={canonical.data if canonical else None}")

    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
