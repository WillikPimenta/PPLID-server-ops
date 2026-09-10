# -*- coding: utf-8 -*-
"""
Relatório HTML padrão cliente — visão CS enxuta.
Estrutura: sumário → procedência → procedentes → linha do tempo → conclusão → glossário.
"""
from __future__ import annotations

import calendar
import html as html_lib
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from report_brb.brb_analytics import BRBAnalytics
from report_brb.brb_charts import chart_barh, chart_bars, chart_donut, img_tag
from report_brb.brb_filters import classificar_fn_fp, na_effective_date, safe_str
from report_brb.brb_format import clean_text, format_date_br, format_int_br, mes_label_pt
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics
from report_brb.brb_normalize import padronizar_descricao
from report_brb.brb_timeline import build_timeline
from report_brb.config_brb import CLIENT

C = CLIENT["cores"]
_CLIENT_SHORT = CLIENT["nome_curto"]

# Texto padronizado — destaque de volume (não afirma causa).
_TEXTO_DESTAQUE_VOLUME = (
    "O volume do mês destacado difere dos demais meses; as bases apresentadas "
    "não permitem determinar a causa dessa variação."
)

_TEXTO_ESCOPO_PLANEJAMENTO = (
    "Consolida dados de Qualidade, Auditoria, Notificação Ativa e Contestação "
    "para leitura descritiva do período. Ações, prioridades, responsáveis, prazos "
    "e metas cabem às áreas gestoras."
)

_TEXTO_RAZAO_BASES = "Comparação entre bases distintas — não é taxa de erro."

_TEXTO_CAUSALIDADE_TIMELINE = (
    "Volumes de etapas diferentes; proximidade no tempo não implica causa."
)

_TEXTO_DEDUP_PROTOCOLOS = (
    "A soma mensal de protocolos distintos pode superar o total do período "
    "(o mesmo protocolo em mais de um mês). O total do período é deduplicado."
)

_TEXTO_COMUNICACAO_CLIENTE = (
    f"Falhas notificadas ao CS não confirmam, por si só, a comunicação ao {_CLIENT_SHORT}."
)

_SECTION_NAV: list[tuple[str, str]] = [
    ("sumario", "Sumário"),
    ("procedencia", "Procedência"),
    ("detalhe", "Procedentes"),
    ("auditados", "Auditados"),
    ("timeline", "Linha do tempo"),
    ("conclusao", "Síntese"),
    ("glossario", "Glossário"),
]

_TL_LABELS = {
    "demanda": "Abertura de demanda",
    "na": "Notificação Ativa",
    "contestacao": "Contestação / retorno",
    "treinamento": "Capacitação",
    "fg": "Auditoria FG",
}

_TL_SUMMARY_META: list[tuple[str, str, str]] = [
    ("demanda", "Solicitações na linha do tempo", "Eventos de abertura de solicitação exibidos"),
    ("na", "Notificações na linha do tempo", "Eventos de Notificação Ativa exibidos"),
    ("fg", "Auditorias FG na linha do tempo", "Eventos de auditoria exibidos"),
    ("contestacao", "Contestações na linha do tempo", "Eventos de avaliação ou retorno exibidos"),
    ("treinamento", "Capacitações na linha do tempo", "Eventos de capacitação exibidos"),
]


