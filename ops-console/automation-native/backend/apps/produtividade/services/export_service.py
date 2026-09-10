# -*- coding: utf-8 -*-
from __future__ import annotations

from io import BytesIO

import pandas as pd
from django.db.models import QuerySet

from apps.produtividade.services.analytics import serialize_record
from apps.produtividade.services.goal_adjustment import (
    build_logado_lookup_for_qs,
    build_ociosidade_lookup_for_qs,
    build_productivity_discount_lookup_for_qs,
)


def export_records_xlsx(qs: QuerySet) -> bytes:
    ociosidade_lookup = build_ociosidade_lookup_for_qs(qs)
    discount_lookup = build_productivity_discount_lookup_for_qs(qs)
    logado_lookup = build_logado_lookup_for_qs(qs)
    rows = [
        serialize_record(r, ociosidade_lookup, discount_lookup, logado_lookup)
        for r in qs.order_by("-recorded_at")
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "matricula",
                "agent_name",
                "etapa",
                "analysis_seconds",
                "stage_goal",
                "productivity_pct",
                "recorded_at",
                "team",
                "location",
                "journey_shift",
                "leader_name",
            ]
        )
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Produtividade")
    buffer.seek(0)
    return buffer.getvalue()
