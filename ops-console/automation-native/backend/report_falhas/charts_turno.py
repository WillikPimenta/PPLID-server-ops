# -*- coding: utf-8 -*-
"""Gráficos e métricas por turno (fluxo principal)."""

import base64
from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from report_falhas.io.data_loader import norm_matricula, normalize_text, safe_str
import report_falhas.config_report as cfg_report
from report_falhas.charts import _clean_axes, fig_to_base64
from report_falhas.insights import compute_trend, trend_text
from report_falhas.matricula_utils import normalize_dificuldade

COL_MATRICULA = cfg_report.COL_MATRICULA
COL_DIFICULDADE = "Nível de Dificuldade"

def chart_falhas_por_turno_base64(df_cur: pd.DataFrame, turno_map: dict | None, titulo: str = 'Falhas por turno'):
    """
    Gera gráfico (base64 PNG) da DISTRIBUIÇÃO DE QUALIDADE por turno.
    
    OBJETIVO:
    - Mostrar a distribuição de níveis de dificuldade (Fácil, Médio, Difícil) por turno.
    - Apresentar a QUALIDADE das falhas, não apenas o volume.
    
    IMPORTANTE:
    - Os turnos possuem diferentes tamanhos de equipe e volumes operacionais.
    - Este gráfico NÃO deve ser interpretado como um indicador de desempenho ou taxa de erro.
    - É uma visão de distribuição proporcional de QUALIDADE das falhas por turno.
    """
    try:
        if df_cur is None or df_cur.empty or (COL_MATRICULA not in df_cur.columns):
            return None, ''
        
        # Mapeia turno para cada falha e normaliza sinônimos do HC
        tmp = df_cur.copy()
        turno_alias_map = {
            'manha': 'Matutino',
            'matutino': 'Matutino',
            'tarde': 'Vespertino',
            'vespertino': 'Vespertino',
            'noite': 'Noturno',
            'noturno': 'Noturno',
            'madrugada': 'Madrugada',
            'integral': 'Integral',
            'intermediario': 'Intermediário',
        }
        tmp['_turno_'] = tmp[COL_MATRICULA].map(lambda m: (turno_map or {}).get(norm_matricula(m), ''))
        tmp['_turno_'] = tmp['_turno_'].apply(lambda v: turno_alias_map.get(normalize_text(v), safe_str(v)))
        # Para este gráfico, ignoramos falhas sem mapeamento de turno.
        # Assim ele fica consistente com o bloco de reincidência por turno.
        tmp = tmp[tmp['_turno_'] != ''].copy()
        if tmp.empty:
            return None, ''
        
        # Se não há coluna de dificuldade, retorna None
        if COL_DIFICULDADE not in tmp.columns:
            return None, ''
        
        # Normaliza Nível de Dificuldade
        tmp['_dif_'] = tmp[COL_DIFICULDADE].apply(normalize_dificuldade)
        tmp.loc[tmp['_dif_'] == '', '_dif_'] = 'Sem classificação'
        
        # Agrupa por turno + dificuldade
        grouped = tmp.groupby(['_turno_', '_dif_']).size().unstack(fill_value=0)
        
        # Ordena colunas de dificuldade de forma lógica
        dif_order = ['Fácil', 'Médio', 'Difícil', 'Sem classificação']
        cols_present = [col for col in dif_order if col in grouped.columns]
        grouped = grouped[cols_present]
        
        if grouped.empty:
            return None, ''
        
        # Ordena turnos (mantém ordem lógica)
        turno_order = ['Madrugada', 'Noturno', 'Intermediário', 'Vespertino', 'Matutino', 'Integral']
        turnos_present = [t for t in turno_order if t in grouped.index]
        extras_present = [t for t in grouped.index if t not in turnos_present]
        turnos_present.extend(sorted(extras_present))
        grouped = grouped.loc[turnos_present]
        
        # Calcula percentuais por turno
        grouped_pct = grouped.div(grouped.sum(axis=1), axis=0) * 100
        
        # Gráfico de barras empilhadas (Stacked Bar Chart)
        fig, ax = plt.subplots(figsize=(11.5, 4.2))
        
        # Cores por nível de dificuldade (INVERTIDAS PELA CRITICIDADE)
        # Fácil = VERMELHO (falha crítica fácil = negligência) 
        # Médio = LARANJA (intermediário)
        # Difícil = VERDE (falha crítica difícil = menor gravidade relativa)
        colors = {
            'Fácil': '#E74C3C',           # Vermelho - falha crítica FÁCIL (negligência)
            'Médio': '#F39C12',           # Laranja - intermediário
            'Difícil': '#2ECC71',         # Verde - falha crítica DIFÍCIL (complexidade)
            'Sem classificação': '#BDC3C7'  # Cinza
        }
        
        bar_colors = [colors.get(col, '#95A5A6') for col in grouped_pct.columns]
        x_pos = np.arange(len(grouped_pct))
        width = 0.70
        
        # Cria barras empilhadas
        bottom = np.zeros(len(grouped_pct))
        bars_list = []
        
        for i, col in enumerate(grouped_pct.columns):
            bar = ax.bar(x_pos, grouped_pct[col], width, label=col, bottom=bottom, 
                        color=bar_colors[i], edgecolor='white', linewidth=1.5)
            bars_list.append(bar)
            bottom += grouped_pct[col].values
        
        # Rótulos dos turnos no eixo X
        ax.set_xlabel('Turno', fontsize=12, fontweight='bold', color='#1E4F91')
        ax.set_ylabel('Distribuição de Qualidade (%)', fontsize=12, fontweight='bold', color='#1E4F91')
        ax.set_xticks(x_pos)
        ax.set_xticklabels(grouped_pct.index, fontsize=10, color='#1E4F91')
        
        # Formatação dos eixos
        ax.set_ylim(0, 105)
        ax.tick_params(axis='y', labelsize=10, colors='#1E4F91')
        ax.grid(axis='y', linestyle='--', alpha=0.2, color='#CDD3DA', linewidth=0.8)
        ax.set_axisbelow(True)
        
        # Legenda fora do gráfico
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.12), ncol=4, 
                 frameon=True, fancybox=True, shadow=False, fontsize=10, 
                 edgecolor='#BDC3C7', title=COL_DIFICULDADE, title_fontsize=10)
        
        # Remove spines superiores e direitos
        for side in ['top', 'right']:
            ax.spines[side].set_visible(False)
        ax.spines['left'].set_color('#CDD3DA')
        ax.spines['bottom'].set_color('#CDD3DA')
        ax.spines['left'].set_linewidth(2)
        ax.spines['bottom'].set_linewidth(2)
        
        # Adiciona percentuais DENTRO das barras empilhadas
        bottom = np.zeros(len(grouped_pct))
        for i, col in enumerate(grouped_pct.columns):
            values = grouped_pct[col].values
            for x, v in zip(x_pos, values):
                if v > 5:  # Só mostra rótulo se > 5%
                    y = bottom[int(x)] + v / 2
                    ax.text(x, y, f'{v:.0f}%', ha='center', va='center', 
                           fontsize=9, fontweight='bold', color='white',
                           bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.3, edgecolor='none'))
            bottom += values
        
        # Converte para base64
        b64 = fig_to_base64(fig)
        
        # Texto descritivo com estatísticas
        total_falhas = len(df_cur)
        fáceis = (tmp['_dif_'] == 'Fácil').sum()
        médios = (tmp['_dif_'] == 'Médio').sum()
        difíceis = (tmp['_dif_'] == 'Difícil').sum()
        
        txt = (
            f"<strong>Distribuição de qualidade por turno (n={total_falhas})</strong>. "
            f"Gráfico mostra a proporção de falhas por nível de dificuldade em cada turno. "
            f"<br><br>"
            f"<strong>Resumo geral:</strong> "
            f"🔴 Fácil: {fáceis} ({fáceis/total_falhas*100:.1f}%) "
            f"| 🟡 Médio: {médios} ({médios/total_falhas*100:.1f}%) "
            f"| 🟢 Difícil (complexidade): {difíceis} ({difíceis/total_falhas*100:.1f}%)"
            f"<br><br>"
            f"<strong>Interpretação das cores:</strong><br>"
            f"🔴 <strong>Fácil (Vermelho)</strong> = Falhas críticas que deveriam ter sido evitadas, com baixo grau de complexidade técnica. Indicam maior exposição operacional e necessidade de reforço de padrão.<br>"
            f"🟡 <strong>Médio (Laranja)</strong> = Falhas criticas de complexidade intermediária, requerem atenção e orientação direcionada.<br>"
            f"🟢 <strong>Difícil (Verde)</strong> = Falhas críticas de alta complexidade técnica, atuam como fator atenuante na análise de risco.<br><br>"
            f"<em>Observação importante:</em> Os turnos possuem diferentes tamanhos de equipe e volumes operacionais. "
            f"Este gráfico não deve ser interpretado como um indicador de desempenho ou taxa de erro, "
            f"mas sim como uma visão de <strong>distribuição proporcional de qualidade das falhas</strong>."
        )
        
        return b64, txt
    except Exception as e:
        return None, ''


