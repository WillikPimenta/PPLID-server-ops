# report_falhas/charts.py
# -*- coding: utf-8 -*-

"""
Gráficos do Relatório de Falhas Críticas (base64 PNG).

Regras:
- Gera imagens inline (base64), compatíveis com Outlook
- Não depende de HTML
- Não aplica regra de negócio (só visualização)
"""

import base64
from io import BytesIO
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from report_falhas.io.data_loader import safe_str, normalize_text


# ============================================================
# Helpers base
# ============================================================

def _clean_axes(ax):
    ax.grid(False)
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#CDD3DA")
    ax.spines["bottom"].set_color("#CDD3DA")


def fig_to_base64(fig) -> str:
    buf = BytesIO()
    fig.tight_layout(pad=0.6)
    fig.savefig(
        buf,
        format="png",
        dpi=150,
        bbox_inches="tight",
        pad_inches=0.15
    )
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def compute_trend(values):
    """
    Calcula tendência linear simples (slope).
    Retorna (slope, intercept) ou (None, None).
    """
    vals = [v for v in values if pd.notna(v)]
    if len(vals) < 2:
        return None, None

    x = np.arange(len(values))
    y = np.array(values, dtype=float)

    mask = ~np.isnan(y)
    x, y = x[mask], y[mask]

    if len(y) < 2:
        return None, None

    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def trend_text(slope, unidade="período") -> str:
    if slope is None:
        return "Tendência: insuficiente para análise."

    if slope > 0:
        return f"▲ Tendência: crescimento (+{abs(slope):.2f} {unidade})"
    if slope < 0:
        return f"▼ Tendência: queda ({-abs(slope):.2f} {unidade})"

    return "→ Tendência: estável"


# ============================================================
# Gráfico de barras com tendência
# ============================================================

def chart_barras_base64(pares_label_val, titulo: str):
    """
    Gera gráfico de barras horizontal/vertical com linha de tendência.
    Retorna: (base64_png, texto_tendencia, slope)
    """
    if not pares_label_val:
        return None, "Sem dados para análise.", None

    labels = [safe_str(l) for l, _ in pares_label_val]
    values = []

    for _, v in pares_label_val:
        try:
            values.append(float(v))
        except Exception:
            values.append(float("nan"))

    slope, intercept = compute_trend(values)

    fig, ax = plt.subplots(figsize=(10.0, 3.6))

    bars = ax.bar(range(len(values)), values, color="#1D4F91", zorder=2)

    # Linha de tendência
    if slope is not None:
        x = np.arange(len(values))
        y_pred = slope * x + intercept
        ax.plot(x, y_pred, color="orange", linestyle="--", label="Tendência", zorder=3)
        ax.legend()

    total = float(sum(v for v in values if not np.isnan(v)))
    max_val = max([v for v in values if not np.isnan(v)] or [0])

    for i, bar in enumerate(bars):
        v = values[i]
        if np.isnan(v):
            continue

        pct = (v / total * 100.0) if total else 0.0
        x = bar.get_x() + bar.get_width() / 2.0
        y = bar.get_height() * 0.6
        if bar.get_height() < (0.12 * (max_val or 1)):
            y = bar.get_height() * 0.75

        ax.text(
            x, y,
            f"{int(round(v))} ({pct:.1f}%)",
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="black",
            zorder=4,
            bbox=dict(
                facecolor="white",
                alpha=0.55,
                boxstyle="round,pad=0.15",
                edgecolor="none",
            ),
        )

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=0)
    ax.set_title(titulo, fontsize=11)
    ax.set_ylabel("Qtde")
    ax.set_ylim(0, (max_val * 1.12) if max_val else ax.get_ylim()[1])

    _clean_axes(ax)

    fig.subplots_adjust(bottom=0.2)

    return (
        fig_to_base64(fig),
        trend_text(slope),
        slope,
    )


# ============================================================
# Gráfico de linha (falhas por dia)
# ============================================================

def chart_linha_base64(pares_label_val):
    """
    Gráfico de linha temporal (ex.: falhas por dia).
    Retorna: (base64_png, texto_tendencia, slope)
    """
    if not pares_label_val:
        return None, "Sem dados para análise.", None

    labels = [safe_str(l) for l, _ in pares_label_val]
    values = []

    for _, v in pares_label_val:
        try:
            values.append(float(v))
        except Exception:
            values.append(float("nan"))

    slope, intercept = compute_trend(values)

    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(11.0, 4.0))

    ax.plot(
        x,
        values,
        marker="o",
        color="#6D2077",
        linewidth=2,
        markersize=5,
    )

    if slope is not None:
        y_pred = slope * x + intercept
        ax.plot(x, y_pred, color="orange", linestyle="--", label="Tendência")
        ax.legend()

    ax.set_title("Falhas por dia – mês atual", fontsize=11)
    ax.set_ylabel("Qtde")
    ax.margins(x=0.01)

    # Limita quantidade de rótulos no eixo X
    from math import ceil
    max_labels = 15
    step = max(1, ceil(len(labels) / max_labels))
    xticks = list(range(0, len(labels), step))

    total = sum(v for v in values if not np.isnan(v))
    xticklabels = [
        f"{labels[i]}\nQtd: {values[i]} ({(values[i]/total*100 if total else 0):.1f}%)"
        for i in xticks
    ]

    ax.set_xticks(xticks)
    ax.set_xticklabels(xticklabels, rotation=0, fontsize=8)

    _clean_axes(ax)
    fig.subplots_adjust(bottom=0.35)

    return (
        fig_to_base64(fig),
        trend_text(slope),
        slope,
    )


# ============================================================
# Pacote de gráficos (usado pelo pipeline)
# ============================================================

def build_charts_bundle(df_cur: pd.DataFrame, context) -> dict:
    """
    Centraliza a geração de gráficos para o pipeline.

    Espera que o context já traga:
    - pares_mes (mensal)
    - pares_diario (diário)
    - pares_facil (facil)
    """

    charts = {}

    # Mensal
    if getattr(context, "pares_mes", None):
        b64, txt, _ = chart_barras_base64(
            context.pares_mes,
            "Falhas por mês (MTD)"
        )
        if b64:
            charts["Falhas por mês"] = b64

    # Diário
    if getattr(context, "pares_diario", None):
        b64, txt, _ = chart_linha_base64(context.pares_diario)
        if b64:
            charts["Falhas por dia (mês atual)"] = b64

    # Falhas fáceis (se existir)
    if getattr(context, "pares_facil", None):
        b64, txt, _ = chart_barras_base64(
            context.pares_facil,
            "Falhas fáceis (tendência)"
        )
        if b64:
            charts["Falhas fáceis"] = b64

    return charts