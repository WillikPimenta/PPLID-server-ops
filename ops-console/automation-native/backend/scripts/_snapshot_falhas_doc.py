# -*- coding: utf-8 -*-
"""One-off snapshot for documentation/revisao-falhas-criticas-portal.md"""
import json
from datetime import date

from django.contrib.auth import get_user_model

from apps.falhas_criticas.models import (
    Contestation,
    Failure,
    FalhasAgent,
    Support,
    Training,
)
from apps.falhas_criticas.services.analytics import (  # noqa: E402
    build_charts_payload,
    build_executive_payload,
)
from apps.falhas_criticas.services.compare_analytics import build_comparativo_bsb_sc  # noqa: E402
from apps.falhas_criticas.services.data_bounds import get_portal_data_bounds  # noqa: E402
from apps.falhas_criticas.services.dataframes import (  # noqa: E402
    apply_support_filters,
    load_period_frames,
    supports_qs_to_legacy_df,
)
from apps.falhas_criticas.services.support_analytics import build_support_full_payload  # noqa: E402
from apps.falhas_criticas.services.training_analytics import (  # noqa: E402
    _training_qs_filtered,
    build_training_full_payload,
)

User = get_user_model()
user = User.objects.filter(is_superuser=True).first() or User.objects.first()
today = date.today()
start = today.replace(day=1)
params = {
    "localidade": "Geral",
    "start_date": start.isoformat(),
    "end_date": today.isoformat(),
    "oficial": True,
}

bounds = get_portal_data_bounds()
counts = {
    "failures": Failure.objects.count(),
    "support": Support.objects.count(),
    "training": Training.objects.count(),
    "contestation": Contestation.objects.count(),
    "agents": FalhasAgent.objects.count(),
}
loc_fail = sorted(set(Failure.objects.values_list("localidade", flat=True)))

df_all, df_cur, df_prev, _, _ = load_period_frames(user, params)
exec_p = build_executive_payload(df_all, df_cur, df_prev, params)
charts = build_charts_payload(df_cur, df_all, params)
comp = build_comparativo_bsb_sc(user, params)

qs_sup = apply_support_filters(Support.objects.all(), params)
df_sup = supports_qs_to_legacy_df(qs_sup)
sup = build_support_full_payload(df_sup, params)
train = build_training_full_payload(_training_qs_filtered(params), params)

out = {
    "captured_at": today.isoformat(),
    "filters": params,
    "bounds": bounds,
    "counts": counts,
    "localidades_failure": loc_fail,
    "period_rows": {
        "cur": len(df_cur),
        "prev": len(df_prev),
        "all": len(df_all),
    },
    "resumo_30s": exec_p.get("resumo_30s"),
    "leitura_executiva": {
        k: exec_p.get("leitura_executiva", {}).get(k)
        for k in (
            "variacao_perc",
            "reinc_total",
            "reinc_pct",
            "novos_mes_count",
            "novos_pct",
        )
    },
    "charts_keys": list(charts.keys()),
    "evolucao_mensal_sample": charts.get("evolucao_mensal", [])[:4],
    "pressao_turno_sample": charts.get("pressao_turno", [])[:4],
    "comparativo_meta": comp.get("meta"),
    "comparativo_placar": comp.get("placar"),
    "comparativo_bsb": comp.get("brasilia", {}).get("falhas"),
    "comparativo_sc": comp.get("sao_carlos", {}).get("falhas"),
    "support_summary": {
        "total": sup.get("total"),
        "nc_oficial_pct": sup.get("nc_oficial_pct"),
        "regra3_count": sup.get("regra3_count"),
        "regra3_pct": sup.get("regra3_pct"),
    },
    "training_kpis": train.get("kpis"),
}

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