def build_agentes_ativos_por_turno(df_hc: pd.DataFrame) -> tuple[dict[str, int], int]:
    """Retorna contagem de agentes ativos por turno.

    Regra de negócio: ativo = data_final IS NULL, turno preenchido e JobTitle = Agente Backoffice I.
    """
    if df_hc is None or df_hc.empty:
        return {}, 0
    if 'data_final' not in df_hc.columns or 'turno' not in df_hc.columns or 'matricula_agente' not in df_hc.columns:
        return {}, 0

    df = df_hc.copy()
    jobtitle_col = None
    for col in df.columns:
        if normalize_text(col) == 'jobtitle':
            jobtitle_col = col
            break
    if jobtitle_col is None:
        return {}, 0

    df = df[df['data_final'].isna()].copy()
    if df.empty:
        return {}, 0

    # Filtrar HC para equipe 'Fraud' se a coluna 'Team: Sector' existir
    team_col = None
    for col in df.columns:
        if normalize_text(col) == normalize_text('Team: Sector'):
            team_col = col
            break
    if team_col is not None:
        df[team_col] = df[team_col].apply(safe_str)
        df = df[df[team_col].apply(lambda v: 'fraud' in normalize_text(v))].copy()
        if df.empty:
            return {}, 0

    df[jobtitle_col] = df[jobtitle_col].apply(safe_str)
    df = df[df[jobtitle_col].apply(normalize_text) == normalize_text('Agente Backoffice I')].copy()
    if df.empty:
        return {}, 0

    df['turno'] = df['turno'].apply(safe_str)
    df = df[df['turno'] != ''].copy()
    if df.empty:
        return {}, 0

    if 'mat_norm' not in df.columns:
        df['mat_norm'] = df['matricula_agente'].apply(norm_matricula)
    df = df[df['mat_norm'] != ''].copy()
    if df.empty:
        return {}, 0

    counts = df.groupby('turno')['mat_norm'].nunique()
    return {str(k): int(v) for k, v in counts.items()}, int(counts.sum())


