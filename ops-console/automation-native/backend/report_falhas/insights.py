# -*- coding: utf-8 -*-

"""Insights textuais e helpers de tendência (Etapa 2)."""

import numpy as np
import pandas as pd


def compute_trend(values):
    vals = [v for v in values if pd.notna(v)]
    if len(vals) < 2:
        return None, None
    x = np.arange(len(values))
    y = np.array(values, dtype=float)
    mask = ~np.isnan(y)
    x = x[mask]
    y = y[mask]
    if len(y) < 2:
        return None, None
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def trend_text(slope, unidade="por período"):
    # Texto de tendência em HTML (compatível com Outlook Classic e Novo Outlook).
    # Evita emojis (⬆️⬇️) que quebram o alinhamento no Outlook Classic.
    if slope is None:
        return "Tendência: insuficiente para análise."
    font = "font-family:Segoe UI Symbol, Segoe UI, Arial;"
    if slope > 0:
        return (
            f"<span style='color:#DC3545;font-weight:bold;{font}'>"
            f"▲ Tendência: crescimento (+{slope:.2f} {unidade})</span>"
        )
    if slope < 0:
        return (
            f"<span style='color:#198754;font-weight:bold;{font}'>"
            f"▼ Tendência: queda ({slope:.2f} {unidade})</span>"
        )
    return "Tendência: estável"


def gerar_insights(
    txt_meses,
    txt_meses_facil,
    txt_diario,
    slope_meses,
    slope_facil,
    slope_diario,
    top3_cenarios,
):
    insights = []
    if not txt_diario:
        txt_diario = "Tendência: insuficiente para análise"
    insights.append(f"Tendência geral (mensal): {txt_meses}")
    insights.append(f"Tendência falhas fáceis: {txt_meses_facil}")
    insights.append(f"Tendência diária: {txt_diario}")

    if slope_meses is None:
        insights.append("Projeção próximo mês (falhas totais): dados insuficientes")
    elif slope_meses > 0:
        insights.append(
            f"Projeção próximo mês (falhas totais): aumento de {abs(slope_meses * 4):.0f} falhas"
        )
    elif slope_meses < 0:
        insights.append(
            f"Projeção próximo mês (falhas totais): redução de {abs(slope_meses * 4):.0f} falhas"
        )
    else:
        insights.append("Projeção próximo mês (falhas totais): estável")

    if slope_facil is None:
        insights.append("Projeção próximo mês (falhas fáceis): dados insuficientes")
    elif slope_facil > 0:
        insights.append(
            f"Projeção próximo mês (falhas fáceis): aumento de {abs(slope_facil * 4):.0f} falhas"
        )
    elif slope_facil < 0:
        insights.append(
            f"Projeção próximo mês (falhas fáceis): redução de {abs(slope_facil * 4):.0f} falhas"
        )
    else:
        insights.append("Projeção próximo mês (falhas fáceis): estável")

    insights.append(
        "<i><b>Obs.:</b> Tendências indicam a direção observada nos dados disponíveis. "
        "As projeções estimam comportamento futuro e só são exibidas quando há "
        "histórico mensal suficiente para análise.</i>"
    )

    if top3_cenarios:
        cenarios_txt = ", ".join([f"{c} ({q})" for c, q in top3_cenarios])
        insights.append(f"Cenários: {cenarios_txt}")

    return insights
