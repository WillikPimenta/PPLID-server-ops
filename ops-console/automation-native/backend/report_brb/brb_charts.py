# -*- coding: utf-8 -*-
"""Gráficos matplotlib → base64 para HTML (seguro em e-mail)."""
from __future__ import annotations

import base64
import io
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from report_brb.config_brb import CLIENT

_PRIMARY = CLIENT["cores"]["primary"]
_DARK = CLIENT["cores"]["dark"]
_GREEN = CLIENT["cores"]["green"]
_RED = CLIENT["cores"]["red"]
_GRAY = CLIENT["cores"]["gray"]
_BORDER = CLIENT["cores"]["border"]

_PALETTE = [
    _PRIMARY,
    "#2563eb",
    "#3b82f6",
    "#60a5fa",
    "#0ea5e9",
    "#64748b",
    "#94a3b8",
    "#cbd5e1",
]

_DPI = 144


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial", "Helvetica"],
            "axes.facecolor": "#fafbfc",
            "figure.facecolor": "#ffffff",
            "axes.edgecolor": _BORDER,
            "axes.labelcolor": "#475569",
            "xtick.color": "#64748b",
            "ytick.color": "#334155",
            "text.color": _DARK,
            "axes.titlesize": 13,
            "axes.titleweight": "600",
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 10,
        }
    )


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=_DPI,
        bbox_inches="tight",
        facecolor=fig.get_facecolor(),
        edgecolor="none",
        pad_inches=0.35,
    )
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _trunc(s: str, n: int = 36) -> str:
    s = str(s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _bar_colors(n: int, base: str | None = None) -> list[str]:
    if base and n == 1:
        return [base]
    if n <= len(_PALETTE):
        return _PALETTE[:n]
    return [_PALETTE[i % len(_PALETTE)] for i in range(n)]


def _style_axes(ax, *, grid_axis: str = "x") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_BORDER)
    ax.spines["bottom"].set_color(_BORDER)
    ax.grid(axis=grid_axis, linestyle="-", linewidth=0.6, alpha=0.35, color="#cbd5e1")
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=6)


def chart_donut(
    labels: Sequence[str],
    values: Sequence[int],
    title: str,
    colors: Sequence[str] | None = None,
    center_label: str | None = None,
) -> str | None:
    if not values or sum(values) == 0:
        return None
    _apply_style()
    cols = list(colors or [_RED, _GREEN, _PRIMARY, "#94a3b8"])[: len(values)]
    total = sum(values)
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    fig.patch.set_facecolor("#ffffff")
    wedges, _, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
        startangle=90,
        colors=cols,
        wedgeprops=dict(width=0.52, edgecolor="#ffffff", linewidth=2.5),
        pctdistance=0.78,
        textprops={"fontsize": 10, "fontweight": "600", "color": "#ffffff"},
    )
    for at in autotexts:
        at.set_fontsize(10)
        at.set_fontweight("700")
    centre = center_label or f"{total}\navaliações"
    ax.text(
        0,
        0,
        centre,
        ha="center",
        va="center",
        fontsize=11,
        fontweight="600",
        color=_DARK,
        linespacing=1.35,
    )
    legend = ax.legend(
        wedges,
        [f"{lab} ({val})" for lab, val in zip(labels, values)],
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        frameon=False,
        fontsize=10,
        labelcolor="#334155",
    )
    for t in legend.get_texts():
        t.set_fontsize(10)
    ax.set_title(title, pad=14, color=_DARK)
    ax.set_aspect("equal")
    fig.subplots_adjust(left=0.02, right=0.72)
    return _fig_to_b64(fig)