def _esc(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return html_lib.escape(str(x))


def _pct(n: int, total: int) -> float:
    return round(100 * n / total, 1) if total else 0.0


def _fmt_pct(n: int, total: int) -> str:
    return f"{_pct(n, total)}%"


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def _is_partial_month(data_fim) -> bool:
    """True quando a data final do período não é o último dia do mês."""
    d = _as_date(data_fim)
    if d is None:
        return False
    last = calendar.monthrange(d.year, d.month)[1]
    return d.day != last


def _partial_month_note(data_fim) -> str:
    """Nota padronizada de mês parcial (vazio se o mês estiver completo)."""
    d = _as_date(data_fim)
    if d is None or not _is_partial_month(d):
        return ""
    label = mes_label_pt(pd.Timestamp(d))
    fim_br = d.strftime("%d/%m/%Y")
    return (
        f"{label} considera dados até {fim_br} e não deve ser comparado "
        "diretamente com meses completos sem essa ressalva."
    )


def _partial_month_ym(data_fim) -> str | None:
    """Retorna 'YYYY-MM' do mês parcial, se aplicável."""
    d = _as_date(data_fim)
    if d is None or not _is_partial_month(d):
        return None
    return f"{d.year:04d}-{d.month:02d}"


def _falhas_na_lane_label() -> str:
    return "Falhas notificadas (Qualidade → CS)"


def _summary_card(label: str, value, sub: str = "", accent: str = "") -> str:
    ac = f" accent-{accent}" if accent else ""
    sub_html = f'<span class="metric-sub">{_esc(sub)}</span>' if sub else ""
    return (
        f'<div class="summary-card{ac}">'
        f'<span class="metric-main">{_esc(value)}</span>'
        f'<span class="metric-lbl">{_esc(label)}</span>{sub_html}</div>'
    )


def _is_numeric_cell(val) -> bool:
    s = str(val).strip().replace(",", ".")
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _tipo_badge_html(valor) -> str:
    """Badge discreto para tipo de procedência — preserva o texto original."""
    raw = safe_str(valor).strip()
    if not raw or raw.lower() in ("nan", "none", "—", "-"):
        return _esc(raw) if raw else "—"
    base = raw.rstrip("*").strip()
    key = {
        "manual": "manual",
        "automatico": "auto",
        "automático": "auto",
        "processual": "proc",
        "nao e falha": "ok",
        "não é falha": "ok",
        "nao classificado": "nc",
        "não classificado": "nc",
    }.get(base.lower(), "nc")
    star = "*" if raw.endswith("*") else ""
    star_html = f'<span class="tipo-badge-star">{star}</span>' if star else ""
    return (
        f'<span class="tipo-badge tipo-badge-{key}">{_esc(base)}</span>'
        f"{star_html}"
    )


def _simple_table(
    headers: list[str],
    rows: list[list],
    *,
    numeric_cols: set[int] | None = None,
    badge_cols: set[int] | None = None,
    html_cols: set[int] | None = None,
) -> str:
    if not rows:
        return '<p class="na">Não disponível para o período.</p>'
    th_parts = []
    for i, h in enumerate(headers):
        cls = ' class="num"' if numeric_cols and i in numeric_cols else ""
        th_parts.append(f"<th{cls}>{_esc(h)}</th>")
    th = "".join(th_parts)
    body = ""
    for row in rows:
        cells = []
        for i, c in enumerate(row):
            cls = []
            if numeric_cols is not None:
                is_num = i in numeric_cols
            else:
                is_num = _is_numeric_cell(c) and i > 0
            if is_num:
                cls.append("num")
            class_attr = f' class="{" ".join(cls)}"' if cls else ""
            if badge_cols and i in badge_cols:
                cells.append(f"<td{class_attr}>{_tipo_badge_html(c)}</td>")
            elif html_cols and i in html_cols:
                cells.append(f"<td{class_attr}>{c}</td>")
            else:
                cells.append(f'<td{class_attr} title="{_esc(c)}">{_esc(c)}</td>')
        body += "<tr>" + "".join(cells) + "</tr>"
    return (
        '<div class="table-wrap">'
        f'<table class="simple-table"><thead><tr>{th}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _section(num: str, title: str, body: str, anchor: str) -> str:
    return (
        f'<section class="report-section report-panel" id="{anchor}">'
        f'<h2 class="section-title"><span class="section-number">{num}</span> {_esc(title)}</h2>'
        f"{body}</section>"
    )


def _details_block(
    summary: str,
    body: str,
    label: str = "Ver detalhamento",
    count: int | None = None,
) -> str:
    count_html = (
        f' <span class="details-count">({count})</span>' if count is not None else ""
    )
    return (
        f'<div class="section-exec">{summary}</div>'
        f'<details class="section-details">'
        f'<summary><span class="details-chevron" aria-hidden="true"></span>'
        f'<span class="details-label">{_esc(label)}</span>{count_html}</summary>'
        f'<div class="details-body">{body}</div>'
        f"</details>"
    )


def _kpi_line(label: str, value_html: str) -> str:
    return f'<li><span class="kpi-label">{_esc(label)}:</span> {value_html}</li>'


def _kpi_card(
    value,
    label: str,
    sub: str = "",
    *,
    accent: str = "info",
    note: str = "",
) -> str:
    if isinstance(value, bool):
        display = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if float(value).is_integer():
                display = format_int_br(int(value))
            else:
                display = value
        except (TypeError, ValueError, OverflowError):
            display = value
    else:
        display = value
    sub_html = f'<span class="kpi-card-sub">{_esc(sub)}</span>' if sub else ""
    note_html = f'<span class="kpi-card-note">{_esc(note)}</span>' if note else ""
    return (
        f'<article class="kpi-card kpi-card-{accent}">'
        f'<span class="kpi-card-value">{_esc(display)}</span>'
        f'<span class="kpi-card-label">{_esc(label)}</span>'
        f"{sub_html}{note_html}</article>"
    )

def _na_falha_protocol_stats(na: pd.DataFrame) -> tuple[int, int, int]:
    """Protocolos distintos, protocolos com mais de uma falha, linhas extras por multi-etapa."""
    if na.empty or "_protocolo_norm" not in na.columns:
        return 0, 0, 0
    counts = na.groupby("_protocolo_norm").size()
    multi = counts[counts > 1]
    multi_n = len(multi)
    extra = int(multi.sum() - multi_n) if multi_n else 0
    return int(counts.shape[0]), multi_n, extra


def _chart_card(inner: str, caption: str = "", lead: str = "") -> str:
    lead_html = f'<p class="chart-lead">{lead}</p>' if lead else ""
    cap = f'<p class="chart-caption">{caption}</p>' if caption else ""
    hint = (
        '<p class="chart-expand-hint">Clique no gráfico para ampliar</p>'
        if "chart-img" in inner
        else ""
    )
    return f'<div class="chart-card">{lead_html}{hint}{inner}{cap}</div>'


def _chart_lightbox_html() -> str:
    return (
        '<div id="chart-lightbox" class="chart-lightbox" role="dialog" '
        'aria-modal="true" aria-label="Gráfico ampliado" hidden>'
        '<button type="button" class="chart-lightbox-close" aria-label="Fechar ampliação">×</button>'
        '<img class="chart-lightbox-img" alt=""/>'
        "</div>"
    )


def _qi_norm(val) -> str:
    s = safe_str(val).strip()
    if not s or s.lower() in ("nan", "none", "—"):
        return ""
    return s


def _period_key(dt) -> pd.Period | None:
    ts = pd.to_datetime(dt, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).to_period("M")


def _build_cs_timeline_months(bundle: BRBDataBundle) -> list[dict]:
    """Timeline CS por mês: solicitações (QI), NA e Contestação em faixas."""
    by_month: dict[pd.Period, dict] = {}

    def _bucket(ym: pd.Period) -> dict:
        if ym not in by_month:
            by_month[ym] = {
                "ym": ym,
                "label": mes_label_pt(ym.to_timestamp()),
                "qis": [],
                "proto_sum": 0,
                "na_falhas": 0,
                "na_protos": 0,
                "na_top_motivo": "",
                "cont_total": 0,
                "cont_proc": 0,
                "cont_improc": 0,
            }
        return by_month[ym]

    dem = bundle.na_demandas
    if not dem.empty:
        for _, row in dem.iterrows():
            qi = _qi_norm(row.get("Demanda", ""))
            if not qi:
                continue
            ym = _period_key(row.get("Data da Abertura"))
            if ym is None:
                continue
            qtd = row.get("Quantidade de Protolocos", 0)
            try:
                qtd_n = int(float(qtd)) if pd.notna(qtd) else 0
            except (TypeError, ValueError):
                qtd_n = 0
            situ = _status_label(safe_str(row.get("Situação", "")))
            b = _bucket(ym)
            b["qis"].append({"qi": qi, "protos": qtd_n, "status": situ})
            b["proto_sum"] += qtd_n

    na = bundle.na_falhas
    if not na.empty:
        proto_col = "_protocolo_norm" if "_protocolo_norm" in na.columns else "PROTOCOLO"
        work = na.copy()
        work["_ym"] = na_effective_date(work).map(_period_key)
        for ym, grp in work.dropna(subset=["_ym"]).groupby("_ym", sort=True):
            b = _bucket(ym)
            b["na_falhas"] = len(grp)
            b["na_protos"] = (
                int(grp[proto_col].astype(str).nunique()) if proto_col in grp.columns else len(grp)
            )
            if "MOTIVO DA FALHA" in grp.columns:
                vc = grp["MOTIVO DA FALHA"].astype(str).value_counts()
                if not vc.empty:
                    b["na_top_motivo"] = clean_text(vc.index[0], 52)

    cont = bundle.contestacao
    if not cont.empty and "Data" in cont.columns:
        c = cont.copy()
        c["_ym"] = c["Data"].map(_period_key)
        for ym, grp in c.dropna(subset=["_ym"]).groupby("_ym", sort=True):
            b = _bucket(ym)
            b["cont_total"] = len(grp)
            if "classificacao_conforme" in grp.columns:
                b["cont_proc"] = int((grp["classificacao_conforme"] == "falha").sum())
                b["cont_improc"] = int((grp["classificacao_conforme"] == "nao_falha").sum())

    return [by_month[k] for k in sorted(by_month.keys())]


def _render_cs_timeline_months(
    months: list[dict],
    data_fim=None,
) -> str:
    if not months:
        return '<p class="na">Sem eventos para montar a linha do tempo no período.</p>'

    # Destaque de volume (falhas >= média + 1 desvio e máximo do período)
    falhas_vals = [int(m["na_falhas"] or 0) for m in months]
    mean_f = sum(falhas_vals) / len(falhas_vals) if falhas_vals else 0
    var_f = (
        sum((v - mean_f) ** 2 for v in falhas_vals) / len(falhas_vals)
        if falhas_vals
        else 0
    )
    std_f = var_f ** 0.5
    thr = mean_f + std_f if std_f > 0 else (max(falhas_vals) if falhas_vals else 0)
    max_f = max(falhas_vals) if falhas_vals else 0
    partial_ym = _partial_month_ym(data_fim)

    blocks = []
    for m in months:
        lanes = []

        # Lane 1: Notificação Ativa = QI / solicitações
        if m["qis"]:
            chips = "".join(
                f'<span class="tl-qi-chip">{_esc(q["qi"])}'
                f' <small>{_esc(q["protos"])} prot.</small></span>'
                for q in m["qis"]
            )
            lanes.append(
                f'<div class="tl-lane tl-lane-demanda">'
                f'<div class="tl-lane-head">Notificação Ativa</div>'
                f'<p class="tl-lane-sub">Solicitações Qualidade → CS (pacote NA)</p>'
                f'<div class="tl-lane-stats">'
                f'<span class="tl-chip"><b>{len(m["qis"])}</b> QI</span>'
                f'<span class="tl-chip"><b>{m["proto_sum"]}</b> protocolos informados</span>'
                f"</div>"
                f'<div class="tl-qi-list">{chips}</div>'
                f"</div>"
            )
        else:
            lanes.append(
                '<div class="tl-lane tl-lane-empty">'
                '<div class="tl-lane-head">Notificação Ativa</div>'
                '<p class="tl-lane-sub">Solicitações Qualidade → CS (pacote NA)</p>'
                '<p class="tl-lane-muted">Nenhuma abertura neste mês</p></div>'
            )

        # Lane 2: Falhas notificadas Qualidade → CS
        falhas_lbl = _falhas_na_lane_label()
        falhas_sub = (
            "Registros de falhas notificados pela Qualidade ao CS. "
            "Não confirma, por si só, a comunicação ao cliente."
        )
        if m["na_falhas"]:
            motivo = (
                f'<p class="tl-lane-meta">Motivo mais frequente: {_esc(m["na_top_motivo"])}</p>'
                if m.get("na_top_motivo")
                else ""
            )
            lanes.append(
                f'<div class="tl-lane tl-lane-na">'
                f'<div class="tl-lane-head">{_esc(falhas_lbl)}</div>'
                f'<p class="tl-lane-sub">{_esc(falhas_sub)}</p>'
                f'<div class="tl-lane-stats">'
                f'<span class="tl-chip"><b>{m["na_falhas"]}</b> falhas</span>'
                f'<span class="tl-chip"><b>{m["na_protos"]}</b> protocolos</span>'
                f"</div>{motivo}</div>"
            )
        else:
            lanes.append(
                f'<div class="tl-lane tl-lane-empty">'
                f'<div class="tl-lane-head">{_esc(falhas_lbl)}</div>'
                f'<p class="tl-lane-sub">{_esc(falhas_sub)}</p>'
                '<p class="tl-lane-muted">Sem falhas registradas neste mês</p></div>'
            )

        # Lane 3: Contestação
        if m["cont_total"]:
            lanes.append(
                f'<div class="tl-lane tl-lane-cont">'
                f'<div class="tl-lane-head">Contestação</div>'
                f'<div class="tl-lane-stats">'
                f'<span class="tl-chip"><b>{m["cont_total"]}</b> avaliações</span>'
                f'<span class="tl-chip"><b>{m["cont_proc"]}</b> procedentes</span>'
                f'<span class="tl-chip"><b>{m["cont_improc"]}</b> improcedentes</span>'
                f"</div></div>"
            )
        else:
            lanes.append(
                '<div class="tl-lane tl-lane-empty">'
                '<div class="tl-lane-head">Contestação</div>'
                '<p class="tl-lane-muted">Sem avaliações neste mês</p></div>'
            )

        n_falhas = int(m["na_falhas"] or 0)
        atypical = n_falhas >= thr and n_falhas == max_f and max_f > 0
        ym_key = str(m["ym"]) if m.get("ym") is not None else ""
        is_partial = bool(partial_ym and ym_key == partial_ym)
        atyp_cls = " tl-month-atypical" if atypical else ""
        badges = ""
        if atypical:
            badges += (
                '<span class="tl-atyp-badge" title="Destaque de volume — não afirma causa">'
                "Destaque de volume</span>"
            )
        if is_partial:
            badges += (
                '<span class="tl-atyp-badge" title="Mês parcial — data final não encerra o mês">'
                "Mês parcial</span>"
            )
        head_stats = (
            f'<span class="tl-head-stat"><b>{n_falhas}</b> falhas</span>'
            f'<span class="tl-head-stat"><b>{m["cont_total"]}</b> aval.</span>'
            f'<span class="tl-head-stat"><b>{m["cont_proc"]}</b> proc.</span>'
        )
        blocks.append(
            f'<details class="tl-month-block{atyp_cls}">'
            f'<summary class="tl-month-summary">'
            f'<span class="tl-month-summary-left">'
            f'<span class="details-chevron" aria-hidden="true"></span>'
            f'<h3 class="tl-month-label">{_esc(m["label"])}</h3>{badges}'
            f"</span>"
            f'<span class="tl-month-summary-stats">{head_stats}</span>'
            f"</summary>"
            f'<div class="tl-month-body"><div class="tl-lanes">{"".join(lanes)}</div></div>'
            f"</details>"
        )

    return f'<div class="tl-months">{"".join(blocks)}</div>'


def _timeline_compare_html(months: list[dict]) -> str:
    if not months:
        return ""
    max_v = max(
        max(int(m["na_falhas"] or 0), int(m["cont_total"] or 0),
            int(m["cont_proc"] or 0))
        for m in months
    ) or 1

    def _bar(val: int, kind: str) -> str:
        pct = max(2, round(100 * val / max_v)) if val else 0
        return (
            f'<span class="tl-cmp-bar tl-cmp-{kind}" style="width:{pct}%" '
            f'title="{val}"><span class="tl-cmp-bar-val">{val}</span></span>'
        )

    rows = []
    for m in months:
        rows.append(
            f'<div class="tl-cmp-row">'
            f'<div class="tl-cmp-label">{_esc(m["label"].split(" ")[0][:3])}</div>'
            f'<div class="tl-cmp-tracks">'
            f'<div class="tl-cmp-track">{_bar(int(m["na_falhas"] or 0), "falhas")}</div>'
            f'<div class="tl-cmp-track">{_bar(int(m["cont_total"] or 0), "aval")}</div>'
            f'<div class="tl-cmp-track">{_bar(int(m["cont_proc"] or 0), "proc")}</div>'
            f"</div></div>"
        )
    legend = (
        '<ul class="tl-legend" aria-label="Legenda da comparação mensal">'
        '<li><span class="tl-leg-swatch tl-cmp-falhas"></span> Falhas</li>'
        '<li><span class="tl-leg-swatch tl-cmp-aval"></span> Avaliações</li>'
        '<li><span class="tl-leg-swatch tl-cmp-proc"></span> Procedentes</li>'
        "</ul>"
    )
    return (
        '<div class="tl-compare">'
        '<h3 class="sub-h">Comparativo mensal</h3>'
        f"{legend}"
        f'<div class="tl-cmp-chart" role="img" '
        f'aria-label="Barras horizontais comparando falhas, avaliações '
        f'e procedentes por mês">'
        + "".join(rows)
        + "</div></div>"
    )


def _timeline_visual_section(bundle: BRBDataBundle, data_fim=None) -> str:
    """Linha do tempo CS: swimlanes por mês (NA/QI · falhas · Contestação)."""
    months = _build_cs_timeline_months(bundle)
    n_qi = sum(len(m["qis"]) for m in months)
    n_with_na = sum(1 for m in months if m["na_falhas"])
    n_with_cont = sum(1 for m in months if m["cont_total"])
    falhas_lbl = _falhas_na_lane_label()
    partial_note = _partial_month_note(data_fim)
    partial_html = (
        f'<p class="note callout"><b>Mês parcial:</b> {_esc(partial_note)}</p>'
        if partial_note
        else ""
    )
    method = (
        '<details class="section-details tl-method-details">'
        '<summary><span class="details-chevron" aria-hidden="true"></span>'
        '<span class="details-label">Como ler os números da linha do tempo</span></summary>'
        '<div class="details-body">'
        '<p class="note">'
        "Protocolos informados = volume do lote na QI. "
        f"{_esc(falhas_lbl)} = registros Qualidade → CS no mês "
        "(os números não precisam coincidir)."
        "</p>"
        f'<p class="note">{_esc(_TEXTO_CAUSALIDADE_TIMELINE)}</p>'
        f'<p class="note"><b>{len(months)}</b> mês(es) · '
        f"<b>{n_qi}</b> QI · "
        f"<b>{n_with_na}</b> com falhas · "
        f"<b>{n_with_cont}</b> com Contestação.</p>"
        '<ul class="tl-legend tl-legend-lanes" aria-label="Legenda das faixas">'
        '<li><span class="tl-leg-swatch tl-lane-demanda-sw"></span> Notificação Ativa</li>'
        f'<li><span class="tl-leg-swatch tl-lane-na-sw"></span> {_esc(falhas_lbl)}</li>'
        '<li><span class="tl-leg-swatch tl-lane-cont-sw"></span> Contestação</li>'
        "</ul>"
        "</div></details>"
    )
    return (
        '<p class="section-p">Visão mensal: <b>Notificação Ativa</b>, '
        "<b>falhas notificadas</b> e <b>contestação</b>. "
        "Expanda cada mês para o detalhe.</p>"
        + partial_html
        + _timeline_compare_html(months)
        + method
        + _render_cs_timeline_months(months, data_fim=data_fim)
    )


def _section_nav_html() -> str:
    buttons = "".join(
        f'<button type="button" class="section-nav-btn{" active" if anchor == "sumario" else ""}" '
        f'data-section="{anchor}" '
        f'aria-current="{"page" if anchor == "sumario" else "false"}">'
        f"{_esc(label)}</button>"
        for anchor, label in _SECTION_NAV
    )
    return (
        '<nav class="section-nav" aria-label="Navegação do relatório">'
        '<p class="section-nav-title">Consultar por seção</p>'
        f'<div class="section-nav-tabs" role="tablist">{buttons}</div>'
        "</nav>"
    )


def _section_nav_script() -> str:
    ids = [a for a, _ in _SECTION_NAV]
    ids_js = ", ".join(f'"{i}"' for i in ids)
    return f"""<script>
(function(){{
  var ids=[{ids_js}];
  var links=document.querySelectorAll(".section-nav-btn");
  var panels=document.querySelectorAll(".report-panel");
  var lb=document.getElementById("chart-lightbox");
  var lbImg=lb && lb.querySelector(".chart-lightbox-img");
  var lbClose=lb && lb.querySelector(".chart-lightbox-close");
  var lbTrigger=null;
  var focusableSel='a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

  function showPanel(id, opts){{
    opts=opts||{{}};
    if(ids.indexOf(id)<0) id="sumario";
    panels.forEach(function(el){{
      el.classList.toggle("is-visible", el.id===id);
    }});
    links.forEach(function(btn){{
      var on=btn.getAttribute("data-section")===id;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-current", on ? "page" : "false");
    }});
    try{{ history.replaceState(null, "", "#"+id); }}catch(e){{}}
    if(opts.scroll!==false){{
      var panel=document.getElementById(id);
      if(panel){{
        var nav=document.querySelector(".section-nav");
        var offset=(nav ? nav.offsetHeight : 0) + 12;
        var y=panel.getBoundingClientRect().top + window.pageYOffset - offset;
        var reduce=window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        window.scrollTo({{ top: Math.max(0, y), behavior: reduce ? "auto" : "smooth" }});
      }}
    }}
  }}
  links.forEach(function(btn){{
    btn.addEventListener("click", function(){{
      showPanel(btn.getAttribute("data-section"));
    }});
  }});
  var hash=(location.hash||"").replace(/^#/,"");
  if(hash && ids.indexOf(hash)>=0) showPanel(hash, {{scroll:true}});
  else showPanel("sumario", {{scroll:false}});

  function trapFocus(e){{
    if(!lb || lb.hidden || e.key!=="Tab") return;
    var nodes=lb.querySelectorAll(focusableSel);
    if(!nodes.length) return;
    var first=nodes[0], last=nodes[nodes.length-1];
    if(e.shiftKey && document.activeElement===first){{ e.preventDefault(); last.focus(); }}
    else if(!e.shiftKey && document.activeElement===last){{ e.preventDefault(); first.focus(); }}
  }}
  function openLb(src, alt, trigger){{
    if(!lb||!lbImg) return;
    lbTrigger=trigger||null;
    lbImg.src=src;
    lbImg.alt=alt||"Gráfico ampliado";
    lb.hidden=false;
    document.body.classList.add("lightbox-open");
    if(lbClose) lbClose.focus();
  }}
  function closeLb(){{
    if(!lb||!lbImg || lb.hidden) return;
    lb.hidden=true;
    lbImg.removeAttribute("src");
    document.body.classList.remove("lightbox-open");
    if(lbTrigger && typeof lbTrigger.focus==="function") lbTrigger.focus();
    lbTrigger=null;
  }}
  document.querySelectorAll(".chart-card img.chart-img").forEach(function(img){{
    img.setAttribute("title", "Clique para ampliar");
    img.setAttribute("tabindex", "0");
    img.setAttribute("role", "button");
    function go(){{ openLb(img.src, img.alt, img); }}
    img.addEventListener("click", go);
    img.addEventListener("keydown", function(e){{
      if(e.key==="Enter" || e.key===" "){{ e.preventDefault(); go(); }}
    }});
  }});
  if(lbClose) lbClose.addEventListener("click", closeLb);
  if(lb) lb.addEventListener("click", function(e){{ if(e.target===lb) closeLb(); }});
  document.addEventListener("keydown", function(e){{
    if(e.key==="Escape") closeLb();
    trapFocus(e);
  }});

  document.querySelectorAll('a[href^="#"]').forEach(function(a){{
    a.addEventListener("click", function(e){{
      var id=(a.getAttribute("href")||"").replace(/^#/,"");
      if(ids.indexOf(id)>=0){{ e.preventDefault(); showPanel(id); }}
    }});
  }});
}})();
</script>"""


def _executive_narrative(
    metrics: BRBMetrics,
    proc_df: pd.DataFrame,
    top_alert: str,
    fg_total: int,
    fg_perfil_df: pd.DataFrame | None = None,
    tipo_df: pd.DataFrame | None = None,
    analytics: BRBAnalytics | None = None,
) -> str:
    motivo = ""
    if not proc_df.empty:
        motivo = (
            f" Principal motivo nas procedentes: "
            f"<b>{_esc(proc_df.iloc[0]['Motivo'])}</b>."
        )
    text = (
        '<p class="executive-narrative">'
        f"No período, a Qualidade notificou ao CS <b>{metrics.na_falhas_registros}</b> registros "
        f"de falha (<b>{metrics.na_falhas_protocolos}</b> protocolos distintos), a partir de "
        f"<b>{metrics.demandas_na_registros}</b> solicitações. "
        f"O time de Contestação registrou <b>{metrics.conforme_nao}</b> procedentes "
        f"em <b>{metrics.conforme_sim + metrics.conforme_nao}</b> avaliações "
        f"(<b>{metrics.pct_procedente}%</b>).{motivo}"
        "</p>"
    )
    method_body = (
        f'<p class="note">{_esc(_TEXTO_ESCOPO_PLANEJAMENTO)}</p>'
        '<p class="section-p">'
        f"Em paralelo, foram registrados <b>{fg_total}</b> casos de falhas encontradas "
        "em auditorias."
        "</p>"
        + _bases_map()
        + _protocol_sequence_block(analytics)
    )
    method = (
        '<details class="section-details">'
        '<summary><span class="details-chevron" aria-hidden="true"></span>'
        '<span class="details-label">Metodologia e mapa das bases</span></summary>'
        f'<div class="details-body">{method_body}</div></details>'
    )
    return text + method


def _protocol_sequence_block(analytics: BRBAnalytics | None) -> str:
    """Ressalva curta: bases distintas (sem cruzamento numérico detalhado no cliente)."""
    if analytics is None:
        return ""
    r = getattr(analytics, "sequencia_resumo", None) or {}
    if not r or not r.get("n_protocolos"):
        return ""
    return (
        '<p class="note callout">'
        "<b>Ressalva:</b> auditoria, notificação e Contestação são bases distintas — "
        "os totais não formam um funil."
        "</p>"
    )


def _bases_map() -> str:
    rows = [
        [
            "Notificação Ativa (solicitações)",
            f"Solicitações abertas pelo Qualidade ao CS do cliente {_CLIENT_SHORT}.",
        ],
        [
            "Falhas notificadas (Qualidade → CS)",
            "Registros sinalizados pela Qualidade ao CS.",
        ],
        [
            "Falhas de auditoria (FG)",
            "Falhas encontradas em auditorias — visão paralela à contestação.",
        ],
        [
            "Contestação",
            f"Contestações do {_CLIENT_SHORT} avaliadas pelo time interno (CONFORME).",
        ],
        [
            "Universo auditado",
            "Protocolos distintos e registros/etapas revisados no período.",
        ],
    ]
    return (
        '<h4 class="sub-h bases-sub-h">Mapa das bases de dados</h4>'
        + _simple_table(["Base", "O que representa"], rows)
        + f'<p class="note">{_esc(_TEXTO_DEDUP_PROTOCOLOS)}</p>'
    )


def _tipo_fg_contestacao_bridge(fg_perfil_df: pd.DataFrame, tipo_df: pd.DataFrame) -> str:
    if fg_perfil_df.empty and tipo_df.empty:
        return ""
    fg_top = _esc(fg_perfil_df.iloc[0]["Tipo de falha"]) if not fg_perfil_df.empty else "N/D"
    cont_top = _esc(tipo_df.iloc[0]["Tipo de procedência"]) if not tipo_df.empty else "N/D"
    return (
        '<p class="note callout">'
        "<b>Auditoria vs Contestação:</b> "
        f"tipo predominante em auditoria <b>{fg_top}</b>; "
        f"nas procedentes <b>{cont_top}</b> — bases distintas."
        "</p>"
    )


def _cover_intro(metrics: BRBMetrics, proc_df: pd.DataFrame) -> str:
    motivo_txt = ""
    if not proc_df.empty:
        motivo_txt = (
            f" Destaque do período: foi observada concentração em "
            f"<b>{_esc(proc_df.iloc[0]['Motivo'])}</b> nas contestações procedentes."
        )
    return (
        f'<p class="cover-intro">Relatório executivo {_CLIENT_SHORT}: '
        "solicitações e falhas notificadas ao CS, auditorias e contestações."
        f"{motivo_txt}</p>"
        + '<ul class="cover-howto">'
        + "<li><b>Como ler:</b> Sumário → Procedência → Procedentes → Auditados → "
        "Linha do tempo → Síntese → Glossário.</li>"
        + "<li><b>Procedente</b> = CONFORME Não · "
        + "<b>Improcedente</b> = CONFORME Sim.</li>"
        + "</ul>"
    )


def _process_diagram_html() -> str:
    return (
        '<div class="process-box process-box-compact">'
        '<p class="flow-box-title">Fluxos do processo</p>'
        '<div class="process-parallel-flows">'
        '<div class="process-flow-col">'
        '<p class="process-flow-label">Notificação (Qualidade → CS)</p>'
        '<div class="process-steps" role="list">'
        '<div class="process-step" role="listitem"><span class="process-role">Qualidade</span>'
        "<span class=\"process-desc\">Abre solicitação e notifica falhas ao CS</span></div>"
        '<span class="flow-arrow" aria-hidden="true">→</span>'
        f'<div class="process-step" role="listitem"><span class="process-role">CS {_CLIENT_SHORT}</span>'
        "<span class=\"process-desc\">Recebe a notificação e trata o caso com o cliente</span></div>"
        "</div></div>"
        '<div class="process-flow-col">'
        '<p class="process-flow-label">Contestação (cliente → Qualidade)</p>'
        '<div class="process-steps" role="list">'
        f'<div class="process-step" role="listitem"><span class="process-role">{_CLIENT_SHORT}</span>'
        "<span class=\"process-desc\">Pode enviar contestações de casos identificados</span></div>"
        '<span class="flow-arrow" aria-hidden="true">→</span>'
        '<div class="process-step" role="listitem">'
        '<span class="process-role">Contestação (Qualidade)</span>'
        "<span class=\"process-desc\">Time interno — avalia procedência (CONFORME)</span></div>"
        "</div></div>"
        "</div>"
        '<p class="flow-note process-parallel">'
        "São canais distintos e podem ter casos relacionados. "
        "<b>Em paralelo:</b> falhas encontradas em auditorias (visão FG)."
        "</p>"
        "</div>"
    )


def _executive_kpi_grid(metrics: BRBMetrics) -> str:
    cards = [
        _kpi_card(
            metrics.na_falhas_registros,
            "Falhas notificadas",
            "Qualidade → CS (registros)",
            accent="info",
        ),
        _kpi_card(
            metrics.na_falhas_protocolos or "N/D",
            "Protocolos distintos",
            "Entre as falhas notificadas",
            accent="info",
        ),
        _kpi_card(
            metrics.conforme_nao,
            "Procedentes",
            f"{metrics.pct_procedente}% das avaliações",
            accent="alert",
        ),
        _kpi_card(
            metrics.conforme_sim,
            "Improcedentes",
            f"{metrics.pct_improcedente}% das avaliações",
            accent="ok",
        ),
    ]
    return '<div class="kpi-grid">' + "".join(cards) + "</div>"


def _executive_kpi_list(metrics: BRBMetrics, total_av: int) -> str:
    """KPIs secundários (contexto operacional) — usados em área expansível."""
    pct_atk = _fmt_pct(metrics.possivel_ataque_na, metrics.na_falhas_registros)
    has_atyp = (
        metrics.auditados_mensal is not None
        and not metrics.auditados_mensal.empty
        and metrics.auditados_mensal["atipico"].astype(bool).any()
    )
    razao_lbl = (
        "Indicador descritivo provisório"
        if has_atyp
        else "Razão descritiva entre bases"
    )
    lines = [
        _kpi_line(
            "Solicitações Qualidade → CS",
            f"<b>{metrics.demandas_na_registros}</b>",
        ),
        _kpi_line("Protocolos informados (volume agregado)", f"<b>{metrics.protocolos_na}</b>"),
        _kpi_line(
            "Falhas notificadas pela Qualidade ao CS (registros)",
            f"<b>{metrics.na_falhas_registros}</b>",
        ),
        _kpi_line(
            "Protocolos distintos entre as falhas notificadas",
            f"<b>{metrics.na_falhas_protocolos or 'N/D'}</b>",
        ),
        _kpi_line("Avaliações do time de Contestação", f"<b>{total_av or 'N/D'}</b>"),
        _kpi_line(
            "Procedentes (CONFORME = Não)",
            f"<b>{metrics.conforme_nao}</b> ({metrics.pct_procedente}% de {total_av or 0} avaliações)",
        ),
        _kpi_line(
            "Improcedentes (CONFORME = Sim)",
            f"<b>{metrics.conforme_sim}</b> ({metrics.pct_improcedente}% de {total_av or 0} avaliações)",
        ),
        _kpi_line(
            "Registros com termos predefinidos no texto do motivo "
            "(filtro automático — não confirma fraude, irregularidade ou ocorrência)",
            f"<b>{metrics.possivel_ataque_na}</b> ({pct_atk} de {metrics.na_falhas_registros} falhas notificadas)",
        ),
        _kpi_line(
            "Casos auditados (protocolos distintos)",
            f"<b>{metrics.auditados_casos}</b> "
            f"(<b>{metrics.auditados_registros}</b> registros/etapas · "
            f"{razao_lbl}: <b>{metrics.auditados_taxa_achado}%</b>)",
        ),
    ]
    return '<ul class="executive-kpi-list">' + "".join(lines) + "</ul>"

def _timeline_exec_bullets(timeline: pd.DataFrame, bundle: BRBDataBundle) -> str:
    bullets: list[str] = []
    cats: set[str] = set()
    if not timeline.empty and "categoria" in timeline.columns:
        cats = set(timeline["categoria"].dropna().astype(str))

    if not bundle.na_demandas.empty:
        qis: list[str] = []
        if "Demanda" in bundle.na_demandas.columns:
            qis = [
                q
                for q in bundle.na_demandas["Demanda"].dropna().astype(str).str.strip().unique()
                if q
            ]
        shown = qis[:3]
        suffix = "..." if len(qis) > 3 else ""
        qi_txt = f" ({', '.join(shown)}{suffix})" if shown else ""
        bullets.append(
            f"Solicitações abertas pelo Qualidade ao CS do cliente BRB{qi_txt}"
        )

    if "na" in cats:
        bullets.append(
            "Disparo de notificações ativas relacionadas à identificação de possível ataque"
        )
    if "fg" in cats:
        bullets.append(
            "Falhas encontradas em auditorias para validação dos casos identificados"
        )
    if "contestacao" in cats:
        bullets.append(
            "Avaliações de procedência pelo time de Contestação sobre contestações enviadas pelo BRB"
        )
    if "treinamento" in cats:
        bullets.append(
            "Capacitações direcionadas aos cenários recorrentes de análise documental"
        )

    if not bullets:
        return '<p class="na">Sem eventos registrados no período.</p>'

    items = "".join(f"<li>{_esc(b)}</li>" for b in bullets[:5])
    return (
        '<p class="section-p"><b>Principais ações do período:</b></p>'
        f'<ul class="exec-bullets">{items}</ul>'
    )


# Classificação de tipo usa APENAS a coluna Tipo de falha (não Tipo de Conclusão).
# SEM FALHA / vazio = não é falha (validar com CONFORME); COLABORADOR = Manual;
# AUTOMÁTICO / PROCESSUAL / REGRA DE NEGÓCIO / MAPEAMENTO = tipos padrão.
_TIPO_PROC_EXPLICIT_COLS = (
    "Tipo de falha",
    "Tipo da Falha",
    "Tipo de Falha",
    "Tipo de falha2",
)

_TIPO_PROC_SEM_FALHA = frozenset(
    {"SEM FALHA", "NAO E FALHA", "NAO FALHA", "N/A", "-", "NENHUM"}
)

_TIPO_PROC_LABELS = (
    "Manual",
    "Automático",
    "Processual",
    "Não é falha",
    "Não classificado",
)

_TIPO_PROC_INTERPRETACAO = {
    "Manual": "Falhas relacionadas à análise humana, checklist ou decisão operacional.",
    "Automático": "Falhas relacionadas à regra, automação, OCR, captura ou validação sistêmica.",
    "Processual": "Falhas relacionadas ao fluxo, etapa, política ou procedimento operacional.",
    "Não é falha": "Avaliação concluiu que não houve falha (CONFORME = Sim / Tipo de falha = SEM FALHA).",
    "Não classificado": "Casos sem informação suficiente para classificação.",
}


def _norm_tipo_token(valor) -> str:
    from report_brb.brb_normalize import normalize_text, strip_accents

    return strip_accents(normalize_text(valor, upper=True))


def _normalize_tipo_procedencia(valor) -> str:
    """Mapeia valor de Tipo de falha → Manual / Automático / Processual.

    SEM FALHA ou vazio → '' (não é falha; CONFORME deve confirmar).
    COLABORADOR → Manual.
    AUTOMÁTICO / REGRA DE NEGÓCIO → Automático.
    PROCESSUAL / MAPEAMENTO → Processual.
    """
    v = _norm_tipo_token(valor)
    if not v or v in _TIPO_PROC_SEM_FALHA:
        return ""
    if v in ("MANUAL", "COLABORADOR", "HUMANO", "OPERADOR"):
        return "Manual"
    if v in (
        "AUTOMATICO",
        "AUTO",
        "AUTOMACAO",
        "SISTEMICO",
        "SISTEMICA",
        "MOTOR",
        "REGRA DE NEGOCIO",
        "REGRA NEGOCIO",
    ):
        return "Automático"
    if v in (
        "PROCESSUAL",
        "PROCESSO",
        "FLUXO",
        "PROCEDIMENTO",
        "ETAPA",
        "MAPEAMENTO",
    ):
        return "Processual"
    return ""


def _classificar_tipo_procedencia(texto: str) -> str:
    from report_brb.brb_normalize import strip_accents

    blob = strip_accents(str(texto or "")).lower()
    if not blob.strip():
        return "Não classificado"
    # Prefixo "sinalização correta" não classifica a falha — usar o restante.
    if "sinalizacao correta" in blob:
        blob = blob.split("sinalizacao correta", 1)[-1]
        blob = blob.lstrip(" -–—:").strip()
        if not blob:
            return "Não classificado"

    manual_kw = (
        "formatacao",
        "fonte",
        "desalinhamento",
        "adulterad",
        "foto divergente",
        "selfie",
        "face",
        "documento adulterado",
        "nao identificado",
        "nao sinalizado",
        "colaborador",
    )
    auto_kw = (
        "ocr",
        "captura",
        "regra automatica",
        "automatico",
        "automacao",
        "tipificacao",
        "tipificad",
        "motor",
        "validacao sistemica",
        "cpf divergente",
        "leitura automatica",
    )
    proc_kw = (
        "etapa",
        "fluxo",
        "processo",
        "procedimento",
        "documento ausente",
        "politica",
        "aceite indevido",
        "documentacao obrigatoria",
        "etapa incorreta",
        "processual",
        "sinalizacao incorreta",
    )

    if any(k in blob for k in auto_kw):
        return "Automático"
    if any(k in blob for k in proc_kw):
        return "Processual"
    if any(k in blob for k in manual_kw):
        return "Manual"
    return "Não classificado"


def _cenario_norm_key(texto) -> str:
    from report_brb.brb_normalize import strip_accents, normalize_text

    return strip_accents(normalize_text(texto, upper=True))


def _is_sinalizacao_correta(texto) -> bool:
    return "SINALIZACAO CORRETA" in _cenario_norm_key(texto)


def _strip_sinalizacao_correta_prefix(texto: str) -> str:
    s = str(texto or "").strip()
    return re.sub(
        r"(?i)^sinaliza[cç][aã]o\s+correta\s*[-–—:]?\s*",
        "",
        s,
    ).strip() or s


def _resolve_cenario_classificacao(row: pd.Series, by_proto: dict) -> str:
    """
    Se o cenário for 'SINALIZAÇÃO CORRETA', pula e busca no mesmo protocolo
    um cenário classificável (NÃO SINALIZADO / SINALIZAÇÃO INCORRETA).
    """
    cen = row.get("Cenário") or row.get("Tipo de falha") or ""
    cen_s = str(cen).strip() if cen is not None else ""
    if not _is_sinalizacao_correta(cen_s):
        return cen_s

    from report_brb.brb_filters import norm_protocolo

    proto = ""
    if "_protocolo_norm" in row.index and str(row.get("_protocolo_norm") or "").strip():
        proto = str(row.get("_protocolo_norm")).strip()
    else:
        proto = norm_protocolo(row.get("Protocolo"))

    siblings = by_proto.get(proto, [])
    prefer_nao: list[str] = []
    prefer_inc: list[str] = []
    for s in siblings:
        if s == cen_s:
            continue
        k = _cenario_norm_key(s)
        if "NAO SINALIZADO" in k:
            prefer_nao.append(s)
        elif "SINALIZACAO INCORRETA" in k or "SINALIZACAO VALIDADA" in k:
            prefer_inc.append(s)
    if prefer_nao:
        return prefer_nao[0]
    if prefer_inc:
        return prefer_inc[0]
    return _strip_sinalizacao_correta_prefix(cen_s)


def _motivo_procedente_row(row: pd.Series) -> str:
    raw = row.get("_cenario_classif") or row.get("Cenário") or row.get("Tipo de falha") or ""
    return padronizar_descricao(str(raw)) or "Não informado na base"


def _texto_tipo_procedente_row(row: pd.Series) -> str:
    cen = row.get("_cenario_classif") or row.get("Cenário")
    parts = [
        cen,
        row.get("Tipo de falha"),
        row.get("Tipo de Falha"),
        row.get("Resultado da Análise"),
        row.get("Observação"),
    ]
    return " ".join(str(p) for p in parts if p is not None and str(p).strip())


def _tipo_procedencia_row(row: pd.Series) -> tuple[str, bool]:
    """Retorna (tipo, inferido) a partir de Tipo de falha.

    Prioridade: primeiro valor preenchido em Tipo de falha / Tipo de falha2.
    SEM FALHA → Não é falha.
    Vazio + CONFORME Sim (improcedente) → Não é falha (sem inferir Manual/Auto).
    Vazio + procedente → inferência por cenário/texto.
    Não usa Tipo de Conclusão de Análise.
    """
    for col in _TIPO_PROC_EXPLICIT_COLS:
        if col not in row.index:
            continue
        raw = _norm_tipo_token(row.get(col))
        if not raw:
            continue
        if raw in _TIPO_PROC_SEM_FALHA:
            return "Não é falha", False
        tipo = _normalize_tipo_procedencia(row.get(col))
        if tipo:
            return tipo, False
        return "Não classificado", False
    # Sem Tipo de falha preenchido
    if str(row.get("classificacao_conforme") or "").strip() == "nao_falha":
        return "Não é falha", False
    tipo = _classificar_tipo_procedencia(_texto_tipo_procedente_row(row))
    return tipo, True


def _apply_tipo_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    tipos = out.apply(_tipo_procedencia_row, axis=1)
    out["_tipo_procedencia"] = [t[0] for t in tipos]
    out["_tipo_inferido"] = [t[1] for t in tipos]
    return out


def _improcedentes_contestacao(cont: pd.DataFrame) -> pd.DataFrame:
    if cont.empty:
        return cont.copy()
    improc = cont[cont["classificacao_conforme"] == "nao_falha"].copy()
    return _apply_tipo_cols(improc)


def _group_contestacao_by_tipo(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    if df.empty:
        return pd.DataFrame(
            columns=["Tipo de procedência", "Casos", "Pct", "Interpretação"]
        ), False
    total = len(df)
    inferred = bool(df.get("_tipo_inferido", pd.Series(dtype=bool)).any())
    rows = []
    for tipo in _TIPO_PROC_LABELS:
        g = df[df["_tipo_procedencia"] == tipo]
        if tipo in ("Não classificado", "Não é falha") and g.empty:
            continue
        rows.append(
            {
                "Tipo de procedência": tipo,
                "Casos": len(g),
                "Pct": _fmt_pct(len(g), total),
                "Interpretação": _TIPO_PROC_INTERPRETACAO[tipo],
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("Casos", ascending=False).reset_index(drop=True)
    return out, inferred


def _na_volume_flow_box(
    metrics: BRBMetrics,
    multi_proto: int = 0,
    extra_rows: int = 0,
) -> str:
    multi_txt = ""
    if multi_proto and extra_rows:
        multi_txt = (
            f" · <b>{multi_proto}</b> protocolo(s) com mais de uma falha "
            f"(+{extra_rows} linha(s) por etapa/achado)"
        )
    return (
        '<div class="flow-box">'
        '<p class="flow-box-title">Como os números se relacionam</p>'
        '<div class="flow-steps">'
        f'<div class="flow-step"><span class="flow-n">{metrics.demandas_na_registros}</span>'
        '<span class="flow-lbl">Solicitações<br><small>Qualidade → CS</small></span></div>'
        '<span class="flow-arrow">→</span>'
        f'<div class="flow-step"><span class="flow-n">{metrics.protocolos_na}</span>'
        '<span class="flow-lbl">Protocolos informados<br><small>volume agregado</small></span></div>'
        '<span class="flow-arrow">→</span>'
        f'<div class="flow-step"><span class="flow-n">{metrics.na_falhas_registros}</span>'
        '<span class="flow-lbl">Falhas notificadas<br><small>Qualidade → CS</small></span></div>'
        '<span class="flow-arrow">→</span>'
        f'<div class="flow-step"><span class="flow-n">{metrics.na_falhas_protocolos or "N/D"}</span>'
        '<span class="flow-lbl">Protocolos distintos<br><small>entre as falhas</small></span></div>'
        "</div>"
        '<p class="flow-note">Um protocolo pode ter mais de uma falha notificada.'
        f"{multi_txt}</p>"
        "</div>"
    )


def _auditados_section(metrics: BRBMetrics, data_fim=None) -> str:
    """Seção Volumes auditados × falhas notificadas — relação descritiva entre bases."""
    mensal = metrics.auditados_mensal
    if mensal is None or mensal.empty:
        return (
            '<p class="section-p">Sem registros de casos auditados para o período.</p>'
        )

    has_atyp = mensal["atipico"].astype(bool).any() if "atipico" in mensal.columns else False
    razao_title = "Indicador provisório" if has_atyp else "Razão entre bases"
    razao_sub = "Falhas notificadas ÷ casos auditados"

    atyp = (
        mensal[mensal["atipico"].astype(bool)]
        if "atipico" in mensal.columns
        else mensal.iloc[0:0]
    )
    story_extra = ""
    if not atyp.empty:
        r = atyp.iloc[0]
        story_extra = (
            f" Destaque em <b>{_esc(r['label'])}</b>: "
            f"<b>{format_int_br(int(r['casos']))}</b> casos e "
            f"<b>{format_int_br(int(r['falhas']))}</b> falhas "
            f"(razão <b>{r['taxa_achado']}%</b>) — "
            f"{_esc(_TEXTO_DESTAQUE_VOLUME)}"
        )

    intro = (
        '<p class="section-p">'
        f"No período: <b>{format_int_br(metrics.auditados_casos)}</b> casos auditados e "
        f"<b>{format_int_br(metrics.na_falhas_registros)}</b> falhas notificadas "
        f"(Qualidade → CS) — razão <b>{metrics.auditados_taxa_achado}%</b>. "
        f"{_esc(_TEXTO_RAZAO_BASES)}"
        f"{story_extra}"
        "</p>"
    )

    kpis = (
        '<div class="kpi-grid kpi-grid-audit">'
        + _kpi_card(
            metrics.auditados_casos,
            "Casos auditados",
            "Protocolos distintos",
            accent="info",
        )
        + _kpi_card(
            metrics.auditados_registros,
            "Registros / etapas",
            "Linhas do universo auditado",
            accent="info",
        )
        + _kpi_card(
            metrics.na_falhas_registros,
            "Falhas notificadas",
            "Qualidade → CS no período",
            accent="alert",
        )
        + _kpi_card(
            f"{metrics.auditados_taxa_achado}%",
            razao_title,
            razao_sub,
            accent="warn",
        )
        + "</div>"
    )

    partial_note = _partial_month_note(data_fim)
    partial_html = (
        f'<p class="note callout"><b>Mês parcial:</b> {_esc(partial_note)}</p>'
        if partial_note
        else ""
    )

    partial_ym = _partial_month_ym(data_fim)
    rows = []
    for _, r in mensal.iterrows():
        ym_key = str(r["ym"]) if "ym" in r.index and r["ym"] is not None else ""
        label = str(r["label"])
        if r.get("atipico"):
            label += " ★"
        if partial_ym and ym_key == partial_ym:
            label += " (parcial)"
        rows.append(
            [
                label,
                format_int_br(int(r["casos"])),
                format_int_br(int(r["registros"])),
                format_int_br(int(r["falhas"])),
                f"{r['taxa_achado']}%",
            ]
        )
    table = _simple_table(
        [
            "Mês",
            "Casos auditados",
            "Registros",
            "Falhas notificadas",
            "Razão descritiva",
        ],
        rows,
        numeric_cols={1, 2, 3, 4},
    )

    charts = ""
    labels = [str(r["label"]).split(" ")[0][:3] for _, r in mensal.iterrows()]
    casos_vals = [int(r["casos"]) for _, r in mensal.iterrows()]
    falhas_vals = [int(r["falhas"]) for _, r in mensal.iterrows()]
    b64_casos = chart_bars(
        labels,
        casos_vals,
        "Casos auditados por mês (protocolos distintos)",
        color="#365f87",
    )
    b64_falhas = chart_bars(
        labels,
        falhas_vals,
        "Falhas notificadas por mês",
        color="#9f3f3c",
    )
    if b64_casos:
        charts += _chart_card(
            img_tag(
                b64_casos,
                "Casos auditados por mês: "
                + ", ".join(f"{a}={b}" for a, b in zip(labels, casos_vals)),
            ),
            "Protocolos distintos no universo auditado.",
        )
    if b64_falhas:
        charts += _chart_card(
            img_tag(
                b64_falhas,
                "Falhas notificadas por mês: "
                + ", ".join(f"{a}={b}" for a, b in zip(labels, falhas_vals)),
            ),
            "Falhas notificadas pela Qualidade ao CS.",
        )

    return (
        intro
        + kpis
        + partial_html
        + '<h3 class="sub-h">Comparativo mensal</h3>'
        + table
        + charts
    )


def _glossary_panel_html() -> str:
    items = [
        ("NA", "Notificação Ativa — canal de solicitações e falhas notificadas pela Qualidade ao CS."),
        ("FG", "Falhas Gerais — falhas encontradas em auditorias (visão paralela à contestação)."),
        ("CS", f"Customer Success — time responsável pelo cliente {_CLIENT_SHORT}."),
        (
            "Contestação",
            "Time interno de Qualidade que avalia as contestações enviadas pelo cliente "
            "(campo CONFORME: procedente / improcedente).",
        ),
        ("QI", "Número da solicitação aberta pelo Qualidade (ex.: QI-8018)."),
        (
            "CONFORME",
            "Campo da Contestação: Não = falha confirmada (procedente); "
            "Sim = análise conforme (improcedente).",
        ),
        ("Procedente", "CONFORME = Não — o time de Contestação confirmou a falha."),
        ("Improcedente", "CONFORME = Sim — análise considerada conforme."),
        (
            "Protocolos vs falhas",
            "Protocolos informados = volume agregado nas solicitações; "
            "falhas notificadas = registros sinalizados pela Qualidade ao CS; "
            "um protocolo pode ter mais de uma falha.",
        ),
        (
            "Auditoria vs Contestação",
            "FG mede falhas encontradas em auditorias; Contestação (time interno de Qualidade) "
            "mede o resultado das contestações enviadas pelo cliente.",
        ),
        ("Manual", _TIPO_PROC_INTERPRETACAO["Manual"]),
        ("Automático", _TIPO_PROC_INTERPRETACAO["Automático"]),
        ("Processual", _TIPO_PROC_INTERPRETACAO["Processual"]),
        ("Não é falha", _TIPO_PROC_INTERPRETACAO["Não é falha"]),
        ("Não classificado", _TIPO_PROC_INTERPRETACAO["Não classificado"]),
        (
            "Registros com termos predefinidos no motivo",
            "Falhas notificadas cujo texto do motivo contém termos de uma lista predefinida "
            "(filtro automático). Não confirma fraude, irregularidade nem ocorrência.",
        ),
        ("FN / FP", "Falso negativo (não sinalizado) / falso positivo (sinalização incorreta)."),
        (
            "Casos auditados",
            "Protocolos distintos revisados no universo auditado do período "
            "(deduplicação global no total).",
        ),
        (
            "Registros / etapas",
            "Linhas brutas do universo auditado; um mesmo protocolo pode aparecer em várias etapas.",
        ),
        (
            "Razão descritiva entre bases",
            "Falhas notificadas ÷ casos auditados. Não é taxa formal de erro.",
        ),
        (
            "Mês parcial",
            "Último mês do período quando a data final não corresponde ao encerramento do mês. "
            "Deve ser interpretado com cautela em comparações mensais.",
        ),
        (
            "Classificação inferida",
            "Classificação atribuída a partir do motivo ou cenário quando o tipo "
            "não está preenchido diretamente na base.",
        ),
        (
            "Destaque de volume",
            "Volume que difere significativamente dos demais períodos (critério: máximo e "
            "acima da média + 1 desvio). Não afirma causa nem constitui anomalia estatística formal.",
        ),
        (
            "Sequência esperada",
            "Protocolo com falha de auditoria, notificação Qualidade → CS e contestação, "
            "com datas mínimas na ordem auditoria → notificação → contestação. "
            "Cruzamento descritivo por protocolo — não confirma comunicação ao cliente.",
        ),
        (
            "Ordem invertida",
            "Protocolo em que as datas mínimas das bases não respeitam a sequência "
            "auditoria → notificação → contestação (ex.: contestação antes da notificação).",
        ),
    ]
    rows = [[a, b] for a, b in items]
    return (
        '<div class="glossary-panel">'
        '<p class="section-p">Referência rápida das siglas e conceitos usados no relatório.</p>'
        + _simple_table(["Sigla / termo", "Significado"], rows)
        + "</div>"
    )


def _tipo_definitions_block() -> str:
    rows = [[t, _TIPO_PROC_INTERPRETACAO[t]] for t in _TIPO_PROC_LABELS]
    return (
        '<h4 class="sub-h">O que significa cada tipo</h4>'
        + _simple_table(["Tipo", "Definição"], rows)
        + '<p class="note">Tipos vêm da coluna de tipo na base de Contestação quando disponível; '
        "quando ausente, a classificação é inferida a partir do motivo/cenário.</p>"
    )


def _count_na_sinalizacao(na: pd.DataFrame) -> int:
    if na.empty or "MOTIVO DA FALHA" not in na.columns:
        return 0
    flags = na["MOTIVO DA FALHA"].map(classificar_fn_fp)
    return int((flags == "FP").sum())


def _tipo_resultado_block(
    title: str,
    tipo_df: pd.DataFrame,
    caption: str,
    pct_header: str,
    color: str,
) -> str:
    if tipo_df.empty:
        return f'<p class="na">Sem dados para {title.lower()}.</p>'
    pairs = [(r["Tipo de procedência"], int(r["Casos"])) for _, r in tipo_df.iterrows()]
    chart = ""
    tipo_colors = [color, "#2563eb", "#64748b", "#94a3b8"]
    b64 = chart_barh(
        pairs,
        title,
        color=tipo_colors[: len(pairs)],
        show_pct=True,
    )
    if b64:
        parts = ", ".join(f"{t}: {n}" for t, n in pairs[:4])
        alt = f"{title} — {parts}"
        top = pairs[0]
        chart = _chart_card(
            img_tag(b64, alt),
            caption,
            lead=f"Maior volume: <b>{_esc(top[0])}</b> ({top[1]} casos).",
        )
    rows = [
        [r["Tipo de procedência"], r["Casos"], r["Pct"]]
        for _, r in tipo_df.iterrows()
    ]
    return (
        f'<div class="tipo-resultado-col">'
        f'<h4 class="sub-h tipo-sub-h">{_esc(title)}</h4>'
        + chart
        + _simple_table(
            ["Tipo", "Qtd", pct_header],
            rows,
            numeric_cols={1, 2},
            badge_cols={0},
        )
        + "</div>"
    )


def _sec2_tipo_dual_block(
    proc_tipo_df: pd.DataFrame,
    improc_tipo_df: pd.DataFrame,
    tipo_inferred: bool = False,
    proc_base: pd.DataFrame | None = None,
    improc_base: pd.DataFrame | None = None,
) -> str:
    if proc_tipo_df.empty and improc_tipo_df.empty:
        return ""
    tipo_ref = improc_tipo_df if not improc_tipo_df.empty else proc_tipo_df
    return (
        _tipo_definitions_block()
        + '<h4 class="sub-h">Distribuição por tipo (Manual · Automático · Processual)</h4>'
        '<div class="tipo-dual-grid">'
        + _tipo_resultado_block(
            "Procedentes (CONFORME = Não)",
            proc_tipo_df,
            "Participação de cada tipo entre os casos com falha confirmada.",
            "% do total procedente",
            C["red"],
        )
        + _tipo_resultado_block(
            "Improcedentes (CONFORME = Sim)",
            improc_tipo_df,
            "Participação de cada tipo entre os casos classificados como conformes.",
            "% do total improcedente",
            C["green"],
        )
        + "</div>"
        + _tipo_transparency_note(
            tipo_ref,
            tipo_inferred,
            proc_tipo_df=proc_tipo_df,
            improc_tipo_df=improc_tipo_df,
            proc_base=proc_base,
            improc_base=improc_base,
        )
    )


def _tipo_classif_counts(df: pd.DataFrame | None) -> tuple[int, int, int]:
    """Retorna (direto da base, inferidos, não classificados).

    \"Não é falha\" conta como classificação direta (SEM FALHA), não como
    \"Não classificado\".
    """
    if df is None or df.empty:
        return 0, 0, 0
    if "_tipo_inferido" not in df.columns or "_tipo_procedencia" not in df.columns:
        return 0, 0, 0
    tipo = df["_tipo_procedencia"]
    nc = int((tipo == "Não classificado").sum())
    inferidos = int(
        ((df["_tipo_inferido"].astype(bool)) & (tipo != "Não classificado")).sum()
    )
    direto = int(
        ((~df["_tipo_inferido"].astype(bool)) & (tipo != "Não classificado")).sum()
    )
    return direto, inferidos, nc


def _tipo_transparency_note(
    tipo_df: pd.DataFrame,
    inferred: bool,
    proc_tipo_df: pd.DataFrame | None = None,
    improc_tipo_df: pd.DataFrame | None = None,
    proc_base: pd.DataFrame | None = None,
    improc_base: pd.DataFrame | None = None,
) -> str:
    has_nc = False
    nc_improc = 0
    total_improc = 0
    if improc_tipo_df is not None and not improc_tipo_df.empty:
        total_improc = int(improc_tipo_df["Casos"].sum())
        nc_rows = improc_tipo_df[improc_tipo_df["Tipo de procedência"] == "Não classificado"]
        nc_improc = int(nc_rows["Casos"].sum()) if not nc_rows.empty else 0
        has_nc = nc_improc > 0
    elif not tipo_df.empty and "Tipo de procedência" in tipo_df.columns:
        nc = tipo_df[tipo_df["Tipo de procedência"] == "Não classificado"]
        has_nc = not nc.empty and int(nc["Casos"].sum()) > 0

    d_proc, i_proc, n_proc = _tipo_classif_counts(proc_base)
    d_imp, i_imp, n_imp = _tipo_classif_counts(improc_base)
    has_counts = (d_proc + i_proc + n_proc + d_imp + i_imp + n_imp) > 0

    note = (
        '<p class="note callout">'
        "<b>Classificação:</b> prioriza a coluna de tipo na Contestação "
        "(<i>COLABORADOR</i> = Manual). "
    )
    if has_counts:
        note += (
            f"Procedentes — direto <b>{d_proc}</b> / inferido <b>{i_proc}</b> / "
            f"N/C <b>{n_proc}</b>. "
            f"Improcedentes — direto <b>{d_imp}</b> / inferido <b>{i_imp}</b> / "
            f"N/C <b>{n_imp}</b>. "
        )
    elif inferred:
        note += "Combina valores da base e inferência por motivo/cenário. "
    if has_nc and total_improc and nc_improc >= max(1, int(0.5 * total_improc)):
        note += (
            f"Muitos improcedentes sem tipo (<b>{nc_improc}</b> de <b>{total_improc}</b>). "
        )
    elif has_nc:
        note += "<b>Não classificado</b> = informação insuficiente na base. "
    # Improcedentes com SEM FALHA
    if improc_tipo_df is not None and not improc_tipo_df.empty:
        nef = improc_tipo_df[improc_tipo_df["Tipo de procedência"] == "Não é falha"]
        if not nef.empty and int(nef["Casos"].sum()) > 0:
            note += (
                "<b>Não é falha</b> = SEM FALHA + CONFORME Sim. "
            )
    note += "O tipo não substitui o motivo.</p>"
    return note


def _procedentes_contestacao(cont: pd.DataFrame) -> pd.DataFrame:
    if cont.empty:
        return cont.copy()
    proc = cont[cont["classificacao_conforme"] == "falha"].copy()
    if proc.empty:
        return proc

    # Índice de cenários por protocolo (toda a Contestação) para resolver SINALIZAÇÃO CORRETA
    from report_brb.brb_filters import norm_protocolo

    full = cont.copy()
    if "_protocolo_norm" not in full.columns:
        full["_protocolo_norm"] = (
            full["Protocolo"].map(norm_protocolo) if "Protocolo" in full.columns else ""
        )
    if "_protocolo_norm" not in proc.columns:
        proc["_protocolo_norm"] = (
            proc["Protocolo"].map(norm_protocolo) if "Protocolo" in proc.columns else ""
        )

    by_proto: dict[str, list[str]] = {}
    for _, r in full.iterrows():
        p = str(r.get("_protocolo_norm") or "").strip()
        if not p:
            continue
        cen = r.get("Cenário")
        if cen is None or not str(cen).strip():
            continue
        by_proto.setdefault(p, []).append(str(cen).strip())

    proc["_cenario_classif"] = proc.apply(
        lambda r: _resolve_cenario_classificacao(r, by_proto), axis=1
    )
    proc = _apply_tipo_cols(proc)
    proc["_motivo"] = proc.apply(_motivo_procedente_row, axis=1)
    return proc


def _group_procedentes_by_tipo(proc: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    return _group_contestacao_by_tipo(proc)


def _group_procedentes(cont: pd.DataFrame) -> pd.DataFrame:
    """Agrupa contestações procedentes (CONFORME Não) por motivo/cenário — Top 10 + Outros."""
    proc = _procedentes_contestacao(cont)
    if proc.empty:
        return pd.DataFrame(
            columns=["Motivo", "Tipo de procedência", "Quantidade", "Pct", "Exemplos"]
        )

    total = len(proc)
    rows = []
    for motivo, g in proc.groupby("_motivo", dropna=False):
        protos = g.get("Protocolo", pd.Series(dtype=str)).dropna().astype(str).unique()[:3]
        ex = ", ".join(protos) if len(protos) else "—"
        tipo_counts = g["_tipo_procedencia"].value_counts()
        tipo_dom = str(tipo_counts.index[0]) if len(tipo_counts) else "Não classificado"
        if len(tipo_counts) > 1:
            tipo_dom = f"{tipo_dom}*"
        rows.append(
            {
                "Motivo": motivo,
                "Tipo de procedência": tipo_dom,
                "Quantidade": len(g),
                "Pct": _fmt_pct(len(g), total),
                "Exemplos": ex,
            }
        )
    df = pd.DataFrame(rows).sort_values("Quantidade", ascending=False)
    if len(df) > 10:
        top = df.head(9)
        outros = df.iloc[9:]
        outros_row = {
            "Motivo": "Outros",
            "Tipo de procedência": "—",
            "Quantidade": int(outros["Quantidade"].sum()),
            "Pct": _fmt_pct(int(outros["Quantidade"].sum()), total),
            "Exemplos": "—",
        }
        df = pd.concat([top, pd.DataFrame([outros_row])], ignore_index=True)
    return df


def _status_label(situacao: str) -> str:
    s = str(situacao).strip()
    if s.lower() in ("cancelado", "cancelada"):
        return "Encerrado/Cancelado"
    return s


def _demanda_volume_note(
    metrics: BRBMetrics,
    multi_proto: int = 0,
    extra_rows: int = 0,
) -> str:
    qi = metrics.demandas_na_qi_distintas
    qi_txt = (
        f" (<b>{qi}</b> número(s) de solicitação distinto(s))"
        if qi and qi != metrics.demandas_na_registros
        else ""
    )
    multi_txt = ""
    if multi_proto and extra_rows:
        multi_txt = (
            f" <b>{multi_proto}</b> protocolo{'s' if multi_proto > 1 else ''} "
            f"{'concentram' if multi_proto > 1 else 'concentra'} mais de uma falha notificada "
            f"(<b>{extra_rows}</b> linha(s) a mais — mesmo protocolo em etapas ou achados distintos)."
        )
    return (
        '<p class="note callout">'
        f"<b>Como ler estes números:</b> "
        f"<b>{metrics.demandas_na_registros}</b> solicitação(ões) abertas pelo Qualidade "
        f"ao <b>CS do cliente BRB</b>{qi_txt}. "
        f"<b>{metrics.protocolos_na}</b> é o volume agregado informado na abertura. "
        f"O <b>CS</b> comunicou <b>{metrics.na_falhas_registros}</b> falhas ao <b>BRB</b> "
        f"(<b>{metrics.na_falhas_protocolos}</b> protocolos distintos) — "
        "cada linha de falha é um achado, não um protocolo único."
        f"{multi_txt}"
        "</p>"
    )


def _demanda_status_table(dem: pd.DataFrame) -> tuple[list[list], str]:
    if dem.empty or "Situação" not in dem.columns:
        return [], ""
    if "Quantidade de Protolocos" in dem.columns:
        raw = dem.groupby("Situação")["Quantidade de Protolocos"].sum().astype(int)
    else:
        raw = dem["Situação"].value_counts()
    rows = [
        [_status_label(k), int(v), _fmt_pct(int(v), int(raw.sum()))]
        for k, v in raw.items()
    ]
    table = _simple_table(["Situação", "Protocolos (volume agregado)", "%"], rows)
    note = (
        '<p class="note">O status <b>Encerrado/Cancelado</b> representa a situação registrada no '
        "controle da solicitação e não significa, necessariamente, ausência de tratativa operacional.</p>"
    )
    return rows, table + note


def _na_attack_tables(bundle: BRBDataBundle, metrics: BRBMetrics) -> tuple[str, str]:
    na = bundle.na_falhas
    if na.empty:
        return '<p class="na">Não disponível.</p>', ""
    atk = na[na.get("possivel_ataque", False) == True].copy()
    total_atk = len(atk)
    if total_atk == 0:
        return '<p class="na">Nenhum registro classificado como possível ataque no período.</p>', ""

    if "MOTIVO DA FALHA" in atk.columns:
        mot = atk.groupby("MOTIVO DA FALHA").size().reset_index(name="Qtd")
        mot = mot.sort_values("Qtd", ascending=False).head(10)
        mot_rows = []
        for _, r in mot.iterrows():
            sub = atk[atk["MOTIVO DA FALHA"] == r["MOTIVO DA FALHA"]]
            protos = sub.get("PROTOCOLO", pd.Series(dtype=str)).astype(str).unique()[:3]
            mot_rows.append(
                [
                    clean_text(r["MOTIVO DA FALHA"], 60),
                    int(r["Qtd"]),
                    _fmt_pct(int(r["Qtd"]), total_atk),
                    ", ".join(protos) if len(protos) else "—",
                    "Monitoramento contínuo",
                ]
            )
        mot_table = _simple_table(
            ["Tipo de alerta", "Qtd", "% do possível ataque", "Exemplos protocolos", "Ação"],
            mot_rows,
        )
    else:
        mot_table = '<p class="na">Motivo não disponível na base.</p>'

    ex_rows = []
    for _, r in atk.head(8).iterrows():
        ex_rows.append(
            [
                clean_text(r.get("PROTOCOLO", ""), 20),
                clean_text(r.get("DEMANDA", ""), 20),
                clean_text(r.get("MOTIVO DA FALHA", ""), 50),
                format_date_br(r.get("DATA DE CADASTRO", "")),
            ]
        )
    ex_table = '<h4 class="sub-h">Exemplos</h4>' + _simple_table(
        ["Protocolo", "Demanda", "Motivo", "Data"], ex_rows
    )
    return mot_table, ex_table


def _timeline_score(row: pd.Series, attack_protos: set) -> float:
    score = 0.0
    cat = str(row.get("categoria", ""))
    badge = str(row.get("badge", "")).upper()
    ref = str(row.get("referencia", ""))
    det = str(row.get("detalhe", "")).lower()
    if cat == "contestacao" and ("NÃO" in badge or "NAO" in badge):
        score += 8
    if cat == "na" and (ref in attack_protos or "fraude" in det or "ataque" in det):
        score += 7
    if cat == "demanda":
        m = re.search(r"(\d+)\s*protocolos", det)
        if m and int(m.group(1)) >= 5:
            score += 5
        else:
            score += 2
    if cat == "treinamento":
        score += 3
    if cat == "na":
        score += 2
    ts = row.get("data")
    if pd.notna(ts):
        score += ts.timestamp() / 1e12
    return score


def _pick_timeline_events(timeline: pd.DataFrame, bundle: BRBDataBundle, limit: int = 10) -> pd.DataFrame:
    if timeline.empty:
        return timeline
    atk_protos = set()
    if not bundle.na_falhas.empty and "possivel_ataque" in bundle.na_falhas.columns:
        atk = bundle.na_falhas[bundle.na_falhas["possivel_ataque"] == True]
        atk_protos = set(atk.get("PROTOCOLO", pd.Series(dtype=str)).astype(str))
    tl = timeline.copy()
    tl["_score"] = tl.apply(lambda r: _timeline_score(r, atk_protos), axis=1)
    return tl.sort_values("_score", ascending=False).head(limit).drop(columns=["_score"])


def _tl_display(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, float) and pd.isna(val):
        return "—"
    try:
        if pd.isna(val):
            return "—"
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "<na>", "nat"):
        return "—"
    return s


def _tl_format_referencia(ref) -> str:
    raw = _tl_display(ref)
    if raw == "—":
        return "—"
    if "|" in raw:
        return " | ".join(_tl_display(p.strip()) for p in raw.split("|"))
    return raw


def _tl_extract_protocol(referencia) -> str:
    ref = _tl_display(referencia)
    if ref == "—":
        return "—"
    proto = ref.split("|")[0].strip()
    return _tl_display(proto) if proto else "—"


def _timeline_summary_table(timeline: pd.DataFrame) -> str:
    if timeline.empty or "categoria" not in timeline.columns:
        return '<p class="na">Sem eventos no período.</p>'
    counts = timeline.groupby("categoria").size().astype(int).to_dict()
    rows: list[list] = []
    for cat, label, desc in _TL_SUMMARY_META:
        qty = int(counts.get(cat, 0))
        if qty > 0:
            rows.append([label, qty, desc])
    if not rows:
        return '<p class="na">Sem eventos no período.</p>'
    return (
        '<h4 class="sub-h">Resumo dos eventos exibidos na linha do tempo</h4>'
        '<p class="note">As quantidades abaixo representam eventos registrados na linha do tempo '
        "(amostra exibida). <b>Não correspondem</b> aos KPIs do sumário — use o sumário para totais "
        "consolidados e esta seção para sequência cronológica.</p>"
        + _simple_table(["Tipo de ação", "Quantidade", "Descrição"], rows)
    )


def _timeline_details_body(timeline: pd.DataFrame) -> str:
    return (
        _timeline_summary_table(timeline)
        + '<details class="nested-details">'
        + "<summary>Ver eventos detalhados</summary>"
        + '<div class="details-body">'
        + _render_timeline_block(timeline, item_class="tl-appendix", grouped=True)
        + "</div></details>"
    )


def _render_timeline_item(
    row: pd.Series,
    item_class: str,
    protos_line: str | None = None,
) -> str:
    cat = str(row.get("categoria", ""))
    tipo = _tl_display(_TL_LABELS.get(cat, row.get("tipo", cat)))
    data_val = row.get("data")
    date_str = format_date_br(data_val) if data_val is not None and pd.notna(data_val) else "—"
    if protos_line is not None:
        body = f'<div class="tl-grouped-refs">Protocolos: {_esc(protos_line)}</div>'
    else:
        ref = _tl_format_referencia(row.get("referencia", ""))
        det = _tl_display(row.get("detalhe", ""))
        body = f'<div class="tl-ref">{_esc(ref)}</div><div class="tl-desc">{_esc(det)}</div>'
    return (
        f'<div class="timeline-item {item_class}">'
        f'<div class="tl-date">{_esc(date_str)}</div>'
        f'<div class="tl-type">{_esc(tipo)}</div>'
        f"{body}</div>"
    )


def _render_timeline_block(df: pd.DataFrame, item_class: str = "", grouped: bool = False) -> str:
    if df.empty:
        return '<p class="na">Sem eventos no período.</p>'
    blocks = []
    df = df.copy()
    df["mes_label"] = df["data"].apply(
        lambda d: mes_label_pt(pd.Timestamp(d)) if pd.notna(d) else "Sem data"
    )
    for mes, grp in df.groupby("mes_label", sort=False):
        items = []
        if grouped:
            for (_, _), sub in grp.groupby(["data", "categoria"], sort=False):
                if len(sub) > 1:
                    protos: list[str] = []
                    for _, row in sub.iterrows():
                        p = _tl_extract_protocol(row.get("referencia", ""))
                        if p != "—" and p not in protos:
                            protos.append(p)
                    shown = protos[:5]
                    suffix = "..." if len(protos) > 5 else ""
                    line = ", ".join(shown) + suffix if shown else "—"
                    items.append(_render_timeline_item(sub.iloc[0], item_class, protos_line=line))
                else:
                    items.append(_render_timeline_item(sub.iloc[0], item_class))
        else:
            for _, row in grp.iterrows():
                items.append(_render_timeline_item(row, item_class))
        blocks.append(
            f'<div class="timeline-month"><h4 class="tl-month-title">{_esc(mes)}</h4>'
            f'<div class="timeline">{"".join(items)}</div></div>'
        )
    return "".join(blocks)

def _top_attack_alert(bundle: BRBDataBundle) -> str:
    na = bundle.na_falhas
    if na.empty or "possivel_ataque" not in na.columns:
        return "Não disponível"
    atk = na[na["possivel_ataque"] == True]
    if atk.empty or "MOTIVO DA FALHA" not in atk.columns:
        return "Não disponível"
    top = atk["MOTIVO DA FALHA"].value_counts().index[0]
    return clean_text(padronizar_descricao(str(top)) or str(top), 60)


def _sec2_exec_text(
    metrics: BRBMetrics,
    proc_df: pd.DataFrame,
    total_av: int,
    proc_tipo_df: pd.DataFrame,
    improc_tipo_df: pd.DataFrame,
) -> str:
    if not total_av:
        return (
            '<p class="section-p">Não há contestações do BRB com avaliação de procedência '
            "registrada no período.</p>"
            '<p class="section-p"><b>Procedente</b> = CONFORME Não (falha confirmada) · '
            "<b>Improcedente</b> = CONFORME Sim (análise conforme).</p>"
        )
    text = (
        f'<p class="section-p">O <b>BRB</b> enviou contestações que o <b>time de Contestação</b> avaliou '
        f"em <b>{total_av}</b> casos: "
        f"<b>{metrics.conforme_nao}</b> procedentes — CONFORME = Não "
        f"(<b>{metrics.pct_procedente}%</b>) · "
        f"<b>{metrics.conforme_sim}</b> improcedentes — CONFORME = Sim "
        f"(<b>{metrics.pct_improcedente}%</b>).</p>"
    )
    if not proc_df.empty:
        text += (
            f'<p class="section-p">Entre as procedentes, o motivo mais frequente foi '
            f"<b>{_esc(proc_df.iloc[0]['Motivo'])}</b> "
            f'(<b>{_esc(proc_df.iloc[0]["Pct"])}</b> do total procedente).</p>'
        )
    if not proc_tipo_df.empty and int(proc_tipo_df.iloc[0].get("Casos", 0)) > 0:
        top = proc_tipo_df.iloc[0]
        text += (
            f'<p class="section-p">Tipo predominante nas <b>procedentes</b>: '
            f"<b>{_esc(top['Tipo de procedência'])}</b> "
            f"(<b>{_esc(top['Casos'])}</b> casos · <b>{_esc(top['Pct'])}</b>).</p>"
        )
    if not improc_tipo_df.empty and int(improc_tipo_df.iloc[0].get("Casos", 0)) > 0:
        top_i = improc_tipo_df.iloc[0]
        label = str(top_i["Tipo de procedência"])
        if label == "Não é falha":
            text += (
                f'<p class="section-p">Entre as <b>improcedentes</b>, predominam casos '
                f"<b>Não é falha</b> "
                f"(<b>{_esc(top_i['Casos'])}</b> · <b>{_esc(top_i['Pct'])}</b>) — "
                f"Tipo de falha = SEM FALHA / CONFORME = Sim.</p>"
            )
        else:
            text += (
                f'<p class="section-p">Tipo predominante nas <b>improcedentes</b>: '
                f"<b>{_esc(label)}</b> "
                f"(<b>{_esc(top_i['Casos'])}</b> casos · <b>{_esc(top_i['Pct'])}</b>).</p>"
            )
    return text


def _sec3_exec_text(proc_df: pd.DataFrame) -> str:
    if proc_df.empty:
        return (
            '<p class="section-p">Não há contestações procedentes no período para detalhamento por motivo.</p>'
        )
    text = (
        '<p class="section-p">Principais motivos das contestações <b>procedentes</b> '
        "(CONFORME = Não). Distribuição por tipo na "
        '<a href="#procedencia" onclick="document.querySelector(\'[data-section=procedencia]\')?.click()">'
        "Seção Procedência</a>.</p>"
    )
    top = proc_df.head(3)
    items = []
    for i, (_, r) in enumerate(top.iterrows(), start=1):
        items.append(
            f"<li><b>{i}. {_esc(r['Motivo'])}</b> — "
            f"{_esc(r['Quantidade'])} casos ({_esc(r['Pct'])})</li>"
        )
    text += f'<ul class="exec-bullets top-motivos">{"".join(items)}</ul>'
    if len(proc_df) > 3:
        text += (
            f'<p class="note">+ {len(proc_df) - 3} motivo(s) no detalhamento completo.</p>'
        )
    return text


def _sec3_tipo_procedencia_block(tipo_df: pd.DataFrame) -> str:
    if tipo_df.empty:
        return ""
    pairs = [(r["Tipo de procedência"], int(r["Casos"])) for _, r in tipo_df.iterrows()]
    chart = ""
    tipo_colors = [C["primary"], "#2563eb", "#64748b", "#94a3b8"]
    b64 = chart_barh(
        pairs,
        "Distribuição por tipo de procedência",
        color=tipo_colors[: len(pairs)],
        show_pct=True,
    )
    if b64:
        parts = ", ".join(f"{t}: {n}" for t, n in pairs[:4])
        chart = _chart_card(
            img_tag(b64, f"Procedência por tipo — {parts}"),
            "Participação de cada tipo entre os casos procedentes confirmados.",
            lead=(
                f"Concentração em <b>{_esc(pairs[0][0])}</b> "
                f"({pairs[0][1]} casos) entre os procedentes."
            ),
        )
    rows = [[r["Tipo de procedência"], r["Casos"], r["Pct"]] for _, r in tipo_df.iterrows()]
    return (
        '<h4 class="sub-h tipo-sub-h">Procedência por tipo</h4>'
        + chart
        + _simple_table(
            ["Tipo de procedência", "Casos", "% sobre procedentes"],
            rows,
            numeric_cols={1, 2},
            badge_cols={0},
        )
    )


def _find_fg_col(df: pd.DataFrame, *tokens: str) -> str | None:
    from report_brb.brb_normalize import strip_accents

    for col in df.columns:
        norm = strip_accents(safe_str(col)).lower()
        if all(t in norm for t in tokens):
            return col
    return None


def _normalize_tipo_falha_fg(valor) -> str:
    from report_brb.brb_normalize import strip_accents

    raw = strip_accents(safe_str(valor)).strip()
    if not raw or raw.lower() in ("nan", "none"):
        return "Não informado"
    key = raw.lower()
    mapping = {
        "automatico": "Automático",
        "manual": "Manual",
        "processual": "Processual",
        "mapeamento": "Mapeamento",
        "colaborador": "Manual",
        "necessidade de avaliacao": "Necessidade de avaliação",
        "necessidade de avaliação": "Necessidade de avaliação",
    }
    return mapping.get(key, raw.title())


def _fg_perfil_por_tipo(fg: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Agrupa FG por Tipo de Falha, excluindo Contestação Externa duplicada na Contestacao."""
    if fg.empty:
        return pd.DataFrame(columns=["Tipo de falha", "Casos", "Pct"]), 0, 0
    tipo_col = _find_fg_col(fg, "tipo", "falha")
    total_fg = len(fg)
    dup_n = int(fg.get("duplicado_contestacao", pd.Series(dtype=bool)).fillna(False).sum())
    if not tipo_col:
        return pd.DataFrame(columns=["Tipo de falha", "Casos", "Pct"]), total_fg, dup_n

    work = fg.copy()
    if "duplicado_contestacao" in work.columns:
        work = work[~work["duplicado_contestacao"].fillna(False)]
    work["_tipo_fg"] = work[tipo_col].map(_normalize_tipo_falha_fg)
    total = len(work)
    if not total:
        return pd.DataFrame(columns=["Tipo de falha", "Casos", "Pct"]), total_fg, dup_n

    counts = work["_tipo_fg"].value_counts()
    order = ["Automático", "Manual", "Processual", "Mapeamento", "Não informado"]
    rows = []
    seen: set[str] = set()
    for tipo in order:
        if tipo in counts.index:
            n = int(counts[tipo])
            rows.append({"Tipo de falha": tipo, "Casos": n, "Pct": _fmt_pct(n, total)})
            seen.add(tipo)
    for tipo, n in counts.items():
        if tipo not in seen:
            rows.append({"Tipo de falha": tipo, "Casos": int(n), "Pct": _fmt_pct(int(n), total)})
    return pd.DataFrame(rows), total_fg, dup_n


def _sec_fg_exec_text(perfil_df: pd.DataFrame, total_fg: int, dup_n: int) -> str:
    analise_n = total_fg - dup_n
    text = (
        '<p class="section-p">Foram registrados <b>'
        f"{total_fg}</b> casos de falhas encontradas em auditorias no período.</p>"
    )
    if dup_n:
        text += (
            f'<p class="section-p"><b>{dup_n}</b> registro(s) de Contestação Externa '
            "já constam na Contestação (mesmo protocolo) e foram excluídos desta visão para evitar duplicidade "
            "com a procedência avaliada na Seção 2.</p>"
        )
    text += (
        f'<p class="section-p">A distribuição por <b>tipo de falha</b> considera '
        f"<b>{analise_n}</b> caso(s) após essa exclusão.</p>"
    )
    if not perfil_df.empty and int(perfil_df.iloc[0].get("Casos", 0)) > 0:
        top = perfil_df.iloc[0]
        text += (
            f'<p class="section-p">A maior concentração está em <b>{_esc(top["Tipo de falha"])}</b> '
            f'(<b>{_esc(top["Pct"])}</b>) — perfil da <b>auditoria interna</b>, distinto do tipo '
            "dominante nas contestações procedentes (Seção 3).</p>"
        )
    return text


def _sec_fg_tipo_block(perfil_df: pd.DataFrame) -> str:
    if perfil_df.empty:
        return '<p class="na">Sem casos FG para análise por tipo no período.</p>'
    pairs = [(r["Tipo de falha"], int(r["Casos"])) for _, r in perfil_df.iterrows()]
    chart = ""
    tipo_colors = ["#0b2a59", C["primary"], "#3b82f6", "#64748b", "#94a3b8"]
    b64 = chart_barh(
        pairs,
        "Perfil por tipo de falha (auditoria FG)",
        color=tipo_colors[: len(pairs)],
        show_pct=True,
    )
    if b64:
        chart = _chart_card(
            img_tag(b64, "FG Tipo de Falha"),
            "Composição dos casos auditados após exclusão de Contestação Externa duplicada.",
        )
    rows = [[r["Tipo de falha"], r["Casos"], r["Pct"]] for _, r in perfil_df.iterrows()]
    return (
        '<h4 class="sub-h tipo-sub-h">Distribuição por tipo de falha (FG)</h4>'
        + chart
        + _simple_table(["Tipo de falha", "Casos", "% sobre FG analisado"], rows)
    )


def _sec_fg_detail_block(fg: pd.DataFrame, perfil_df: pd.DataFrame, dup_n: int) -> str:
    parts = [
        '<p class="note callout">'
        "<b>Origem:</b> falhas encontradas em auditorias (visão FG), classificadas por tipo de falha. "
        "<b>Contestação Externa:</b> quando o protocolo já consta na Contestação, o caso não é "
        "contado novamente nesta visão — a avaliação de procedência permanece na Seção 2/3.</p>"
    ]
    tipo_an_col = _find_fg_col(fg, "tipo", "anal")
    if tipo_an_col and not fg.empty:
        ext = fg[fg.get("contestacao_externa_fg", False).fillna(False)]
        ga = fg[~fg.get("contestacao_externa_fg", False).fillna(False)]
        parts.append(
            _simple_table(
                ["Tipo de análise (FG)", "Casos"],
                [
                    ["G Auditoria", len(ga)],
                    ["Contestação Externa", len(ext)],
                    ["Excluídos (protocolo já na Contestação)", dup_n],
                ],
            )
        )
    if not perfil_df.empty:
        parts.append('<h4 class="sub-h">Detalhamento por tipo de falha</h4>')
        parts.append(
            _simple_table(
                ["Tipo de falha", "Casos", "% sobre FG analisado"],
                [[r["Tipo de falha"], r["Casos"], r["Pct"]] for _, r in perfil_df.iterrows()],
            )
        )
    return "".join(parts)


def _sec3_tipo_detail_block(tipo_df: pd.DataFrame, inferred: bool) -> str:
    if tipo_df.empty:
        return ""
    rows = [[r["Tipo de procedência"], r["Interpretação"]] for _, r in tipo_df.iterrows()]
    has_nc = (
        not tipo_df.empty
        and (tipo_df["Tipo de procedência"] == "Não classificado").any()
        and int(tipo_df.loc[tipo_df["Tipo de procedência"] == "Não classificado", "Casos"].sum()) > 0
    )
    note = (
        '<p class="note callout">'
        "<b>Tipo:</b> prioriza a coluna na Contestação (<i>COLABORADOR</i> = Manual). "
    )
    if inferred:
        note += "Pode incluir inferência por motivo/cenário. "
    if has_nc:
        note += "<b>Não classificado</b> = informação insuficiente. "
    note += "O tipo não substitui o motivo.</p>"
    return (
        '<h4 class="sub-h">Interpretação dos tipos</h4>'
        + note
        + _simple_table(["Tipo", "Interpretação"], rows)
    )


def _conclusion_bullets(
    metrics: BRBMetrics,
    proc_df: pd.DataFrame,
    periodo: str,
    top_alert: str,
    tipo_df: pd.DataFrame | None = None,
    fg_total: int = 0,
    fg_dup: int = 0,
    fg_perfil_df: pd.DataFrame | None = None,
    data_fim=None,
) -> str:
    total_av = metrics.conforme_sim + metrics.conforme_nao
    highlights: list[str] = []
    if total_av:
        highlights.append(
            f"Período <b>{_esc(periodo)}</b>: <b>{total_av}</b> avaliações de Contestação, "
            f"das quais <b>{metrics.conforme_nao}</b> procedentes "
            f"(<b>{metrics.pct_procedente}%</b>; CONFORME = Não)."
        )
    if not proc_df.empty:
        highlights.append(
            f"Concentração observada nas procedentes: "
            f"<b>{_esc(proc_df.iloc[0]['Motivo'])}</b> "
            f"(<b>{_esc(proc_df.iloc[0]['Pct'])}</b>)."
        )
    if fg_total and fg_perfil_df is not None and not fg_perfil_df.empty:
        fg_tipo = _esc(fg_perfil_df.iloc[0]["Tipo de falha"])
        cont_txt = ""
        if tipo_df is not None and not tipo_df.empty:
            cont_txt = (
                f"; nas procedentes da Contestação, "
                f"<b>{_esc(tipo_df.iloc[0]['Tipo de procedência'])}</b>"
            )
        highlights.append(
            f"Tipo predominante nas falhas de auditoria: <b>{fg_tipo}</b>{cont_txt}."
        )

    partial_note = _partial_month_note(data_fim)
    partial_html = (
        f'<p class="note callout"><b>Mês parcial:</b> {_esc(partial_note)}</p>'
        if partial_note
        else ""
    )

    hl = "".join(f"<li>{b}</li>" for b in highlights[:3])
    return (
        '<div class="conclusion-box">'
        '<h4 class="sub-h" style="margin-top:0">Síntese do período</h4>'
        f"<ul>{hl or '<li>Sem destaques quantitativos no período.</li>'}</ul>"
        f"{partial_html}"
        '<p class="note">'
        "<b>Ressalvas:</b> relatório descritivo (ações e metas cabem às áreas gestoras). "
        "Auditoria, notificação e Contestação são bases distintas. "
        f"{_esc(_TEXTO_COMUNICACAO_CLIENTE)}"
        "</p>"
        "</div>"
    )


def _appendix_html(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    analytics: BRBAnalytics | None,
    timeline_full: pd.DataFrame,
    proc_table_fn,
) -> str:
    parts = []
    parts.append(
        '<p class="note callout">'
        "<b>Anexo para consulta:</b> amostras das bases usadas no relatório. "
        "Métricas técnicas de qualidade de dado ficam em bloco separado (uso interno)."
        "</p>"
    )
    if analytics is not None:
        parts.append("<h4>Cruzamentos resumidos</h4>")
        cruz_rows = [
            [
                "Casos FG sem correspondência em Notificação Ativa",
                analytics.cruzamentos.get("fg_sem_match_na", "N/D"),
            ],
            [
                "Casos FG sem avaliação em Contestação",
                analytics.cruzamentos.get("fg_sem_contestacao", "N/D"),
            ],
            [
                "Protocolos FG ∩ notificados (Qualidade → CS)",
                analytics.sequencia_resumo.get("n_fg_e_na", "N/D")
                if getattr(analytics, "sequencia_resumo", None)
                else "N/D",
            ],
            [
                "Sequência esperada (auditoria → notificação → contestação)",
                analytics.sequencia_resumo.get("n_completo_esperado", "N/D")
                if getattr(analytics, "sequencia_resumo", None)
                else "N/D",
            ],
            [
                "Sem contestação correspondente",
                analytics.sequencia_resumo.get("n_sem_contestacao", "N/D")
                if getattr(analytics, "sequencia_resumo", None)
                else "N/D",
            ],
            [
                "Ordem temporal invertida",
                analytics.sequencia_resumo.get("n_ordem_invertida", "N/D")
                if getattr(analytics, "sequencia_resumo", None)
                else "N/D",
            ],
        ]
        parts.append(_simple_table(["Indicador", "Qtd"], cruz_rows))
        if not analytics.qualidade.empty:
            rows = [
                [r["metrica"], r["quantidade"], f"{r['percentual']}%"]
                for _, r in analytics.qualidade.iterrows()
                if not _is_pii_label(r["metrica"])
            ]
            parts.append(
                '<details class="nested-details">'
                "<summary>Qualidade de dados (uso interno)</summary>"
                '<div class="details-body">'
                '<p class="note">Indicadores técnicos de preenchimento e consistência da base — '
                "não são KPIs operacionais para o CS.</p>"
                + _simple_table(["Métrica", "Qtd", "%"], rows)
                + "</div></details>"
            )
    parts.append(
        "<h4>Solicitações NA — amostra</h4>" + proc_table_fn(bundle.na_demandas)
    )
    parts.append(
        "<h4>Falhas NA — amostra (até 100)</h4>" + proc_table_fn(bundle.na_falhas.head(100))
    )
    parts.append(
        "<h4>Contestação — amostra (até 100)</h4>"
        + proc_table_fn(bundle.contestacao.head(100))
    )
    parts.append(
        "<h4>Linha do tempo — amostra</h4>"
        + _render_timeline_block(timeline_full.head(80), "tl-appendix")
    )
    return "".join(parts)


def _client_css() -> str:
    P, D, G, BD = C["primary"], C["dark"], C["gray"], C["border"]
    return f"""
    *{{box-sizing:border-box;}}
    html{{scroll-padding-top:72px;}}
    body{{margin:0;background:#f1f5f9;font-family:"Segoe UI",system-ui,Arial,sans-serif;color:#1e293b;font-size:15px;line-height:1.65;}}
    body.lightbox-open{{overflow:hidden;}}
    .wrap{{max-width:1080px;margin:0 auto;padding:28px 22px 48px;}}
    .cover{{background:linear-gradient(145deg,{D} 0%,#134a8a 55%,{P} 100%);color:#fff;border-radius:12px;padding:28px 32px;margin-bottom:20px;position:relative;overflow:hidden;}}
    .cover::after{{content:"";position:absolute;right:-36px;top:-36px;width:160px;height:160px;border-radius:50%;background:rgba(255,255,255,.08);pointer-events:none;}}
    .cover-brand{{display:flex;align-items:center;gap:12px;margin-bottom:14px;position:relative;z-index:1;}}
    .cover-mark{{display:inline-flex;align-items:center;justify-content:center;min-width:46px;height:32px;padding:0 12px;border:1px solid rgba(255,255,255,.45);border-radius:8px;font-size:13px;font-weight:800;letter-spacing:.08em;background:rgba(255,255,255,.1);}}
    .cover-eyebrow{{font-size:13px;font-weight:600;opacity:.88;}}
    .cover h1{{margin:0 0 10px;font-size:24px;font-weight:700;letter-spacing:-.01em;position:relative;z-index:1;}}
    .cover-meta{{font-size:13px;opacity:.9;margin-top:6px;position:relative;z-index:1;}}
    .cover-meta b{{font-weight:700;}}
    .cover-intro{{margin-top:14px;font-size:15px;opacity:.95;line-height:1.6;max-width:780px;position:relative;z-index:1;}}
    .cover-howto{{margin:12px 0 0;padding-left:18px;font-size:13px;opacity:.92;line-height:1.55;max-width:780px;position:relative;z-index:1;}}
    .cover-howto li{{margin-bottom:4px;}}
    .section-nav{{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.97);border:1px solid {BD};border-radius:12px;padding:12px 14px;margin-bottom:20px;backdrop-filter:blur(6px);}}
    .section-nav-title{{margin:0 0 8px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:{G};font-weight:600;}}
    .section-nav-tabs{{display:flex;flex-wrap:nowrap;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:thin;padding-bottom:2px;}}
    .section-nav-btn{{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;min-height:40px;border:1px solid {BD};cursor:pointer;padding:8px 14px;border-radius:8px;color:#334155;background:#f8fafc;font-weight:600;font-size:13px;line-height:1.2;transition:background .15s,color .15s,border-color .15s;font-family:inherit;}}
    .section-nav-btn:hover{{background:#e8f1fb;border-color:#93c5fd;color:{P};}}
    .section-nav-btn:focus-visible{{outline:3px solid #93c5fd;outline-offset:2px;}}
    .section-nav-btn.active{{background:{P};color:#fff;border-color:{P};}}
    .kpi-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 0 20px;}}
    .kpi-grid-audit{{margin:12px 0 16px;grid-template-columns:repeat(4,1fr);}}
    @media(max-width:1024px){{.kpi-grid,.kpi-grid-audit{{grid-template-columns:repeat(2,1fr);}}}}
    @media(max-width:480px){{.kpi-grid,.kpi-grid-audit{{grid-template-columns:1fr;}}}}
    .kpi-card{{background:#f8fafc;border:1px solid {BD};border-radius:10px;padding:14px 14px 12px;border-top:3px solid {P};min-width:0;}}
    .kpi-card-info{{border-top-color:{P};}}
    .kpi-card-alert{{border-top-color:#b45309;}}
    .kpi-card-ok{{border-top-color:#15803d;}}
    .kpi-card-warn{{border-top-color:#d97706;background:#fffbeb;}}
    .kpi-card-value{{display:block;font-size:26px;font-weight:800;color:{D};line-height:1.15;letter-spacing:-.02em;}}
    .kpi-card-label{{display:block;margin-top:6px;font-size:13px;font-weight:700;color:#334155;}}
    .kpi-card-sub{{display:block;margin-top:2px;font-size:12px;color:{G};}}
    .kpi-card-note{{display:block;margin-top:8px;font-size:12px;color:#92400e;line-height:1.4;}}
    .flow-box{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 16px;margin:12px 0;}}
    .flow-box-title{{margin:0 0 10px;font-size:13px;font-weight:700;color:{D};}}
    .flow-steps,.process-steps{{display:flex;flex-wrap:wrap;align-items:center;gap:6px;justify-content:center;}}
    .flow-step,.process-step{{text-align:center;min-width:72px;padding:8px 10px;background:#fff;border:1px solid {BD};border-radius:8px;}}
    .flow-n{{display:block;font-size:20px;font-weight:800;color:{P};line-height:1.2;}}
    .flow-lbl{{display:block;font-size:12px;color:#475569;margin-top:4px;line-height:1.3;}}
    .flow-lbl small{{color:{G};}}
    .flow-arrow{{color:{G};font-weight:700;font-size:15px;padding:0 2px;}}
    .flow-note{{margin:10px 0 0;font-size:13px;color:#475569;line-height:1.45;}}
    .process-box{{background:#fff;border:1px solid {BD};border-radius:10px;padding:14px 16px;margin:16px 0 12px;}}
    .process-box-compact{{padding:12px 14px;}}
    .process-box .flow-box-title{{margin-bottom:12px;}}
    .process-parallel-flows{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:4px;}}
    @media(max-width:720px){{.process-parallel-flows{{grid-template-columns:1fr;}}}}
    .process-flow-col{{border:1px solid {BD};border-radius:8px;padding:12px;background:#f8fafc;}}
    .process-flow-label{{margin:0 0 10px;font-size:12px;font-weight:700;color:{D};}}
    .process-step{{min-width:100px;max-width:140px;}}
    .process-role{{display:block;font-size:13px;font-weight:800;color:{P};margin-bottom:3px;}}
    .process-desc{{display:block;font-size:12px;color:#475569;line-height:1.35;}}
    .process-parallel{{margin-top:8px;}}
    .tipo-dual-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:14px 0;}}
    @media(max-width:720px){{.tipo-dual-grid{{grid-template-columns:1fr;}}}}
    .tipo-resultado-col{{min-width:0;}}
    .glossary-panel{{padding:2px 0;}}
    .report-panels{{min-height:280px;}}
    .report-panel{{display:none;}}
    .report-panel.is-visible{{display:block;}}
    .executive-summary{{margin-bottom:0;background:#fff;border:1px solid {BD};border-radius:12px;padding:22px 24px;}}
    .executive-summary h2,.section-title{{font-size:21px;color:{D};margin:0 0 16px;font-weight:700;letter-spacing:-.01em;}}
    .section-title .section-number{{border:none;padding:0;}}
    .executive-narrative{{color:#334155;margin:0 0 14px;line-height:1.65;font-size:15px;}}
    .bases-sub-h{{margin-top:16px;margin-bottom:8px;}}
    .executive-kpi-list{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:0 20px;}}
    @media(max-width:640px){{.executive-kpi-list{{grid-template-columns:1fr;}}}}
    .executive-kpi-list li{{padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:14px;color:#334155;}}
    .kpi-label{{color:#475569;}}
    .section-exec{{margin-bottom:8px;}}
    .section-details,.nested-details,.tl-method-details{{background:#fff;border:1px solid {BD};border-radius:8px;margin-top:14px;}}
    .section-details summary,.nested-details summary,.tl-method-details summary{{display:flex;align-items:center;gap:8px;padding:12px 14px;font-weight:600;color:{P};cursor:pointer;font-size:14px;min-height:44px;list-style:none;}}
    .section-details summary::-webkit-details-marker,.nested-details summary::-webkit-details-marker,.tl-method-details summary::-webkit-details-marker,.tl-month-summary::-webkit-details-marker{{display:none;}}
    .section-details summary:focus-visible,.nested-details summary:focus-visible,.tl-month-summary:focus-visible,.chart-lightbox-close:focus-visible{{outline:3px solid #93c5fd;outline-offset:2px;}}
    .details-chevron{{width:0;height:0;border-top:5px solid transparent;border-bottom:5px solid transparent;border-left:7px solid {P};flex:0 0 auto;transition:transform .15s ease;}}
    details[open] > summary .details-chevron{{transform:rotate(90deg);}}
    .details-count{{color:{G};font-weight:600;font-size:13px;}}
    .section-details .details-body,.nested-details .details-body{{padding:0 14px 16px;font-size:14px;color:#475569;}}
    .exec-bullets{{margin:0 0 8px;padding-left:18px;color:#475569;line-height:1.6;}}
    .report-section{{background:#fff;border:1px solid {BD};border-radius:12px;padding:22px 24px;margin-bottom:0;}}
    .section-number{{color:{P};margin-right:6px;}}
    .section-p{{color:#475569;margin:0 0 12px;line-height:1.65;font-size:15px;}}
    .sub-h{{font-size:15px;color:{D};margin:16px 0 10px;font-weight:700;}}
    .section-exec .tipo-sub-h{{margin-top:0;}}
    .chart-wrap,.tipo-chart-wrap{{margin:12px 0;text-align:center;}}
    .chart-card{{background:#fff;border:1px solid {BD};border-radius:10px;padding:14px 16px 12px;margin:14px 0;text-align:center;}}
    .chart-lead{{margin:0 0 10px;font-size:15px;color:#334155;text-align:left;line-height:1.55;}}
    .chart-card .chart-img,.chart-wrap .chart-img,.tipo-chart-wrap .chart-img{{max-width:640px;width:100%;height:auto;display:block;margin:0 auto;cursor:zoom-in;border-radius:4px;}}
    .chart-card .chart-img:focus-visible{{outline:3px solid #93c5fd;outline-offset:3px;}}
    .chart-expand-hint{{margin:0 0 8px;font-size:12px;color:{G};letter-spacing:.02em;}}
    .chart-caption{{font-size:13px;color:{G};margin:10px 0 0;line-height:1.45;text-align:center;}}
    .chart-lightbox{{position:fixed;inset:0;z-index:1000;background:rgba(15,23,42,.85);display:flex;align-items:center;justify-content:center;padding:24px;}}
    .chart-lightbox[hidden]{{display:none !important;}}
    .chart-lightbox-img{{max-width:min(1100px,96vw);max-height:90vh;width:auto;height:auto;border-radius:8px;background:#fff;}}
    .chart-lightbox-close{{position:absolute;top:16px;right:20px;border:none;background:#fff;color:#0f172a;width:44px;height:44px;border-radius:50%;font-size:24px;line-height:1;cursor:pointer;}}
    .tl-compare{{margin:8px 0 18px;padding:14px;background:#f8fafc;border:1px solid {BD};border-radius:10px;}}
    .tl-legend{{display:flex;flex-wrap:wrap;gap:10px 16px;list-style:none;margin:0 0 12px;padding:0;font-size:12px;color:#475569;}}
    .tl-leg-swatch{{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:middle;}}
    .tl-cmp-falhas,.tl-lane-na-sw{{background:#ea580c;}}
    .tl-cmp-aval,.tl-lane-cont-sw{{background:#16a34a;}}
    .tl-cmp-proc{{background:#b45309;}}
    .tl-cmp-cap{{background:#6D2077;}}
    .tl-lane-demanda-sw{{background:#2563eb;}}
    .tl-cmp-chart{{display:flex;flex-direction:column;gap:8px;}}
    .tl-cmp-row{{display:grid;grid-template-columns:48px 1fr;gap:8px;align-items:center;}}
    .tl-cmp-label{{font-size:12px;font-weight:700;color:{D};text-transform:uppercase;}}
    .tl-cmp-tracks{{display:flex;flex-direction:column;gap:3px;}}
    .tl-cmp-track{{height:14px;background:#e2e8f0;border-radius:3px;overflow:hidden;}}
    .tl-cmp-bar{{display:block;height:100%;min-width:0;border-radius:3px;position:relative;}}
    .tl-cmp-bar-val{{position:absolute;right:4px;top:50%;transform:translateY(-50%);font-size:10px;font-weight:700;color:#fff;text-shadow:0 0 2px rgba(0,0,0,.35);}}
    .tl-cmp-bar.tl-cmp-falhas{{background:#ea580c;}}
    .tl-cmp-bar.tl-cmp-aval{{background:#16a34a;}}
    .tl-cmp-bar.tl-cmp-proc{{background:#b45309;}}
    .tl-cmp-bar.tl-cmp-cap{{background:#6D2077;}}
    .tl-months{{display:flex;flex-direction:column;gap:10px;margin:12px 0 4px;}}
    .tl-month-block{{background:#fff;border:1px solid {BD};border-radius:10px;overflow:hidden;}}
    .tl-month-block.tl-month-atypical{{border-color:#f59e0b;box-shadow:inset 3px 0 0 #f59e0b;}}
    .tl-month-summary{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 12px;padding:12px 14px;cursor:pointer;min-height:48px;list-style:none;}}
    .tl-month-summary-left{{display:flex;align-items:center;gap:8px;min-width:0;}}
    .tl-month-label{{margin:0;font-size:15px;font-weight:800;color:{D};letter-spacing:.02em;}}
    .tl-atyp-badge{{display:inline-block;font-size:11px;font-weight:700;color:#92400e;background:#fef3c7;border:1px solid #fcd34d;border-radius:4px;padding:2px 7px;}}
    .tl-month-summary-stats{{display:flex;flex-wrap:wrap;gap:8px;}}
    .tl-head-stat{{font-size:12px;color:#475569;background:#f1f5f9;border-radius:4px;padding:3px 8px;}}
    .tl-head-stat b{{color:{D};}}
    .tl-month-body{{padding:0 14px 14px;}}
    .tl-lanes{{display:grid;grid-template-columns:1fr 1fr;gap:10px;}}
    @media(max-width:860px){{.tl-lanes{{grid-template-columns:1fr;}}}}
    .tl-lane{{border:1px solid {BD};border-radius:8px;padding:10px;background:#fafbfc;min-height:80px;}}
    .tl-lane-head{{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;}}
    .tl-lane-sub{{margin:0 0 8px;font-size:12px;color:{G};line-height:1.35;}}
    .tl-lane-demanda{{border-top:3px solid #2563eb;}}
    .tl-lane-demanda .tl-lane-head{{color:#1d4ed8;}}
    .tl-lane-na{{border-top:3px solid #ea580c;}}
    .tl-lane-na .tl-lane-head{{color:#c2410c;}}
    .tl-lane-cont{{border-top:3px solid #16a34a;}}
    .tl-lane-cont .tl-lane-head{{color:#15803d;}}
    .tl-lane-empty{{opacity:.72;}}
    .tl-lane-stats{{display:flex;flex-wrap:wrap;gap:6px;}}
    .tl-lane-meta{{margin:8px 0 0;font-size:12px;color:#475569;line-height:1.35;}}
    .tl-lane-muted{{margin:0;font-size:13px;color:{G};font-style:italic;}}
    .tl-qi-list{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}}
    .tl-qi-chip{{display:inline-block;background:#eff6ff;border:1px solid #bfdbfe;color:#1e3a8a;border-radius:6px;padding:3px 8px;font-size:12px;font-weight:600;}}
    .tl-qi-chip small{{font-weight:500;color:{G};margin-left:2px;}}
    .tl-chip{{display:inline-flex;align-items:baseline;gap:4px;background:#fff;border:1px solid {BD};border-radius:6px;padding:3px 8px;font-size:12px;color:#475569;}}
    .tl-chip b{{color:{D};font-size:13px;}}
    .simple-table{{width:100%;border-collapse:collapse;font-size:14px;}}
    .simple-table th{{text-align:left;padding:10px 12px;background:#f8fafc;border-bottom:2px solid {BD};font-size:12px;text-transform:uppercase;color:{G};}}
    .simple-table td{{padding:10px 12px;border-bottom:1px solid #f1f5f9;vertical-align:top;word-break:break-word;}}
    .simple-table tbody tr:nth-child(even){{background:#f8fafc;}}
    .simple-table tbody tr:hover{{background:#eef6ff;}}
    .simple-table td.num,.simple-table th.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;}}
    .table-wrap{{overflow-x:auto;margin:12px 0;-webkit-overflow-scrolling:touch;}}
    .tipo-badge{{display:inline-block;font-size:12px;font-weight:700;padding:3px 8px;border-radius:4px;border:1px solid transparent;white-space:nowrap;}}
    .tipo-badge-manual{{background:#eff6ff;color:#1d4ed8;border-color:#bfdbfe;}}
    .tipo-badge-auto{{background:#f0fdf4;color:#15803d;border-color:#bbf7d0;}}
    .tipo-badge-proc{{background:#fff7ed;color:#c2410c;border-color:#fed7aa;}}
    .tipo-badge-ok{{background:#ecfdf5;color:#047857;border-color:#a7f3d0;}}
    .tipo-badge-nc{{background:#f1f5f9;color:#475569;border-color:{BD};}}
    .tipo-badge-star{{margin-left:2px;color:{G};}}
    .chart-wrap img{{max-width:100%;height:auto;}}
    .na{{color:{G};font-style:italic;font-size:14px;}}
    .timeline-month{{margin-bottom:16px;}}
    .tl-month-title{{font-size:12px;font-weight:700;color:{D};margin:0 0 8px;text-transform:uppercase;}}
    .timeline{{border-left:2px solid {BD};margin-left:8px;padding-left:16px;}}
    .timeline-item{{margin-bottom:10px;padding-bottom:10px;border-bottom:1px dashed {BD};}}
    .timeline-item:last-child{{border-bottom:none;}}
    .tl-date{{font-size:13px;color:{G};font-weight:600;}}
    .tl-type{{font-size:14px;font-weight:700;color:{P};margin:2px 0;}}
    .tl-ref{{font-size:13px;font-family:Consolas,monospace;color:#334155;}}
    .tl-desc{{font-size:13px;color:#475569;margin-top:2px;}}
    .tl-grouped-refs{{font-family:Consolas,monospace;font-size:13px;color:#334155;margin-top:4px;}}
    .conclusion-box{{background:#f8fafc;border-left:4px solid {P};padding:16px 18px;border-radius:0 8px 8px 0;}}
    .conclusion-box ul{{margin:0;padding-left:18px;color:#334155;line-height:1.65;font-size:15px;}}
    .appendix-details{{background:#fafbfc;border:1px solid {BD};border-radius:8px;margin-top:0;}}
    .appendix-details summary{{padding:14px 16px;font-weight:600;color:{G};cursor:pointer;font-size:14px;min-height:44px;}}
    .appendix-details .appendix-body{{padding:0 16px 20px;font-size:14px;color:#475569;}}
    .btn-more{{background:{P};color:#fff;border:none;border-radius:4px;padding:10px 16px;font-size:14px;cursor:pointer;margin-top:12px;min-height:44px;font-family:inherit;}}
    .footer{{text-align:center;color:{G};font-size:13px;margin-top:28px;padding-top:14px;border-top:1px solid {BD};}}
    .note{{font-size:13px;color:{G};margin-top:8px;line-height:1.5;}}
    .callout{{background:#eff6ff;border-left:3px solid {P};padding:12px 14px;border-radius:0 6px 6px 0;margin:12px 0;}}
    @media(max-width:768px){{
      .wrap{{padding:16px 12px 36px;}}
      .cover{{padding:20px 16px;border-radius:10px;}}
      .cover h1{{font-size:20px;}}
      .cover-brand{{flex-wrap:wrap;gap:8px;}}
      .executive-summary,.report-section{{padding:16px;}}
      .section-nav-btn{{min-height:44px;}}
    }}
    @media(prefers-reduced-motion:reduce){{
      *,*::before,*::after{{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important;scroll-behavior:auto !important;}}
      .details-chevron{{transition:none;}}
    }}
    """

def _is_pii_label(text) -> bool:
    """Matrícula / agente — não exibir no relatório cliente."""
    from report_brb.brb_normalize import strip_accents

    s = strip_accents(str(text or "")).lower()
    tokens = (
        "matric",
        "agente",
        "agentes",
        "agent:",
        "userlan",
        "nome agente",
    )
    return any(t in s for t in tokens)


def _client_safe_columns(cols) -> list:
    return [c for c in cols if not str(c).startswith("_") and not _is_pii_label(c)]


def _mini_table(df: pd.DataFrame, max_cols: int = 6) -> str:
    if df is None or df.empty:
        return '<p class="na">Sem registros.</p>'
    cols = _client_safe_columns(df.columns)[:max_cols]
    if not cols:
        return '<p class="na">Sem colunas exibíveis.</p>'
    rows = []
    for _, r in df.head(50).iterrows():
        rows.append([clean_text(r.get(c, ""), 40) for c in cols])
    return _simple_table(cols, rows)


def _sanitize_timeline_client(timeline: pd.DataFrame) -> pd.DataFrame:
    """Remove matrícula/agente das referências da linha do tempo."""
    if timeline is None or timeline.empty:
        return timeline
    tl = timeline.copy()
    if "referencia" in tl.columns:
        def _clean_ref(val):
            s = str(val or "").strip()
            if not s or s.lower() in ("nan", "none", "—"):
                return "—"
            # "PROTO | MATRICULA" → só protocolo
            if "|" in s:
                s = s.split("|", 1)[0].strip()
            s = re.sub(r"\s*×\s*\d+\s*matr[ií]culas?", "", s, flags=re.IGNORECASE).strip()
            return s or "—"

        tl["referencia"] = tl["referencia"].map(_clean_ref)
    if "detalhe" in tl.columns:
        tl["detalhe"] = tl["detalhe"].astype(str).str.replace(
            r"(?i)\bmatr[ií]cula\b", "caso", regex=True
        )
    return tl


def render_cliente_html(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    periodo_label: str,
    out_path: Path,
    analytics: BRBAnalytics | None = None,
    data_fim=None,
) -> Path:
    """Gera relatório padrão cliente — página única, sem abas nem badges."""
    gerado = datetime.now().strftime("%d/%m/%Y às %H:%M")
    nome = CLIENT["nome_curto"]
    total_av = metrics.conforme_sim + metrics.conforme_nao
    top_alert = _top_attack_alert(bundle)
    proc_df = _group_procedentes(bundle.contestacao)
    fg_perfil_df, fg_total, fg_dup = _fg_perfil_por_tipo(bundle.falhas_gerais)
    fg_analisados = max(0, fg_total - fg_dup)
    proc_base = _procedentes_contestacao(bundle.contestacao)
    tipo_df, tipo_inferred = _group_procedentes_by_tipo(proc_base)
    improc_base = _improcedentes_contestacao(bundle.contestacao)
    improc_tipo_df, _ = _group_contestacao_by_tipo(improc_base)
    cover_intro = _cover_intro(metrics, proc_df)

    _, multi_proto, extra_rows = _na_falha_protocol_stats(bundle.na_falhas)

    # --- Sumário executivo ---
    secondary_kpis = (
        '<details class="section-details">'
        '<summary><span class="details-chevron" aria-hidden="true"></span>'
        '<span class="details-label">Indicadores operacionais completos</span></summary>'
        '<div class="details-body">'
        + _executive_kpi_list(metrics, total_av)
        + _na_volume_flow_box(metrics, multi_proto, extra_rows)
        + "</div></details>"
    )
    summary = (
        '<div class="executive-summary report-panel is-visible" id="sumario">'
        "<h2>Sumário executivo</h2>"
        + _executive_kpi_grid(metrics)
        + _executive_narrative(
            metrics, proc_df, top_alert, fg_total, fg_perfil_df, tipo_df, analytics
        )
        + _process_diagram_html()
        + secondary_kpis
        + "</div>"
    )

    # --- 1. Procedência ---
    donut = ""
    if total_av:
        alt_donut = (
            f"Gráfico de procedência: {metrics.conforme_nao} procedentes "
            f"({metrics.pct_procedente}%) e {metrics.conforme_sim} improcedentes "
            f"({metrics.pct_improcedente}%) em {total_av} avaliações"
        )
        b64 = chart_donut(
            ["Procedente", "Improcedente"],
            [metrics.conforme_nao, metrics.conforme_sim],
            "Resultado das avaliações",
            colors=[C["red"], C["green"]],
            center_label=f"{total_av}\navaliações",
        )
        if b64:
            donut = _chart_card(
                img_tag(b64, alt_donut),
                "Procedente (CONFORME = Não) · Improcedente (CONFORME = Sim).",
                lead=(
                    f"Das <b>{total_av}</b> avaliações, "
                    f"<b>{metrics.conforme_nao}</b> ({metrics.pct_procedente}%) foram procedentes "
                    f"e <b>{metrics.conforme_sim}</b> ({metrics.pct_improcedente}%) improcedentes."
                ),
            )
    improc_inferred = bool(
        improc_base.get("_tipo_inferido", pd.Series(dtype=bool)).any()
    ) if not improc_base.empty else False
    tipo_any_inferred = tipo_inferred or improc_inferred
    sec2_exec = (
        _sec2_exec_text(metrics, proc_df, total_av, tipo_df, improc_tipo_df)
        + donut
        + _tipo_fg_contestacao_bridge(fg_perfil_df, tipo_df)
    )
    sec2_detail = (
        _simple_table(
            ["Resultado", "Quantidade", "Percentual"],
            [
                ["Total avaliado", total_av or "N/D", "100%"],
                ["Procedente (CONFORME = Não)", metrics.conforme_nao, f"{metrics.pct_procedente}%"],
                ["Improcedente (CONFORME = Sim)", metrics.conforme_sim, f"{metrics.pct_improcedente}%"],
            ],
            numeric_cols={1, 2},
        )
        + _sec2_tipo_dual_block(
            tipo_df,
            improc_tipo_df,
            tipo_any_inferred,
            proc_base=proc_base,
            improc_base=improc_base,
        )
    )
    sec2 = _details_block(sec2_exec, sec2_detail, count=3 if total_av else None)

    # --- 2. Detalhamento procedentes ---
    proc_rows = (
        [
            [
                r["Motivo"],
                r["Tipo de procedência"],
                r["Quantidade"],
                r["Pct"],
                r["Exemplos"],
            ]
            for _, r in proc_df.iterrows()
        ]
        if not proc_df.empty
        else []
    )
    sec3_exec = _sec3_exec_text(proc_df)
    sec3_detail = '<h4 class="sub-h">Detalhamento por motivo/cenário</h4>'
    if proc_rows and proc_df["Tipo de procedência"].astype(str).str.endswith("*").any():
        sec3_detail += (
            '<p class="note">* Motivo com mais de um tipo de procedência entre os casos agrupados.</p>'
        )
    sec3_detail += _simple_table(
        [
            "Motivo / cenário",
            "Tipo de procedência",
            "Qtd",
            "% do total procedente",
            "Exemplos protocolos",
        ],
        proc_rows,
        numeric_cols={2, 3},
        badge_cols={1},
    )
    sec3 = _details_block(
        sec3_exec,
        sec3_detail,
        count=len(proc_rows) if proc_rows else None,
    )

    # --- Linha do tempo (por demanda / QI) ---
    sec_tl = _timeline_visual_section(bundle, data_fim=data_fim)

    # --- Volumes auditados × falhas notificadas ---
    sec_aud = _auditados_section(metrics, data_fim=data_fim)

    # --- Conclusão ---
    conclusao = _conclusion_bullets(
        metrics,
        proc_df,
        periodo_label,
        top_alert,
        tipo_df,
        fg_total=fg_total,
        fg_dup=fg_dup,
        fg_perfil_df=fg_perfil_df,
        data_fim=data_fim,
    )

    # --- Glossário ---
    glossario = (
        '<section class="report-section report-panel" id="glossario">'
        '<h2 class="section-title">Glossário</h2>'
        + _glossary_panel_html()
        + "</section>"
    )

    section_nav = _section_nav_html()
    nav_script = _section_nav_script()
    lightbox = _chart_lightbox_html()

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Relatório Executivo {html_lib.escape(nome)} — {_esc(periodo_label)}</title>
<style>{_client_css()}</style>
</head>
<body>
<div class="wrap">
  <header class="cover">
    <div class="cover-brand">
      <span class="cover-mark">{html_lib.escape(nome)}</span>
      <span class="cover-eyebrow">Relatório executivo · Qualidade &amp; CS</span>
    </div>
    <h1>Falhas, Notificação Ativa e Contestação</h1>
    <div class="cover-meta">Período analisado: <b>{_esc(periodo_label)}</b> · Gerado em {_esc(gerado)}</div>
    {cover_intro}
  </header>
  {section_nav}
  <div id="report-panels" class="report-panels">
  {summary}
  {_section("1.", "Resultado de procedência (time de Contestação)", sec2, "procedencia")}
  {_section("2.", "Detalhamento dos casos procedentes", sec3, "detalhe")}
  {_section("3.", "Volumes auditados × falhas notificadas", sec_aud, "auditados")}
  {_section("4.", "Linha do tempo", sec_tl, "timeline")}
  <section class="report-section report-panel" id="conclusao"><h2 class="section-title">Síntese e ressalvas</h2>{conclusao}</section>
  {glossario}
  </div>
  <footer class="footer">Confidencial · Uso interno · {_esc(nome)} · {_esc(gerado)}</footer>
</div>
{lightbox}
{nav_script}
</body>
</html>"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
