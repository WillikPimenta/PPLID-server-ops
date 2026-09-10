# -*- coding: utf-8 -*-
"""Renderização HTML executivo BRB — abas, gráficos e storytelling."""
from __future__ import annotations

import html as html_lib
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

from report_brb.brb_charts import (
    chart_barh,
    chart_bars,
    chart_donut,
    chart_funnel,
    img_tag,
)
from report_brb.brb_format import (
    HIDDEN_COLS,
    cell_html,
    column_label,
)
from report_brb.brb_glossary import GLOSSARY, STORY, glossary_html
from report_brb.brb_analytics import BRBAnalytics, compute_analytics
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics
from report_brb.brb_reconciliation import build_reconciliation
from report_brb.brb_timeline import build_timeline
from report_brb.config_brb import CLIENT

C = CLIENT["cores"]
_TAB_IDS = []  # definido em brb_render_executive.EXEC_TAB_IDS

TL_CAT_LABELS = {
    "demanda": "Demanda",
    "na": "Notificação Ativa",
    "fg": "Falhas Gerais",
    "contestacao": "Contestação",
    "treinamento": "Capacitação",
}

NA_DEMANDA_COLS = [
    ("Demanda", "Demanda"),
    ("Mês", "Mês"),
    ("Quantidade de Protolocos", "Qtd. protocolos"),
    ("Situação", "Situação"),
    ("Data da Abertura", "Data abertura"),
]

NA_ATTACK_COLS = [
    ("PROTOCOLO", "Protocolo"),
    ("DATA DE CADASTRO", "Data cadastro"),
    ("DEMANDA", "Demanda"),
    ("demanda_inferida", "Demanda inferida"),
    ("DATA DE NOTIFICAÇÃO", "Data notificação"),
    ("MOTIVO DA FALHA", "Motivo"),
    ("RESULTADO DO CLIENTE", "Resultado cliente"),
]

FG_DETAIL_COLS = [
    ("Protocolo", "Protocolo"),
    ("Matrícula Agente", "Matrícula"),
    ("Nome Agente", "Agente"),
    ("Novo cenário", "Cenário"),
    ("categoria_macro", "Categoria"),
    ("severidade", "Severidade"),
    ("fn_fp", "FN/FP"),
    ("CONFORME", "CONFORME"),
]

FG_MULTI_COLS = [
    ("_protocolo_norm", "Protocolo"),
    ("n_matriculas", "Nº matrículas"),
    ("Nome Agente", "Agente"),
]

PROC_COLS = [
    ("Protocolo", "Protocolo"),
    ("Matrícula", "Matrícula"),
    ("Colaborador", "Colaborador"),
    ("CONFORME", "CONFORME"),
    ("Cenário", "Cenário"),
    ("Data de Análise", "Data análise"),
]


def _esc(x) -> str:
    return html_lib.escape("" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))


def _empty_state(msg: str = "Nenhum registro no período.") -> str:
    return f'<div class="empty-state">{_esc(msg)}</div>'


def _lead(key: str) -> str:
    """Um parágrafo introdutório por aba."""
    text = STORY.get(key, "")
    return f'<p class="lead">{text}</p>' if text else ""


def _insight_bar(items: list[tuple[str, str]]) -> str:
    """Faixa executiva com métricas-chave da aba."""
    if not items:
        return ""
    parts = "".join(
        f'<div class="insight-item"><span class="insight-val">{_esc(v)}</span>'
        f'<span class="insight-lbl">{_esc(k)}</span></div>'
        for k, v in items
    )
    return f'<div class="insight-bar">{parts}</div>'


def _callout(html: str) -> str:
    return f'<div class="callout">{html}</div>'


def _card(label: str, value, sub: str = "", tip_key: str = "", accent: str = "primary") -> str:
    tip = GLOSSARY.get(tip_key or label, "")
    tip_html = f' <span class="tip" title="{_esc(tip)}">i</span>' if tip else ""
    sub_html = f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else ""
    return (
        f'<div class="kpi accent-{accent}">'
        f'<div class="kpi-lbl">{_esc(label)}{tip_html}</div>'
        f'<div class="kpi-val">{_esc(value)}</div>'
        f"{sub_html}</div>"
    )