def chart_barh(
    pairs: Sequence[tuple],
    title: str,
    color: str | Sequence[str] = _PRIMARY,
    xlabel: str = "Quantidade",
    show_pct: bool = False,
) -> str | None:
    if not pairs:
        return None
    _apply_style()
    pairs = list(pairs)[:12]
    raw_labels = [str(p[0]) for p in pairs][::-1]
    labels = [_trunc(l) for l in raw_labels]
    values = [int(p[1]) for p in pairs][::-1]
    total = sum(values) or 1
    if isinstance(color, str):
        colors = _bar_colors(len(values), color)
    else:
        colors = list(color)[: len(values)]
        if len(colors) < len(values):
            colors = colors + _bar_colors(len(values) - len(colors))

    fig_h = max(3.8, 0.62 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(8.5, fig_h))
    fig.patch.set_facecolor("#ffffff")
    y_pos = range(len(values))
    bars = ax.barh(y_pos, values, color=colors, height=0.58, edgecolor="none")
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels)
    ax.set_xlabel(xlabel)
    ax.set_title(title, pad=12, color=_DARK)
    _style_axes(ax, grid_axis="x")
    mx = max(values) if values else 0
    ax.set_xlim(0, (mx * 1.22) if mx else 1)
    for bar, v in zip(bars, values):
        suffix = f"  ({round(100 * v / total)}%)" if show_pct and total else ""
        ax.text(
            bar.get_width() + mx * 0.015,
            bar.get_y() + bar.get_height() / 2,
            f"{v}{suffix}",
            va="center",
            ha="left",
            fontsize=9,
            fontweight="600",
            color="#334155",
        )
    return _fig_to_b64(fig)


def chart_bars(
    labels: Sequence[str],
    values: Sequence[int],
    title: str,
    color: str | Sequence[str] = _PRIMARY,
    rotate: int = 0,
) -> str | None:
    if not values:
        return None
    _apply_style()
    lbls = [_trunc(l, 16) for l in labels]
    vals = [int(v) for v in values]
    if isinstance(color, str):
        colors = _bar_colors(len(vals), color)
    else:
        colors = list(color)[: len(vals)]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    fig.patch.set_facecolor("#ffffff")
    x = range(len(lbls))
    bars = ax.bar(x, vals, color=colors, width=0.62, edgecolor="none")
    ax.set_xticks(list(x))
    ax.set_xticklabels(lbls, rotation=rotate, ha="right" if rotate else "center")
    ax.set_title(title, pad=12, color=_DARK)
    ax.set_ylabel("Quantidade")
    _style_axes(ax, grid_axis="y")
    mx = max(vals) if vals else 0
    ax.set_ylim(0, mx * 1.14 if mx else 1)
    for bar, v in zip(bars, vals):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + mx * 0.02,
            str(v),
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="600",
            color="#334155",
        )
    return _fig_to_b64(fig)


def chart_funnel(
    stages: Sequence[tuple[str, int]],
    title: str,
) -> str | None:
    if not stages:
        return None
    _apply_style()
    labels = [s[0] for s in stages]
    values = [int(s[1]) for s in stages]
    colors = _PALETTE[: len(values)]
    fig, ax = plt.subplots(figsize=(8.5, max(3.5, 0.7 * len(values) + 1.5)))
    fig.patch.set_facecolor("#ffffff")
    bars = ax.barh(range(len(values)), values, color=colors, height=0.55, edgecolor="none")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_title(title, pad=12, color=_DARK)
    ax.invert_yaxis()
    _style_axes(ax, grid_axis="x")
    mx = max(values) if values else 0
    ax.set_xlim(0, mx * 1.18 if mx else 1)
    for bar, v in zip(bars, values):
        ax.text(
            bar.get_width() + mx * 0.015,
            bar.get_y() + bar.get_height() / 2,
            str(v),
            va="center",
            ha="left",
            fontsize=10,
            fontweight="600",
            color="#334155",
        )
    return _fig_to_b64(fig)


def img_tag(b64: str | None, alt: str = "Gráfico", css_class: str = "chart-img") -> str:
    if not b64:
        return '<p class="na">Sem dados para gráfico.</p>'
    return (
        f'<img class="{css_class}" src="data:image/png;base64,{b64}" alt="{alt}">'
    )