def build_falhas_por_agente_turno(df_cur: pd.DataFrame, turno_map: dict | None, agentes_turno_map: dict[str, int]) -> list[dict]:
    """Retorna lista de turnos com falhas, agentes e falhas por agente."""
    if df_cur is None or df_cur.empty or (COL_MATRICULA not in df_cur.columns):
        return []

    tmp = df_cur.copy()
    tmp['_turno_'] = tmp[COL_MATRICULA].map(lambda m: (turno_map or {}).get(norm_matricula(m), ''))
    tmp['_turno_'] = tmp['_turno_'].apply(safe_str)
    tmp = tmp[tmp['_turno_'] != ''].copy()

    falhas_por_turno = tmp['_turno_'].value_counts().to_dict()
    turnos_all = set(agentes_turno_map.keys()) | set(falhas_por_turno.keys())

    rows = []
    for turno in turnos_all:
        turno_txt = safe_str(turno)
        if turno_txt.lower() == 'integral':
            continue
        agentes = int(agentes_turno_map.get(turno, 0))
        if agentes <= 0:
            continue
        falhas = int(falhas_por_turno.get(turno, 0))
        if falhas <= 0:
            continue
        fpa = falhas / agentes if agentes else 0
        rows.append({
            'Turno': turno_txt,
            'Falhas': falhas,
            'Agentes': agentes,
            'Falhas por agente': fpa,
        })

    order = ['Madrugada', 'Noite', 'Intermediário', 'Tarde', 'Manhã']
    order_idx = {t: i for i, t in enumerate(order)}
    rows.sort(key=lambda r: order_idx.get(r['Turno'], 999))
    return rows