def _resolve_columns(
    df: pd.DataFrame,
    columns: list[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    if columns:
        return [(c, lbl) for c, lbl in columns if c in df.columns]
    return [
        (c, column_label(c))
        for c in df.columns
        if c not in HIDDEN_COLS and not str(c).startswith("_")
    ]


def _df_table(
    df: pd.DataFrame,
    columns: list[tuple[str, str]] | None = None,
    max_rows: int = 25,
    table_id: str | None = None,
) -> str:
    if df is None or df.empty:
        return _empty_state()
    tid = table_id or f"tbl-{uuid.uuid4().hex[:8]}"
    cols = _resolve_columns(df, columns)
    if not cols:
        return _empty_state()
    total = len(df)
    shown = min(max_rows, total)
    sub = df.head(shown)
    headers = "".join(f"<th>{_esc(lbl)}</th>" for _, lbl in cols)
    rows = []
    for _, row in sub.iterrows():
        cells = "".join(cell_html(row[c], c, _esc) for c, _ in cols)
        rows.append(f"<tr>{cells}</tr>")
    foot = ""
    if total > shown:
        foot = (
            f'<div class="table-foot">Exibindo {shown} de {total} registros · '
            f'<button type="button" class="btn-more" onclick="expandTable(\'{tid}\')">Ver mais</button></div>'
        )
    else:
        foot = f'<div class="table-foot">Exibindo {total} registro(s)</div>'
    hidden_rows = ""
    if total > shown:
        extra = df.iloc[shown:]
        extra_rows = []
        for _, row in extra.iterrows():
            cells = "".join(cell_html(row[c], c, _esc) for c, _ in cols)
            extra_rows.append(f'<tr class="extra-row" style="display:none">{cells}</tr>')
        hidden_rows = "".join(extra_rows)
    return (
        f'<div class="table-wrap" id="wrap-{tid}" data-total="{total}" data-shown="{shown}">'
        f'<table class="data-table" id="{tid}">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}{hidden_rows}</tbody></table>{foot}</div>"
    )


def _section(title: str, body: str, subtitle: str = "") -> str:
    sub = f'<p class="section-sub">{_esc(subtitle)}</p>' if subtitle else ""
    return (
        f'<div class="block">'
        f'<h3 class="block-title">{_esc(title)}</h3>{sub}{body}</div>'
    )


def _kpi_strip(metrics: BRBMetrics) -> str:
    cards = "".join(
        [
            _card("Protocolos NA", metrics.protocolos_na, "demandas agregadas", "Protocolos NA"),
            _card("Casos FG", metrics.casos_unicos_fg, f"{metrics.protocolos_distintos_fg} protocolos", "Casos únicos FG"),
            _card("Falhas NA", metrics.na_falhas_registros, f"{metrics.na_falhas_protocolos} protocolos", "Falhas NA"),
            _card(
                "Taxa conforme",
                f"{metrics.pct_improcedente}%",
                f"{metrics.conforme_sim} Sim · {metrics.conforme_nao} Não",
                "CONFORME Sim",
                "green",
            ),
        ]
    )
    return f'<div class="kpi-strip">{cards}</div>'


def _visao_charts(metrics: BRBMetrics) -> tuple[str, str]:
    donut = _chart_frame(
        img_tag(
            chart_donut(
                ["Conforme (Sim)", "Falha (Não)"],
                [metrics.conforme_sim, metrics.conforme_nao],
                "Distribuição CONFORME",
                colors=[C["green"], C["red"]],
            ),
            "CONFORME",
        ),
        "Procedência — avaliação CONFORME",
    )
    funnel = _chart_frame(
        img_tag(
            chart_funnel(
                [
                    ("Casos FG", metrics.casos_unicos_fg),
                    ("Com contestação", metrics.contestacao_casos_unicos),
                    ("Sem CONFORME", metrics.fg_sem_contestacao),
                    ("Falhas NA", metrics.na_falhas_registros),
                ],
                "Fluxo entre bases",
            ),
            "Funil",
        ),
        "Volume por etapa do processo",
    )
    return donut, funnel


def _numeros_hint() -> str:
    return (
        '<div class="callout callout-sm">'
        "Cada indicador vem de uma aba do Excel com regra própria — "
        '<b>não some os valores</b>. '
        '<a href="#base" class="link-tab" onclick="setTab(\'base\');return false;">'
        "Ver Base Detalhada</a></div>"
    )


def _flow_strip(metrics: BRBMetrics) -> str:
    """Fluxo operacional simplificado."""
    steps = [
        ("Demandas NA", metrics.protocolos_na, "Volume agregado por QI"),
        ("Falhas NA", metrics.na_falhas_registros, "Notificadas ao cliente"),
        ("Casos FG", metrics.casos_unicos_fg, "Auditados (Prot. + Matr.)"),
        ("Avaliados", metrics.contestacao_casos_unicos, "Contestação CONFORME"),
    ]
    parts = []
    for i, (label, val, hint) in enumerate(steps):
        if i:
            parts.append('<div class="flow-arrow" aria-hidden="true">›</div>')
        parts.append(
            f'<div class="flow-step" title="{_esc(hint)}">'
            f'<span class="flow-val">{_esc(val)}</span>'
            f'<span class="flow-lbl">{_esc(label)}</span></div>'
        )
    cap = ""
    if metrics.treinamentos_registros:
        cap = (
            f'<div class="flow-cap">Capacitação: <b>{metrics.treinamentos_registros}</b> registros · '
            f"<b>{metrics.treinamentos_agentes}</b> agentes</div>"
        )
    return (
        '<div class="flow-wrap">'
        '<p class="flow-note">Cada etapa usa regra de contagem diferente — <b>não some os valores</b>.</p>'
        f'<div class="flow-strip">{"".join(parts)}</div>{cap}</div>'
    )


def _grid2(a: str, b: str) -> str:
    return f'<div class="grid-2">{a}{b}</div>'


def _chart_frame(img_html: str, caption: str = "") -> str:
    cap = f'<div class="chart-caption">{_esc(caption)}</div>' if caption else ""
    return f'<div class="chart-card">{img_html}{cap}</div>'


def _build_tab_visao(metrics: BRBMetrics, periodo_label: str, metrics_full: BRBMetrics | None) -> str:
    period_callout = ""
    if metrics_full and "sem filtro" not in periodo_label.lower():
        period_callout = (
            f'<div class="callout callout-sm">Comparativo: <b>{metrics.casos_unicos_fg}</b> casos FG no período '
            f"vs <b>{metrics_full.casos_unicos_fg}</b> na base total.</div>"
        )
    donut, funnel = _visao_charts(metrics)
    return (
        _lead("visao")
        + _kpi_strip(metrics)
        + _numeros_hint()
        + period_callout
        + _grid2(donut, funnel)
    )


def _build_tab_numeros_body(
    metrics: BRBMetrics,
    bundle: BRBDataBundle,
    periodo_label: str,
    metrics_full: BRBMetrics | None,
) -> str:
    cards = _recon_cards(metrics, bundle, periodo_label, metrics_full)
    return (
        _flow_strip(metrics)
        + '<div class="callout callout-warn">Os números abaixo <b>não devem ser somados</b> entre si. '
        "Cada linha vem de uma aba do Excel com regra própria de contagem.</div>"
        + f'<div class="recon-list">{cards}</div>'
    )


def _build_tab_numeros(
    metrics: BRBMetrics,
    bundle: BRBDataBundle,
    periodo_label: str,
    metrics_full: BRBMetrics | None,
) -> str:
    return _lead("numeros") + _build_tab_numeros_body(metrics, bundle, periodo_label, metrics_full)


def _build_tab_na(bundle: BRBDataBundle, metrics: BRBMetrics) -> str:
    chart_mes = _chart_frame(
        img_tag(
            chart_bars(
                list(metrics.por_mes_demandas.keys()),
                list(metrics.por_mes_demandas.values()),
                "Volume por mês",
                rotate=25,
            ),
            "NA por mês",
        ),
        "Demandas abertas por mês",
    )
    motivos = (
        list(zip(metrics.top_motivos_na["Motivo"], metrics.top_motivos_na["Quantidade"]))
        if not metrics.top_motivos_na.empty
        else []
    )
    chart_mot = _chart_frame(
        img_tag(chart_barh(motivos, "Principais motivos", color=C["dark"]), "Motivos NA"),
        "Motivos das falhas notificadas",
    )
    na_attack = (
        bundle.na_falhas[bundle.na_falhas.get("possivel_ataque", False) == True]
        if not bundle.na_falhas.empty
        else pd.DataFrame()
    )
    insight = _insight_bar(
        [
            ("Falhas notificadas", str(metrics.na_falhas_registros)),
            ("Possível ataque", str(metrics.possivel_ataque_na)),
            ("Demanda inferida", str(metrics.na_demanda_inferida)),
        ]
    )
    dem_cols = [c for c, _ in NA_DEMANDA_COLS if c in bundle.na_demandas.columns]
    na_cols = [c for c, _ in NA_ATTACK_COLS if c in na_attack.columns]
    return (
        _lead("na")
        + insight
        + _grid2(chart_mes, chart_mot)
        + _section("Demandas abertas", _df_table(bundle.na_demandas, NA_DEMANDA_COLS if dem_cols else None, table_id="na-dem"))
        + _section(
            "Falhas com indício de ataque",
            _df_table(na_attack, NA_ATTACK_COLS if na_cols else None, table_id="na-atk"),
            "Registros com palavras-chave de fraude ou adulteração",
        )
    )


def _build_tab_fg(bundle: BRBDataBundle, metrics: BRBMetrics) -> str:
    return _lead("fg") + _build_tab_fg_body(bundle, metrics)


def _build_tab_fg_body(bundle: BRBDataBundle, metrics: BRBMetrics) -> str:
    tend_pairs = sorted(metrics.por_tendencia.items(), key=lambda x: -x[1]) if metrics.por_tendencia else []
    chart_tend = _chart_frame(
        img_tag(
            chart_barh(tend_pairs, "Classificação por Tendência", color=C["primary"]),
            "Tendência",
        ),
        "Distribuição por tipo de achado na auditoria",
    )
    tend_col = next((c for c in bundle.falhas_gerais.columns if "end" in str(c).lower()), None)
    fg_cols = FG_DETAIL_COLS.copy()
    if tend_col:
        fg_cols = [
            ("Protocolo", "Protocolo"),
            ("Matrícula Agente", "Matrícula"),
            ("Nome Agente", "Agente"),
            (tend_col, "Tendência"),
            ("Novo cenário", "Cenário"),
            ("categoria_macro", "Categoria"),
            ("severidade", "Severidade"),
            ("fn_fp", "FN/FP"),
            ("CONFORME", "CONFORME"),
        ]
    insight = _insight_bar(
        [
            ("Casos únicos", str(metrics.casos_unicos_fg)),
            ("FN", str(metrics.fn_count)),
            ("FP", str(metrics.fp_count)),
            ("Multi-usuário", str(metrics.protocolos_multi_usuario_fg)),
        ]
    )
    return (
        insight
        + chart_tend
        + _section(
            "Protocolos com múltiplas matrículas",
            _df_table(metrics.multi_usuario, FG_MULTI_COLS, max_rows=15, table_id="fg-multi"),
        )
        + _section("Detalhamento auditado", _df_table(bundle.falhas_gerais, fg_cols, table_id="fg-det"))
    )


def _build_tab_procedencia(metrics: BRBMetrics) -> str:
    donut = _chart_frame(
        img_tag(
            chart_donut(
                ["Conforme (Sim)", "Falha (Não)"],
                [metrics.conforme_sim, metrics.conforme_nao],
                f"{metrics.pct_improcedente}% conforme",
                colors=[C["green"], C["red"]],
            ),
            "Procedência",
        ),
        "Taxa de conformidade operacional",
    )
    cenarios = (
        list(zip(metrics.conforme_por_cenario["Cenário"], metrics.conforme_por_cenario["Quantidade"]))
        if not metrics.conforme_por_cenario.empty
        else []
    )
    chart_cen = _chart_frame(
        img_tag(
            chart_barh(cenarios, "Cenários com falha procedente", color=C["red"]),
            "Cenários",
        ),
        "Cenários com maior volume de falha (Não)",
    )
    insight = _insight_bar(
        [
            ("Avaliados", str(metrics.contestacao_casos_unicos)),
            ("Conforme", str(metrics.conforme_sim)),
            ("Falha", str(metrics.conforme_nao)),
            ("FG sem avaliação", str(metrics.fg_sem_contestacao)),
        ]
    )
    return (
        _lead("procedencia")
        + insight
        + _grid2(donut, chart_cen)
        + _section("Detalhamento", _df_table(metrics.procedencia_detalhe, PROC_COLS, table_id="proc-det"))
    )


def _build_tab_capacitacao(bundle: BRBDataBundle, metrics: BRBMetrics) -> str:
    if not bundle.treinamentos.empty and "Status" in bundle.treinamentos.columns:
        trein_resumo = bundle.treinamentos["Status"].value_counts().reset_index()
        trein_resumo.columns = ["Status", "Quantidade"]
        pairs = list(zip(trein_resumo["Status"], trein_resumo["Quantidade"]))
    else:
        trein_resumo = pd.DataFrame()
        pairs = []
    chart = _chart_frame(
        img_tag(chart_barh(pairs, "Status dos treinamentos", color="#6D2077"), "Treinamentos"),
        "Status das ações de capacitação",
    )
    insight = _insight_bar(
        [
            ("Registros", str(metrics.treinamentos_registros)),
            ("Agentes", str(metrics.treinamentos_agentes)),
        ]
    )
    return (
        _lead("capacitacao")
        + insight
        + chart
        + _section("Resumo", _df_table(trein_resumo, [("Status", "Status"), ("Quantidade", "Quantidade")]))
    )


def _tl_cat_label(categoria: str, badge: str) -> str:
    b = str(badge).upper()
    if "NÃO" in b or "NAO" in b:
        return "Falha procedente"
    if "SIM" in b and categoria == "contestacao":
        return "Conforme"
    return TL_CAT_LABELS.get(categoria, badge)


def _build_tab_timeline(bundle: BRBDataBundle) -> str:
    timeline = build_timeline(bundle, limit=80)
    if timeline.empty:
        return _lead("timeline") + _empty_state("Sem eventos no período.")

    n_events = len(timeline)
    n_months = timeline["mes_label"].nunique()

    filters = (
        '<div class="filter-bar">'
        '<span class="filter-label">Filtrar:</span>'
        '<button type="button" class="filter-btn active" onclick="filterTimeline(\'all\',this)">Todos</button>'
        '<button type="button" class="filter-btn" onclick="filterTimeline(\'demanda\',this)">Demandas</button>'
        '<button type="button" class="filter-btn" onclick="filterTimeline(\'na\',this)">NA</button>'
        '<button type="button" class="filter-btn" onclick="filterTimeline(\'fg\',this)">FG</button>'
        '<button type="button" class="filter-btn" onclick="filterTimeline(\'contestacao\',this)">Contestação</button>'
        '<button type="button" class="filter-btn" onclick="filterTimeline(\'treinamento\',this)">Capacitação</button>'
        f'<span class="filter-meta">{n_events} eventos em {n_months} meses</span>'
        "</div>"
    )

    blocks = []
    for mes, grp in timeline.groupby("mes_label", sort=False):
        rows = []
        for _, row in grp.iterrows():
            cat = row["categoria"]
            label = _tl_cat_label(cat, row["badge"])
            rows.append(
                f'<tr class="tl-row" data-categoria="{_esc(cat)}">'
                f'<td class="col-date">{_esc(row["data_fmt"])}</td>'
                f'<td class="col-cat"><span class="cat cat-{_esc(cat)}">{_esc(label)}</span></td>'
                f'<td class="col-ref cell-mono">{_esc(row["referencia"])}</td>'
                f'<td class="col-desc">{_esc(row["detalhe"])}</td></tr>'
            )
        blocks.append(
            f'<div class="tl-month" data-month="{_esc(mes)}">'
            f'<h4 class="month-title">{_esc(mes)}</h4>'
            f'<div class="table-wrap"><table class="data-table tl-table">'
            f"<thead><tr><th>Data</th><th>Tipo</th><th>Referência</th><th>Descrição</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div></div>"
        )

    return _lead("timeline") + filters + "".join(blocks)


def _base_css() -> str:
    P, D, G, R, BD, BG = C["primary"], C["dark"], C["gray"], C["red"], C["border"], C["bg"]
    return f"""
    *{{box-sizing:border-box;}}
    body{{margin:0;background:#eef2f7;font-family:"Segoe UI",system-ui,Arial,sans-serif;color:#1e293b;
          font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;}}
    .wrap{{max-width:1140px;margin:0 auto;padding:28px 24px 40px;}}
    .hero{{background:linear-gradient(90deg,{D},{P});color:#fff;border-radius:8px;padding:28px 32px;
           display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap;}}
    .hero-main{{flex:1;min-width:240px;}}
    .hero-eyebrow{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;opacity:.8;margin-bottom:8px;}}
    .hero h1{{margin:0;font-size:24px;font-weight:700;letter-spacing:-.02em;}}
    .hero-period{{font-size:13px;opacity:.9;margin-top:8px;}}
    .hero-date{{font-size:12px;opacity:.75;white-space:nowrap;}}
    .nav{{display:flex;gap:0;border-bottom:2px solid {BD};margin:20px 0 0;background:#fff;
          border-radius:8px 8px 0 0;padding:0 8px;overflow-x:auto;position:sticky;top:0;z-index:10;
          box-shadow:0 1px 3px rgba(0,0,0,.06);}}
    .nav a{{text-decoration:none;padding:14px 16px;font-size:13px;font-weight:600;color:{G};
            border-bottom:3px solid transparent;margin-bottom:-2px;white-space:nowrap;}}
    .nav a:hover{{color:{D};}}
    .nav a.active{{color:{P};border-bottom-color:{P};}}
    .nav a.hidden{{display:none;}}
    .panel{{background:#fff;border:1px solid {BD};border-top:none;border-radius:0 0 8px 8px;
            padding:32px 36px;min-height:400px;}}
    .tab-panel{{display:none;}}
    .tab-panel.active{{display:block;}}
    .link-tab{{color:{P};font-weight:600;text-decoration:none;}}
    .link-tab:hover{{text-decoration:underline;}}
    .lead{{font-size:15px;color:#475569;margin:0 0 24px;line-height:1.65;max-width:820px;}}
    .callout{{background:#f8fafc;border-left:3px solid {P};padding:16px 20px;margin-bottom:28px;
              font-size:14px;line-height:1.65;color:#334155;border-radius:0 6px 6px 0;}}
    .callout-sm{{padding:12px 16px;margin:16px 0;font-size:13px;}}
    .callout-warn{{background:#fffbeb;border-left-color:#d97706;color:#78350f;}}
    .highlight-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px;}}
    @media(max-width:900px){{.highlight-grid{{grid-template-columns:1fr;}}}}
    .highlight{{background:#fff;border:1px solid {BD};border-radius:6px;padding:18px 20px;
                border-top:3px solid {P};}}
    .highlight.accent-na{{border-top-color:#2563eb;}}
    .highlight.accent-fg{{border-top-color:#7c3aed;}}
    .highlight.accent-procedencia{{border-top-color:{C["green"]};}}
    .highlight-title{{font-size:12px;font-weight:700;color:{D};text-transform:uppercase;
                      letter-spacing:.06em;margin-bottom:10px;}}
    .highlight-list{{margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.6;}}
    .highlight-list li{{margin-bottom:4px;}}
    .flow-wrap{{margin-bottom:28px;}}
    .flow-note{{font-size:12px;color:{G};margin:0 0 12px;}}
    .flow-strip{{display:flex;align-items:stretch;gap:0;background:#fff;border:1px solid {BD};
                 border-radius:6px;overflow:hidden;}}
    @media(max-width:700px){{.flow-strip{{flex-direction:column;}}.flow-arrow{{display:none;}}}}
    .flow-step{{flex:1;text-align:center;padding:16px 12px;border-right:1px solid {BD};}}
    .flow-step:last-of-type{{border-right:none;}}
    .flow-val{{display:block;font-size:26px;font-weight:700;color:{D};line-height:1;}}
    .flow-lbl{{display:block;font-size:11px;color:{G};margin-top:6px;text-transform:uppercase;
               letter-spacing:.04em;}}
    .flow-arrow{{display:flex;align-items:center;color:#cbd5e1;font-size:22px;font-weight:300;
                 padding:0 4px;flex-shrink:0;}}
    .flow-cap{{font-size:12px;color:{G};margin-top:10px;text-align:center;}}
    .chart-card{{background:#fff;border:1px solid {BD};border-radius:6px;padding:12px 14px 10px;}}
    .chart-card img{{width:100%;height:auto;display:block;border-radius:4px;}}
    .chart-caption{{font-size:11px;color:{G};text-align:center;margin-top:8px;font-weight:600;
                    text-transform:uppercase;letter-spacing:.04em;}}
    .glossary-details{{margin-top:24px;border:1px solid {BD};border-radius:6px;background:#fafbfc;}}
    .glossary-details summary{{padding:14px 18px;font-weight:600;color:{D};cursor:pointer;font-size:13px;}}
    .glossary-details .glossary-body{{padding:0 18px 18px;}}
    .kpi-strip{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:28px;}}
    @media(max-width:900px){{.kpi-strip{{grid-template-columns:repeat(2,1fr);}}}}
    @media(max-width:500px){{.kpi-strip{{grid-template-columns:1fr;}}}}
    .kpi{{background:#fff;border:1px solid {BD};border-radius:6px;padding:18px 20px;
          border-left:4px solid {P};}}
    .kpi.accent-green{{border-left-color:{C["green"]};}}
    .kpi-lbl{{font-size:11px;font-weight:600;color:{G};text-transform:uppercase;letter-spacing:.06em;}}
    .kpi-val{{font-size:32px;font-weight:700;color:{D};margin-top:6px;letter-spacing:-.02em;line-height:1;}}
    .kpi-sub{{font-size:12px;color:{G};margin-top:6px;}}
    .tip{{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;
          border-radius:50%;background:#e2e8f0;color:{G};font-size:9px;font-weight:700;cursor:help;vertical-align:middle;}}
    .insight-bar{{display:flex;flex-wrap:wrap;gap:0;border:1px solid {BD};border-radius:6px;
                  margin-bottom:24px;overflow:hidden;background:#fafbfc;}}
    .insight-item{{flex:1;min-width:120px;padding:14px 18px;border-right:1px solid {BD};text-align:center;}}
    .insight-item:last-child{{border-right:none;}}
    .insight-val{{display:block;font-size:22px;font-weight:700;color:{D};}}
    .insight-lbl{{display:block;font-size:11px;color:{G};margin-top:4px;text-transform:uppercase;letter-spacing:.04em;}}
    .block{{margin-top:32px;}}
    .block-title{{font-size:15px;font-weight:700;color:{D};margin:0 0 4px;padding-bottom:10px;
                  border-bottom:1px solid {BD};}}
    .section-sub{{font-size:13px;color:{G};margin:0 0 14px;}}
    .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:24px 0;}}
    @media(max-width:800px){{.grid-2{{grid-template-columns:1fr;}}}}
    .glossary-table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px;}}
    .glossary-table th,.glossary-table td{{text-align:left;padding:10px 12px;border-bottom:1px solid {BD};}}
    .glossary-table th{{font-size:11px;color:{G};text-transform:uppercase;letter-spacing:.04em;}}
    .glossary-table td:first-child{{font-weight:600;color:{D};width:28%;}}
    .table-wrap{{overflow-x:auto;border:1px solid {BD};border-radius:6px;}}
    .data-table{{width:100%;border-collapse:collapse;font-size:13px;}}
    .data-table thead{{background:#f8fafc;}}
    .data-table th{{text-align:left;padding:11px 16px;font-size:11px;font-weight:700;color:{G};
                    text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid {BD};}}
    .data-table td{{padding:11px 16px;border-bottom:1px solid #f1f5f9;vertical-align:top;}}
    .data-table tbody tr:last-child td{{border-bottom:none;}}
    .data-table tbody tr:hover{{background:#f8fafc;}}
    .cell-mono{{font-family:Consolas,"Courier New",monospace;font-size:12px;color:{D};}}
    .cell-text{{max-width:280px;}}
    .table-foot{{font-size:12px;color:{G};padding:10px 4px;}}
    .btn-more{{background:transparent;border:1px solid {P};color:{P};border-radius:4px;padding:4px 12px;
               font-size:12px;font-weight:600;cursor:pointer;margin-left:8px;}}
    .btn-more:hover{{background:{P};color:#fff;}}
    .badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}}
    .badge-sim{{background:#ecfdf5;color:#047857;}}
    .badge-nao{{background:#fef2f2;color:#b91c1c;}}
    .badge-inferida{{background:#fffbeb;color:#b45309;}}
    .badge-fn,.badge-fp{{background:#f1f5f9;color:#475569;}}
    .badge-muted{{color:{G};}}
    .recon-list{{display:flex;flex-direction:column;gap:12px;margin-bottom:28px;}}
    .recon-item{{border:1px solid {BD};border-radius:6px;padding:18px 20px;background:#fafbfc;}}
    .recon-top{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:6px;}}
    .recon-metric{{font-weight:700;color:{D};font-size:14px;}}
    .recon-num{{font-size:24px;font-weight:700;color:{P};}}
    .recon-meta{{font-size:11px;color:{G};text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px;}}
    .recon-text{{font-size:13px;color:#475569;margin:0 0 6px;line-height:1.5;}}
    .recon-diff{{font-size:12px;color:{G};margin:0;font-style:italic;line-height:1.45;}}
    .empty-state{{text-align:center;padding:40px;color:{G};font-size:14px;border:1px dashed {BD};
                  border-radius:6px;background:#fafbfc;}}
    .filter-bar{{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:24px;padding-bottom:16px;
                 border-bottom:1px solid {BD};}}
    .filter-label{{font-size:12px;color:{G};font-weight:600;margin-right:4px;}}
    .filter-btn{{background:transparent;border:1px solid transparent;border-radius:4px;padding:5px 12px;
                 font-size:12px;font-weight:600;color:#475569;cursor:pointer;}}
    .filter-btn:hover{{background:#f1f5f9;}}
    .filter-btn.active{{background:{P};color:#fff;border-color:{P};}}
    .filter-meta{{margin-left:auto;font-size:12px;color:{G};}}
    .month-title{{font-size:13px;font-weight:700;color:{D};margin:28px 0 10px;text-transform:uppercase;
                  letter-spacing:.06em;}}
    .tl-month:first-child .month-title{{margin-top:0;}}
    .tl-table .col-date{{width:100px;white-space:nowrap;color:{G};font-size:12px;}}
    .tl-table .col-cat{{width:140px;}}
    .tl-table .col-ref{{width:160px;}}
    .cat{{font-size:11px;font-weight:600;padding:2px 8px;border-radius:3px;background:#f1f5f9;color:#475569;}}
    .cat-na{{background:#eff6ff;color:#1d4ed8;}}
    .cat-fg{{background:#faf5ff;color:#7e22ce;}}
    .cat-contestacao{{background:#ecfdf5;color:#047857;}}
    .cat-treinamento{{background:#f5f3ff;color:#6d28d9;}}
    .tl-row.hidden{{display:none;}}
    .tl-month.hidden{{display:none;}}
    .footer{{text-align:center;color:{G};font-size:12px;margin-top:28px;padding-top:16px;
             border-top:1px solid {BD};}}
    """


def _base_css_with_analytics(extra: str = "") -> str:
    return _base_css() + (extra or "")


def _page_scripts() -> str:
    return """
function expandTable(tid) {
  var wrap = document.getElementById('wrap-' + tid);
  var tbl = document.getElementById(tid);
  if (!tbl) return;
  var rows = tbl.querySelectorAll('tr.extra-row');
  rows.forEach(function(r) { r.style.display = ''; });
  var btn = wrap ? wrap.querySelector('.btn-more') : null;
  if (btn) btn.style.display = 'none';
  var foot = wrap ? wrap.querySelector('.table-foot') : null;
  if (foot && wrap) {
    var total = wrap.getAttribute('data-total');
    if (total) foot.textContent = 'Exibindo ' + total + ' registro(s)';
  }
}
function filterTimeline(cat, btn) {
  document.querySelectorAll('.filter-btn').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  document.querySelectorAll('.tl-row').forEach(function(el) {
    var show = cat === 'all' || el.getAttribute('data-categoria') === cat;
    el.classList.toggle('hidden', !show);
  });
  document.querySelectorAll('.tl-month').forEach(function(m) {
    var visible = m.querySelectorAll('.tl-row:not(.hidden)').length > 0;
    m.classList.toggle('hidden', !visible);
  });
}
function setMode(mode) {
  document.querySelectorAll('.mode-btn').forEach(function(b) {
    b.classList.toggle('active', b.getAttribute('data-mode') === mode);
  });
  document.querySelectorAll('.nav-tab-analytic').forEach(function(a) {
    a.classList.toggle('hidden', mode === 'executivo');
  });
  if (mode === 'executivo') {
    var active = document.querySelector('.tab-panel.active');
    if (active && (active.id === 'panel-capacitacao' || active.id === 'panel-base')) {
      setTab('resumo');
    }
  }
}
function setTab(which) {
  document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
  var panel = document.getElementById('panel-' + which);
  if (panel) panel.classList.add('active');
  document.querySelectorAll('.nav a').forEach(function(a) { a.classList.remove('active'); });
  var tab = document.getElementById('tab-' + which);
  if (tab) tab.classList.add('active');
  window.scrollTo({top: 0, behavior: 'smooth'});
}
"""


def _build_pages(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    periodo_label: str,
    metrics_full: BRBMetrics | None,
    analytics: BRBAnalytics | None = None,
) -> dict[str, str]:
    if analytics is None:
        analytics = compute_analytics(bundle)
    from report_brb.brb_render_analytics import _tab_qualidade
    from report_brb.brb_render_executive import EXEC_TAB_IDS, build_executive_pages

    def _qualidade_tab(analytics_obj, df_table_fn, section_fn, chart_frame_fn):
        return _tab_qualidade(analytics_obj, df_table_fn, section_fn, lambda k: "", chart_frame_fn)

    helpers = {
        "df_table": _df_table,
        "section": _section,
        "grid2": _grid2,
        "chart_frame": _chart_frame,
        "fg_tab": _build_tab_fg_body,
        "cap_tab": _build_tab_capacitacao,
        "numeros_tab": _build_tab_numeros_body,
        "qualidade_tab": _qualidade_tab,
        "timeline_tab": _build_tab_timeline,
        "na_tab": _build_tab_na,
        "proc_tab": _build_tab_procedencia,
        "glossary": glossary_html,
    }
    pages = build_executive_pages(
        bundle, metrics, analytics, periodo_label, metrics_full, helpers
    )
    pages["_tab_ids"] = EXEC_TAB_IDS
    return pages


def _hero_html(periodo_label: str, gerado: str) -> str:
    nome = CLIENT["nome"]
    return f"""<header class="hero">
    <div class="hero-main">
      <div class="hero-eyebrow">Report Executivo · {html_lib.escape(CLIENT["nome_curto"])}</div>
      <h1>Falhas e Notificação Ativa</h1>
      <div class="hero-period">Período analisado: <b>{_esc(periodo_label)}</b></div>
    </div>
    <div class="hero-date">Gerado em {gerado}</div>
  </header>"""


def _recon_cards(
    metrics: BRBMetrics,
    bundle: BRBDataBundle,
    periodo_label: str,
    metrics_full: BRBMetrics | None,
) -> str:
    rows = build_reconciliation(metrics, bundle, metrics_full, periodo_label)
    return "".join(
        f'<div class="recon-item">'
        f'<div class="recon-top"><span class="recon-metric">{_esc(r.metrica)}</span>'
        f'<span class="recon-num">{_esc(r.valor)}</span></div>'
        f'<div class="recon-meta">{_esc(r.fonte)} · {_esc(r.unidade)}</div>'
        f'<p class="recon-text">{_esc(r.explicacao)}</p>'
        f'<p class="recon-diff">{_esc(r.relacionado)}</p></div>'
        for r in rows
    )


def render_html(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    periodo_label: str,
    out_path: Path,
    metrics_full: BRBMetrics | None = None,
    analytics: BRBAnalytics | None = None,
) -> Path:
    gerado = datetime.now().strftime("%d/%m/%Y")
    nome = CLIENT["nome"]
    if analytics is None:
        analytics = compute_analytics(bundle)
    pages = _build_pages(bundle, metrics, periodo_label, metrics_full, analytics)
    extra_css = pages.pop("_extra_css", "")
    tab_ids = pages.pop("_tab_ids")

    mode_toggle = (
        '<div class="mode-toggle">'
        '<button type="button" class="mode-btn active" data-mode="completo" onclick="setMode(\'completo\')">'
        "Todas as abas</button>"
        '<button type="button" class="mode-btn" data-mode="executivo" onclick="setMode(\'executivo\')">'
        "Modo Executivo</button></div>"
    )
    tab_links = "".join(
        f'<a id="tab-{tid}" href="#{tid}" onclick="setTab(\'{tid}\');return false;"'
        f' class="{"active" if tid == "resumo" else ""}'
        f'{" nav-tab-analytic" if tid in ("capacitacao", "base") else ""}">{label}</a>'
        for tid, label in tab_ids
    )
    panels = "".join(
        f'<div id="panel-{tid}" class="tab-panel{" active" if tid == "resumo" else ""}">{pages[tid]}</div>'
        for tid, _ in tab_ids
    )

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Relatório Executivo {html_lib.escape(nome)} — {html_lib.escape(periodo_label)}</title>
<style>{_base_css_with_analytics(extra_css)}</style>
</head>
<body>
<div class="wrap">
  {_hero_html(periodo_label, gerado)}
  {mode_toggle}
  <nav class="nav">{tab_links}</nav>
  <main class="panel">{panels}</main>
  <footer class="footer">Confidencial · Uso interno · {html_lib.escape(nome)}</footer>
</div>
<script>
{_page_scripts()}
(function() {{
  var hash = (window.location.hash || '').replace('#', '').trim();
  if (hash && document.getElementById('panel-' + hash)) setTab(hash);
}})();
</script>
</body>
</html>"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_html_email(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    periodo_label: str,
    out_path: Path,
    metrics_full: BRBMetrics | None = None,
) -> Path:
    """Versão flat sem abas/JS — compatível com Outlook."""
    gerado = datetime.now().strftime("%d/%m/%Y")
    nome = CLIENT["nome"]
    donut, funnel = _visao_charts(metrics)
    recon = _recon_cards(metrics, bundle, periodo_label, metrics_full)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Relatório BRB (e-mail) — {html_lib.escape(periodo_label)}</title>
<style>{_base_css()}
    .email-section{{margin-top:36px;padding-top:24px;border-top:1px solid {C["border"]};}}
    .email-note{{font-size:12px;color:{C["gray"]};margin-top:24px;}}
</style>
</head>
<body>
<div class="wrap">
  {_hero_html(periodo_label, gerado)}
  <main class="panel" style="border-radius:8px;margin-top:20px;">
    <p class="lead">Resumo executivo BRB — versão para e-mail. Tabelas detalhadas estão no relatório completo no navegador.</p>
    {_kpi_strip(metrics)}
    {_grid2(donut, funnel)}
    <div class="email-section">
      <h3 class="block-title">Entenda os números</h3>
      <div class="callout callout-warn">Os valores abaixo <b>não devem ser somados</b> entre si.</div>
      {_flow_strip(metrics)}
      <div class="recon-list">{recon}</div>
    </div>
    <p class="email-note">Para abas detalhadas (NA, Falhas BRB, Procedência, Capacitação, Linha do tempo), abra o arquivo HTML completo no navegador.</p>
  </main>
  <footer class="footer">Confidencial · Uso interno · {html_lib.escape(nome)}</footer>
</div>
</body>
</html>"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