def chart_falhas_por_agente_turno_base64(rows: list[dict]) -> tuple[str | None, str]:
    if not rows:
        return None, ''

    labels = [r['Turno'] for r in rows]
    values = [float(r['Falhas por agente']) for r in rows]

    fig, ax = plt.subplots(figsize=(9.5, 3.8))
    max_val = max(values) if values else 0
    colors = ['#F5A623' if v == max_val else '#1E4F91' for v in values]

    bars = ax.bar(labels, values, color=colors, edgecolor='#1E4F91', linewidth=1)
    ax.set_ylabel('Falhas por agente', fontsize=11, fontweight='bold', color='#1E4F91')
    ax.set_xlabel('Turno', fontsize=11, fontweight='bold', color='#1E4F91')
    ax.tick_params(axis='x', labelsize=10)
    ax.tick_params(axis='y', labelsize=10, colors='#1E4F91')
    ax.grid(axis='y', linestyle='--', alpha=0.2, color='#CDD3DA', linewidth=0.8)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    for rect, val in zip(bars, values):
        ax.text(
            rect.get_x() + rect.get_width() / 2,
            rect.get_height(),
            f"{val:.2f}".replace('.', ',') + "\nfalhas/agente",
            ha='center',
            va='bottom',
            fontsize=9,
            fontweight='bold',
            color='#1E4F91',
        )

    mean_val = sum(values) / len(values) if values else 0
    max_idx = values.index(max_val) if values else 0
    min_val = min(values) if values else 0
    min_idx = values.index(min_val) if values else 0

    max_txt = f"{max_val:.2f}".replace('.', ',')
    mean_txt = f"{mean_val:.2f}".replace('.', ',')
    min_txt = f"{min_val:.2f}".replace('.', ',')
    resumo = (
        "<div style='font-size:12px;color:#6c757d;margin:6px 0 8px;'>"
        f"<b>Maior pressão operacional:</b> {safe_str(labels[max_idx])} ({max_txt}) &nbsp;|&nbsp; "
        f"<b>Média geral:</b> {mean_txt} &nbsp;|&nbsp; "
        f"<b>Menor pressão operacional:</b> {safe_str(labels[min_idx])} ({min_txt})"
        "</div>"
    )

    return fig_to_base64(fig), resumo


def render_falhas_por_agente_turno_table(rows: list[dict]) -> str:
    if not rows:
        return ''
    body = []
    for r in rows:
        fpa_txt = f"{r['Falhas por agente']:.2f}".replace('.', ',')
        body.append(
            "<tr>"
            f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{safe_str(r['Turno'])}</td>"
            f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;'>{int(r['Falhas'])}</td>"
            f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;'>{int(r['Agentes'])}</td>"
            f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;'>{fpa_txt}</td>"
            "</tr>"
        )

    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' "
        "style='border-collapse:collapse;width:100%;font-size:12px;margin:10px 0 6px;'>"
        "<thead><tr style='background:#F8F9FA;'>"
        "<th style='padding:8px;border:1px solid #e9ecef;text-align:left;'>Turno</th>"
        "<th style='padding:8px;border:1px solid #e9ecef;text-align:right;'>Falhas</th>"
        "<th style='padding:8px;border:1px solid #e9ecef;text-align:right;'>Agentes ativos</th>"
        "<th style='padding:8px;border:1px solid #e9ecef;text-align:right;'>Falhas por agente</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table>"
    )
def chart_barras_base64_custom(pares_label_val, titulo: str, min_points: int = 2):
    if not pares_label_val:
        return None, "Sem dados para análise de tendência.", None
    labels = [str(l) for (l, _) in pares_label_val]
    values = []
    for (_, v) in pares_label_val:
        try:
            values.append(float(v))
        except Exception:
            values.append(float('nan'))
    non_nan = [v for v in values if not np.isnan(v)]
    if len(non_nan) < min_points:
        slope, intercept = None, None
    else:
        slope, intercept = compute_trend(values)
    fig, ax = plt.subplots(figsize=(10.0, 3.6))
    bar_width = 0.35 if len(values) <= 1 else 0.8
    bars = ax.bar(range(len(values)), values, color="#1D4F91", zorder=2, width=bar_width)
    if len(values) == 1:
        ax.set_xlim(-0.6, 0.6)
    if slope is not None:
        x = np.arange(len(values))
        y_pred = slope * x + intercept
        ax.plot(x, y_pred, color="orange", linestyle="--", label="Tendência", zorder=3)
        ax.legend()
    total = float(sum([float(v) for v in values if not np.isnan(v)]))
    max_val = max([float(v) for v in values if not np.isnan(v)] or [0.0])
    for i, bar in enumerate(bars):
        v = values[i]
        if np.isnan(v):
            continue
        pct = (float(v)/total*100.0) if total else 0.0
        x = bar.get_x() + bar.get_width()/2.0
        y = bar.get_height() * 0.55
        if bar.get_height() < (0.12 * (max_val or 1)):
            y = bar.get_height() * 0.72
        label = f"{int(round(v))} ({pct:.1f}%)"
        ax.text(
            x, y, label,
            ha="center", va="center",
            color="black", fontsize=9, fontweight="bold", zorder=4,
            bbox=dict(facecolor="white", alpha=0.55, boxstyle="round,pad=0.15", edgecolor="none")
        )
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=0)
    ax.set_title(titulo, fontsize=11)
    ax.set_ylabel("Qtde")
    ax.set_ylim(0, (max_val * 1.12) if max_val else ax.get_ylim()[1])
    _clean_axes(ax)
    fig.subplots_adjust(bottom=0.16)
    buf = BytesIO()
    fig.tight_layout(pad=0.6)
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return b64, trend_text(slope), slope
#Considerar por turno

def chart_linha_base64(pares_label_val):
    if not pares_label_val:
        return None, "Sem dados para análise de tendência.", None
    labels = [l for (l, _) in pares_label_val]
    values = [v for (_, v) in pares_label_val]
    slope, intercept = compute_trend(values)
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(11, 4.0))
    ax.plot(x, values, marker="o", color="#6D2077", linewidth=2, markersize=5)
    if slope is not None:
        y_pred = slope * x + intercept
        ax.plot(x, y_pred, color="orange", linestyle="--", label="Tendência")
        ax.legend()
    ax.set_title("Falhas por dia - mês atual", fontsize=11)
    ax.set_ylabel("Qtde")
    ax.margins(x=0.01)
    from math import ceil
    max_labels = 15
    step = max(1, ceil(len(labels) / max_labels))
    xticks = list(range(0, len(labels), step))
    total = sum(values)
    xtick_labels = [f"{labels[i]}\nQtd: {values[i]} ({(values[i]/total*100 if total else 0):.1f}%)" for i in xticks]
    ax.set_xticks(xticks)
    ax.set_xticklabels(xtick_labels, rotation=0, ha="center", fontsize=8)
    _clean_axes(ax)
    fig.subplots_adjust(bottom=0.35)
    return fig_to_base64(fig), trend_text(slope), slope
