# -*- coding: utf-8 -*-
"""
Quality Pulse BRB — entregável cliente.
Estrutura e visual espelhados do quality-pulse-brb-exemplo.html; números do pipeline.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from report_brb import config_brb
from report_brb.brb_analytics import BRBAnalytics
from report_brb.brb_contestacao_temporal import contestacao_temporal as _contestacao_temporal
from report_brb.brb_filters import parse_excel_date
from report_brb.brb_format import format_int_br
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics
from report_brb.brb_normalize import padronizar_descricao, strip_accents
from report_brb.brb_render_cliente import (
    _build_cs_timeline_months,
    _find_fg_col,
    _fg_perfil_por_tipo,
    _group_procedentes,
    _group_procedentes_by_tipo,
    _normalize_tipo_falha_fg,
    _partial_month_note,
    _partial_month_ym,
    _period_key,
    _procedentes_contestacao,
)
from report_brb.brb_finding_guidance import FINDING_TYPE_GUIDANCE, cs_validation_items, is_sensitive_scenario
_META_PROCEDENCIA = 5.0
PULSE_EXEC_TABS = frozenset({"visao", "procedencia", "tempo", "causas", "evolucao"})
PULSE_ANALYTIC_TABS = frozenset({"auditoria", "metodo"})
_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "serasa_experian_logo.png"
_LOGO_SVG_PATH = Path(__file__).resolve().parent / "assets" / "serasa_experian_logo.svg"
_LOGO_DATA_URI: str | None = None


def _serasa_logo_data_uri() -> str:
    """Logo oficial embutida: SVG vetorial (nitidez) com fallback PNG HiDPI."""
    global _LOGO_DATA_URI
    if _LOGO_DATA_URI is not None:
        return _LOGO_DATA_URI
    import base64

    if _LOGO_SVG_PATH.is_file():
        raw = _LOGO_SVG_PATH.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        _LOGO_DATA_URI = f"data:image/svg+xml;base64,{b64}"
    elif _LOGO_PATH.is_file():
        b64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
        _LOGO_DATA_URI = f"data:image/png;base64,{b64}"
    else:
        _LOGO_DATA_URI = ""
    return _LOGO_DATA_URI


def _esc(x) -> str:
    return html_lib.escape("" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))


def _pct(n: float, digits: int = 1) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "—"
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _parse_pct_str(s) -> float:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return 0.0
    t = str(s).strip().replace("%", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _short_motivo(text: str, n: int = 42) -> str:
    s = re.sub(r"\s+", " ", str(text or "").strip())
    s = re.sub(r"(?i)^n[aã]o\s+sinalizado\s*[-–—:]\s*", "", s).strip()
    # Compact common long labels (data-driven cleanup, not hard numbers)
    replacements = (
        (r"(?i)^foto\s+do\s+cliente\s+divergente\s+com\b.*", "Foto cliente × documento"),
        (r"(?i)^foto\s+do\s+cliente\s+inv[aá]lida\b.*", "Foto do cliente inválida"),
        (r"(?i)^formata[cç][aã]o/?fonte\s+adulterada\b.*", "Formatação/fonte adulterada"),
    )
    for pat, rep in replacements:
        if re.match(pat, s):
            s = rep
            break
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _th_tip(label: str, tip: str, *, num: bool = False) -> str:
    """Cabeçalho com tooltip no hover."""
    cls = ' class="num tip"' if num else ' class="tip"'
    return f"<th{cls} data-tip=\"{_esc(tip)}\">{_esc(label)}</th>"


def _period_human(inicio, fim, periodo_label: str = "") -> str:
    """Ex.: 01 jan — 29 jul 2026"""
    meses = {
        1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
        7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
    }
    if inicio is not None and fim is not None:
        return (
            f"{inicio.day:02d} {meses[inicio.month]} — "
            f"{fim.day:02d} {meses[fim.month]} {fim.year}"
        )
    m = re.search(
        r"(\d{2})/(\d{2})/(\d{4})\s*(?:a|–|-|até)\s*(\d{2})/(\d{2})/(\d{4})",
        periodo_label or "",
        flags=re.I,
    )
    if m:
        d1, mo1, y1, d2, mo2, y2 = map(int, m.groups())
        return f"{d1:02d} {meses[mo1]} — {d2:02d} {meses[mo2]} {y2}"
    return periodo_label or "—"


def _fmt_horas(n: float, digits: int = 1) -> str:
    """Formata horas: 12,3 h / 0,3 h."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "—"
    if abs(v) < 0.05:
        return "0 h"
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return f"{s.replace('.', ',')} h"


def _capacitacao_resumo(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    *,
    causa_top: str = "",
) -> dict:
    """KPIs de capacitação: esforço (sessão), cobertura e hora-pessoa estimada."""
    agentes = int(getattr(metrics, "treinamentos_agentes", 0) or 0)
    sessoes = int(getattr(metrics, "treinamentos_sessoes", 0) or 0)
    horas_sessao = float(getattr(metrics, "treinamentos_horas", 0) or 0)
    tema = "—"
    dur_med = 0.0
    sessoes_ok = 0

    tre_h = getattr(bundle, "treinamentos_horas", None)
    if tre_h is not None and not tre_h.empty and "horas" in tre_h.columns:
        h = tre_h.copy()
        if "SessionStatus" in h.columns:
            h = h[h["SessionStatus"].astype(str).str.lower().ne("cancelado")]
        sessoes_ok = len(h)
        if sessoes_ok:
            horas_sessao = round(float(h["horas"].sum()), 1)
            dur_med = float(h["horas"].median())
            if "Event: EventTitle" in h.columns:
                vc = h["Event: EventTitle"].astype(str).value_counts()
                if len(vc):
                    tema = str(vc.index[0])

    hora_pessoa = round(dur_med * agentes, 1) if agentes and dur_med else 0.0
    if not hora_pessoa and horas_sessao and agentes and sessoes_ok:
        # fallback: duração média implícita × agentes
        hora_pessoa = round((horas_sessao / sessoes_ok) * agentes, 1)

    tema_short = _short_motivo(tema, 48) if tema and tema != "—" else "—"
    causa_short = _short_motivo(causa_top, 48) if causa_top else "—"
    t_up = (tema or "").upper()
    c_up = (causa_top or "").upper()
    # Famílias de conteúdo: "Quadrilha" no treino cobre formatação/fonte, sobreposição etc.
    familias = (
        frozenset({"QUADRILHA", "FORMAT", "ADULTER", "SOBREPOS", "RASURA", "DESALINH", "FONTE"}),
        frozenset({"ILEG", "INCOMPLET", "DETERIOR", "PASSAVEL"}),
        frozenset({"FRAUDADOR", "FACEMATCH", "BIOMETRIA", "SELFIE"}),
    )
    alinhado = False
    if tema and causa_top:
        for fam in familias:
            if any(k in t_up for k in fam) and any(k in c_up for k in fam):
                alinhado = True
                break

    return {
        "horas_sessao": horas_sessao,
        "sessoes": sessoes_ok or sessoes,
        "agentes": agentes,
        "hora_pessoa": hora_pessoa,
        "dur_med": dur_med,
        "tema": tema,
        "tema_short": tema_short,
        "causa_short": causa_short,
        "alinhado": alinhado,
    }


def _monthly_series(bundle: BRBDataBundle, data_fim=None) -> list[dict]:
    months = _build_cs_timeline_months(bundle)
    cutoff_ym = pd.Timestamp(data_fim).strftime("%Y-%m") if data_fim is not None else None
    if cutoff_ym:
        months = [m for m in months if str(m.get("ym") or "") <= cutoff_ym]
    partial_ym = _partial_month_ym(data_fim)
    out = []
    for m in months:
        aval = int(m.get("cont_total") or 0)
        proc = int(m.get("cont_proc") or 0)
        taxa = round(100.0 * proc / aval, 1) if aval else None
        ym_key = str(m["ym"]) if m.get("ym") is not None else ""
        horas = float(m.get("treinamentos_horas") or 0.0)
        out.append(
            {
                "ym": m.get("ym"),
                "ym_key": ym_key,
                "label": m.get("label") or "",
                "short": (m.get("label") or "").split(" ")[0][:3],
                "falhas": int(m.get("na_falhas") or 0),
                "avaliacoes": aval,
                "procedentes": proc,
                "horas": horas,
                "taxa": taxa,
                "parcial": bool(partial_ym and ym_key == partial_ym),
            }
        )
    return out


def _spark_heights(series: list[dict]) -> list[int]:
    taxas = [r["taxa"] for r in series if r["taxa"] is not None]
    if not taxas:
        return [10] * len(series)
    peak = max(taxas) or 1.0
    return [
        8 if r["taxa"] is None else max(8, int(round(100 * r["taxa"] / peak)))
        for r in series
    ]


def _manual_share(tipo_df: pd.DataFrame) -> tuple[float, int, int]:
    if tipo_df.empty:
        return 0.0, 0, 0
    col = "Casos" if "Casos" in tipo_df.columns else "Quantidade"
    total = int(tipo_df[col].sum())
    man = tipo_df[tipo_df["Tipo de procedência"].astype(str).str.startswith("Manual")]
    n_man = int(man[col].sum()) if not man.empty else 0
    pct = round(100.0 * n_man / total, 0) if total else 0.0
    return pct, n_man, total


def _month_label(ts: pd.Timestamp) -> str:
    short = tuple(_MES_FULL.keys())[ts.month - 1]
    return f"{short.capitalize()} {ts.year}"


def _presentation_motivo(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if value.isupper():
        value = value.capitalize()
    value = re.sub(r"(?i)^doc\.\s*", "Documento ", value)
    return value[:1].upper() + value[1:] if value else value


def _motivo_recurrence_data(
    proc: pd.DataFrame,
    motivo_df: pd.DataFrame,
    *,
    limit: int = 5,
    data_fim=None,
) -> list[dict]:
    """Série mensal dos principais motivos pela data da análise contestada."""
    if proc.empty or motivo_df.empty or "_motivo" not in proc.columns:
        return []
    work = proc.copy()
    analysis_dates = (
        parse_excel_date(work["Data de Análise"])
        if "Data de Análise" in work.columns
        else pd.Series(pd.NaT, index=work.index)
    )
    reference_dates = (
        parse_excel_date(work["Data"])
        if "Data" in work.columns
        else pd.Series(pd.NaT, index=work.index)
    )
    work["_recurrence_date"] = analysis_dates.where(analysis_dates.notna(), reference_dates)
    work = work[work["_recurrence_date"].notna()].copy()
    if data_fim is not None:
        work = work[work["_recurrence_date"].dt.normalize() <= pd.Timestamp(data_fim)].copy()
    if work.empty:
        return []
    work["_recurrence_month"] = work["_recurrence_date"].dt.to_period("M")

    count_col = "Quantidade" if "Quantidade" in motivo_df.columns else "Casos"
    result = []
    for _, summary in motivo_df.head(limit).iterrows():
        motivo = str(summary.get("Motivo") or "").strip()
        subset = work[work["_motivo"].astype(str).eq(motivo)]
        if subset.empty:
            continue
        events = []
        previous = None
        for period, group in subset.groupby("_recurrence_month", sort=True):
            protocols = group.get("Protocolo", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
            protocols_n = int(protocols[protocols.ne("")].nunique())
            gap = int(period.ordinal - previous.ordinal) if previous is not None else None
            events.append(
                {
                    "period": str(period),
                    "label": _month_label(period.to_timestamp()),
                    "occurrences": int(len(group)),
                    "protocols": protocols_n,
                    "gap_months": gap,
                }
            )
            previous = period
        result.append(
            {
                "source_key": "contestacao",
                "source_label": "Contestação · Falha confirmada",
                "motivo": _presentation_motivo(motivo),
                "short": _short_motivo(_presentation_motivo(motivo), 54),
                "total": int(len(subset)),
                "unique_protocols": int(
                    subset.get("Protocolo", pd.Series(dtype=str))
                    .fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
                ),
                "months": len(events),
                "events": events,
            }
        )
    return result


def _automatic_recurrence_data(
    fg: pd.DataFrame,
    *,
    limit: int = 5,
    data_fim=None,
) -> list[dict]:
    """Série mensal dos principais achados automáticos da auditoria."""
    if fg.empty:
        return []
    work = fg.copy()
    if "duplicado_contestacao" in work.columns:
        work = work[~work["duplicado_contestacao"].fillna(False)].copy()
    type_col = next(
        (
            col for col in work.columns
            if "tipo" in strip_accents(str(col)).lower()
            and "falha" in strip_accents(str(col)).lower()
        ),
        None,
    )
    if not type_col:
        return []
    type_norm = work[type_col].fillna("").astype(str).map(
        lambda value: strip_accents(value).strip().upper()
    )
    work = work[type_norm.isin({"AUTOMATICO", "AUTO", "AUTOMACAO", "SISTEMICO", "SISTEMICA", "MOTOR", "REGRA DE NEGOCIO"})].copy()
    if work.empty:
        return []

    scenario_col = next(
        (col for col in ("Novo cenário", "Cenário", "descricao_padrao") if col in work.columns),
        None,
    )
    if not scenario_col or "Data de Análise" not in work.columns:
        return []
    work["_motivo_auto"] = work[scenario_col].fillna("").astype(str).map(padronizar_descricao)
    work["_auto_date"] = parse_excel_date(work["Data de Análise"])
    work = work[work["_motivo_auto"].astype(str).str.strip().ne("") & work["_auto_date"].notna()].copy()
    if data_fim is not None:
        work = work[work["_auto_date"].dt.normalize() <= pd.Timestamp(data_fim)].copy()
    if work.empty:
        return []
    work["_auto_month"] = work["_auto_date"].dt.to_period("M")
    visible = work["_motivo_auto"].astype(str).map(strip_accents).str.upper()
    work = work[~visible.str.contains("FACE EM BASE DE FRAUDADORES", na=False)].copy()
    counts = work["_motivo_auto"].value_counts().head(limit)
    result = []
    for motivo, total in counts.items():
        subset = work[work["_motivo_auto"].eq(motivo)]
        events = []
        previous = None
        for period, group in subset.groupby("_auto_month", sort=True):
            protocols = group.get("Protocolo", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
            gap = int(period.ordinal - previous.ordinal) if previous is not None else None
            events.append(
                {
                    "period": str(period),
                    "label": _month_label(period.to_timestamp()),
                    "occurrences": int(len(group)),
                    "protocols": int(protocols[protocols.ne("")].nunique()),
                    "gap_months": gap,
                }
            )
            previous = period
        result.append(
            {
                "source_key": "automatico",
                "source_label": "Auditoria · Automático",
                "motivo": _presentation_motivo(str(motivo)),
                "short": _short_motivo(_presentation_motivo(str(motivo)), 54),
                "total": int(total),
                "unique_protocols": int(
                    subset.get("Protocolo", pd.Series(dtype=str))
                    .fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
                ),
                "months": len(events),
                "events": events,
            }
        )
    return result


def _automatic_opportunity(text: str) -> str:
    key = strip_accents(str(text or "")).upper()
    if "TOMADOR" in key and "ASSINA" in key:
        return "Revisar a regra de detecção de assinatura, exceções e evidências usadas na decisão automática."
    if "FORMAT" in key or "FONTE" in key or "DESALINH" in key:
        return "Calibrar critérios de formatação e ampliar exemplos limítrofes no conjunto de validação."
    if "ILEG" in key or "DETERIOR" in key or "QUALIDADE" in key:
        return "Revisar limiares de qualidade da imagem e o direcionamento para análise manual."
    if "TIPIFIC" in key:
        return "Validar o mapeamento das saídas automáticas para a tipificação registrada."
    if "DIVERG" in key or "SELFIE" in key or "CPF" in key:
        return "Revisar critérios de comparação e testar casos limítrofes com amostra direcionada."
    if "SOBREPOS" in key:
        return "Calibrar a detecção de sobreposição e revisar falsos positivos e falsos negativos."
    return "Revisar regra, evidência automática e exceções em uma amostra direcionada."


def _automatic_contestation_data(proc: pd.DataFrame, *, data_fim=None) -> list[dict]:
    if proc.empty or "_tipo_procedencia" not in proc.columns:
        return []
    work = proc[proc["_tipo_procedencia"].eq("Automático")].copy()
    if work.empty:
        return []
    dates = (
        parse_excel_date(work["Data de Análise"])
        if "Data de Análise" in work.columns
        else pd.Series(pd.NaT, index=work.index)
    )
    work["_auto_cont_date"] = dates
    if data_fim is not None:
        work = work[work["_auto_cont_date"].isna() | (work["_auto_cont_date"].dt.normalize() <= pd.Timestamp(data_fim))]
    result = []
    for motivo, group in work.groupby("_motivo", dropna=False):
        name = _presentation_motivo(str(motivo))
        valid_dates = group["_auto_cont_date"].dropna()
        protocols = group.get("Protocolo", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
        result.append(
            {
                "motivo": name,
                "short": _short_motivo(name, 50),
                "cases": int(len(group)),
                "protocols": int(protocols[protocols.ne("")].nunique()),
                "period": _month_label(valid_dates.max()) if len(valid_dates) else "Data não informada",
                "opportunity": _automatic_opportunity(name),
            }
        )
    return sorted(result, key=lambda item: (-item["cases"], item["motivo"]))


def _fg_predominante(fg_tipo_df: pd.DataFrame) -> str:
    if fg_tipo_df.empty or "Casos" not in fg_tipo_df.columns:
        return "—"
    row = fg_tipo_df.sort_values("Casos", ascending=False).iloc[0]
    return str(row["Tipo de falha"])


def _train_span_label(series: list[dict]) -> str:
    with_t = [r for r in series if (r.get("horas") or 0) > 0]
    if not with_t:
        return "no período"
    first = with_t[0]["short"].lower()
    last = with_t[-1]["short"].lower()
    if first == last:
        return first
    return f"{first}–{last}"


def _combo_chart_html(series: list[dict]) -> str:
    """Combo: pontos = taxa; barras verdes = horas de capacitação; delta pp."""
    if not series:
        return ""
    max_taxa = max((r["taxa"] or 0) for r in series) or 1.0
    max_h = max((r.get("horas") or 0) for r in series) or 1.0
    max_bottom = 153
    max_train = 112

    cols = []
    prev = None
    for r in series:
        taxa = r["taxa"]
        if taxa is None:
            delta = '<span class="delta">—</span>'
            bottom = 20
            taxa_lbl = "—"
        else:
            bottom = max(20, int(round(max_bottom * taxa / max_taxa)))
            taxa_lbl = f"{_pct(taxa)}%"
            if prev is None:
                delta = '<span class="delta">base</span>'
            else:
                d = round(taxa - prev, 1)
                if abs(d) < 0.05:
                    delta = '<span class="delta">0 p.p.</span>'
                else:
                    cls = "delta up" if d > 0 else "delta"
                    sign = "+" if d > 0 else "−"
                    delta = f'<span class="{cls}">{sign}{_pct(abs(d))} p.p.</span>'
            prev = taxa
        horas = float(r.get("horas") or 0)
        th = max(2, int(round(max_train * horas / max_h))) if horas else 2
        # rótulo curto na barra (sem " h" para caber)
        if horas:
            hs = f"{horas:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            train_inner = f"<b>{hs}</b>"
        else:
            train_inner = ""
        partial_cls = " partial-bg" if r["parcial"] else ""
        label = f'{_esc(r["short"])}{"*" if r["parcial"] else ""}'
        cols.append(
            f'<div class="cm{partial_cls}">{delta}'
            f'<div class="point" style="bottom:{bottom}px"><b>{_esc(taxa_lbl)}</b><i></i></div>'
            f'<div class="train" style="height:{th}px">{train_inner}</div>'
            f"<small>{label}</small></div>"
        )
    return "".join(cols)


def _bars_procedencia_html(series: list[dict]) -> str:
    """Barras de tendência — meses parciais ficam de fora (alinhado ao hero/spark)."""
    trend = [r for r in series if r["taxa"] is not None and not r.get("parcial")]
    if not trend:
        trend = [r for r in series if r["taxa"] is not None]
    if not trend:
        return '<p class="muted">Sem tendência de procedência no período.</p>'
    max_taxa = max((r["taxa"] or 0) for r in trend) or 1.0
    max_px = 192
    parts = []
    for r in trend:
        px = max(8, int(round(max_px * r["taxa"] / max_taxa)))
        label = _esc(r["short"])
        parts.append(
            f'<div class="bar"><b>{_pct(r["taxa"])}%</b>'
            f'<i style="height:{px}px"></i><small>{label}</small></div>'
        )
    return "".join(parts)


def _build_pulse_nav(*, exec_mode: bool = True) -> str:
    spec = (
        ("visao", "Visão executiva", False),
        ("procedencia", "Procedência", False),
        ("tempo", "Tempo e dispersão", False),
        ("causas", "Motivos das falhas", False),
        ("auditoria", "Auditoria × falhas", True),
        ("evolucao", "Evolução mensal", False),
        ("metodo", "Contexto", True),
    )
    btns = []
    for tid, label, analytic in spec:
        cls_parts = []
        if tid == "visao":
            cls_parts.append("active")
        if exec_mode and analytic:
            cls_parts.extend(["nav-tab-analytic", "hidden"])
        cls_attr = f' class="{" ".join(cls_parts)}"' if cls_parts else ""
        btns.append(f"<button{cls_attr} data-tab=\"{tid}\">{label}</button>")
    toggle = ""
    if exec_mode:
        toggle = (
            '<button type="button" class="btn-analytic-toggle" id="toggle-analytic" '
            'aria-pressed="false">Ver detalhes analíticos</button>'
        )
    return f'<div class="tabs">{"".join(btns)}</div>{toggle}'


_MES_FULL = {
    "jan": "janeiro", "fev": "fevereiro", "mar": "março", "abr": "abril",
    "mai": "maio", "jun": "junho", "jul": "julho", "ago": "agosto",
    "set": "setembro", "out": "outubro", "nov": "novembro", "dez": "dezembro",
}


def _hero_scenario(
    series: list[dict],
    *,
    taxa_acum: float,
    proc_n: int,
    total_aval: int,
    meta_procedencia: float = _META_PROCEDENCIA,
) -> dict:
    """Cenários de copy do hero: queda | alta | estavel | fallback."""
    with_taxa = [r for r in series if r["taxa"] is not None and r["avaliacoes"] > 0]
    closed = [r for r in with_taxa if not r.get("parcial")]
    partial_latest = with_taxa[-1] if with_taxa and with_taxa[-1].get("parcial") else None
    trend_series = closed or with_taxa
    last = trend_series[-1] if trend_series else None
    prev = trend_series[-2] if len(trend_series) >= 2 else None
    peak = max(trend_series, key=lambda r: r["taxa"]) if trend_series else None
    last_taxa = last["taxa"] if last else None
    peak_taxa = peak["taxa"] if peak else None
    last_short = (last["short"] or "").lower() if last else "—"
    last_partial = bool(last and last["parcial"])
    peak_short = (peak["short"] or "").lower() if peak else "—"
    peak_month_full = _MES_FULL.get(peak_short, peak_short)
    prev_short = (prev["short"] or "").lower() if prev else "—"
    drop_pp = (
        round(peak_taxa - last_taxa, 1)
        if peak_taxa is not None and last_taxa is not None
        else None
    )
    mom_pp = (
        round(last_taxa - prev["taxa"], 1)
        if last_taxa is not None and prev is not None and prev["taxa"] is not None
        else None
    )

    signal_label = (
        f"Taxa em {last_short}" + (" · parcial" if last_partial else "")
        if last_taxa is not None
        else "Taxa acumulada"
    )

    # Prioridade: alta m/m ≥ 1 pp; senão queda vs pico ≥ 1 pp; senão estável se série ok
    if mom_pp is not None and mom_pp >= 1.0 and (last_taxa or 0) > meta_procedencia:
        scenario = "alta"
        hero_h1 = "A taxa de falhas confirmadas subiu.<br>Hora de priorizar o motivo #1."
        hero_p = (
            "O Quality Overview aponta a alta recente, a concentração do principal motivo "
            "e frentes para o CS validar com Operação/Qualidade."
        )
        trend_chip = "● Atenção necessária"
        signal_cls = "up"
        signal_delta = f"↑ {_pct(mom_pp)} p.p. vs {prev_short}"
        decision_h3 = "Conter a alta observada"
        decision_p = "Priorizar checklist do motivo #1 e amostra semanal."
        insight = (
            "! Alta recente",
            f"De {_pct(prev['taxa'])}% em {prev_short} para {_pct(last_taxa)}% em {last_short}"
            + (" parcial." if last_partial else "."),
        )
        evo_lead = (
            f"A taxa de falhas confirmadas subiu {_pct(mom_pp)} p.p. vs {prev_short}"
            f" enquanto a capacitação segue no radar."
        )
    elif drop_pp is not None and drop_pp >= 1.0 and last_taxa is not None:
        scenario = "queda"
        hero_h1 = "A taxa de falhas confirmadas caiu.<br>Agora é hora de sustentar."
        hero_p = (
            "O Quality Overview mostra a queda recente, onde o risco ainda se concentra "
            "e o que o CS pode validar para sustentar."
        )
        trend_chip = "● Tendência favorável"
        signal_cls = "down"
        signal_delta = f"↓ {_pct(drop_pp)} p.p. desde o pico de {peak_month_full}"
        decision_h3 = "Proteger o ganho observado"
        decision_p = "Avaliar checklist de formatação e amostra semanal."
        insight = (
            "✓ Queda recente",
            f"De {_pct(peak_taxa)}% em {peak_month_full} para {_pct(last_taxa)}% em {last_short}"
            + (" parcial." if last_partial else "."),
        )
        evo_lead = (
            f"A taxa de falhas confirmadas caiu no período em que houve capacitação."
        )
    elif last_taxa is not None and (mom_pp is None or abs(mom_pp) < 1.0):
        scenario = "estavel"
        hero_h1 = "Falhas confirmadas em patamar estável.<br>Manter o olhar na causa dominante."
        hero_p = (
            "O Quality Overview resume o período, o motivo dominante "
            "e pontos de acompanhamento para o CS."
        )
        trend_chip = "● Leitura estável"
        signal_cls = "down"
        signal_delta = f"≈ {_pct(last_taxa)}% · sem variação relevante"
        decision_h3 = "Sustentar o patamar atual"
        decision_p = "Manter amostra e reforço do motivo #1."
        insight = (
            "● Patamar estável",
            f"Taxa em {last_short}: {_pct(last_taxa)}%"
            + (" (parcial)." if last_partial else "."),
        )
        evo_lead = "A taxa de falhas confirmadas se manteve estável no fim do período."
    else:
        scenario = "fallback"
        hero_h1 = "Pulso da operação<br>de Qualidade &amp; CS"
        hero_p = (
            f"No período, <b>{format_int_br(proc_n)}</b> procedentes em "
            f"<b>{format_int_br(total_aval)}</b> contestações recebidas "
            f"(<b>{_pct(taxa_acum)}%</b>)."
        )
        trend_chip = "● Leitura do período"
        signal_cls = "down"
        signal_delta = f"Acumulado {_pct(taxa_acum)}%"
        signal_label = "Taxa acumulada" if last_taxa is None else signal_label
        decision_h3 = "Validar os sinais do período"
        decision_p = "Priorizar causa dominante e amostra com Qualidade."
        insight = (
            "i Leitura do período",
            f"Taxa acumulada de falhas confirmadas: {_pct(taxa_acum)}% "
            f"({format_int_br(proc_n)} de {format_int_br(total_aval)}).",
        )
        evo_lead = "Resultado e resposta operacional na mesma leitura."

    return {
        "scenario": scenario,
        "hero_h1": hero_h1,
        "hero_p": hero_p,
        "trend_chip": trend_chip,
        "signal_label": signal_label,
        "signal_cls": signal_cls,
        "signal_delta": signal_delta,
        "decision_h3": decision_h3,
        "decision_p": decision_p,
        "insight": insight,
        "evo_lead": evo_lead,
        "last": last,
        "peak": peak,
        "last_taxa": last_taxa,
        "peak_taxa": peak_taxa,
        "peak_month_full": peak_month_full,
        "last_short": last_short,
        "last_partial": last_partial,
        "drop_pp": drop_pp,
        "mom_pp": mom_pp,
        "partial_latest": partial_latest,
    }


def _cs_plan_context() -> dict[str, str | float]:
    """Metas configuráveis do CS, sem expor contexto interno no report executivo."""
    client = config_brb.CLIENT or {}
    goals = client.get("cs_goals") or {}
    return {
        "confirmed_rate": float(goals.get("confirmed_rate", _META_PROCEDENCIA)),
        "aged_over_90_pct": float(goals.get("aged_over_90_pct", 10.0)),
        "cause_concentration_pct": float(goals.get("cause_concentration_pct", 35.0)),
    }


_MONTH_CHIP_LABELS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def _infer_pulse_default_filter(
    data_inicio,
    data_fim,
    years: list[int],
    generated_at: datetime,
) -> dict:
    default_year = years[-1] if years else generated_at.year
    preset = "ytd"
    default_month: int | None = None
    if data_inicio and data_fim:
        default_year = int(data_inicio.year)
        if (
            data_inicio.year == data_fim.year
            and data_inicio.month == data_fim.month
            and data_inicio.day == 1
        ):
            preset = "month"
            default_month = int(data_inicio.month) - 1
        elif (
            data_inicio.month == 1
            and data_inicio.day == 1
            and data_fim.month == 12
            and data_fim.day == 31
            and data_inicio.year == data_fim.year
        ):
            preset = "ano"
    return {"preset": preset, "year": default_year, "month": default_month}


def _motivos_by_month(bundle: BRBDataBundle) -> dict[str, list[dict]]:
    proc = _procedentes_contestacao(bundle.contestacao)
    if proc.empty or "Data" not in proc.columns or "_motivo" not in proc.columns:
        return {}
    out: dict[str, list[dict]] = {}
    work = proc.copy()
    work["_ym"] = work["Data"].map(_period_key)
    for ym, grp in work.dropna(subset=["_ym"]).groupby("_ym", sort=True):
        ym_key = str(ym)
        total = len(grp)
        if not total:
            continue
        rows = []
        for motivo, n in grp["_motivo"].value_counts().head(8).items():
            rows.append(
                {
                    "motivo": str(motivo),
                    "n": int(n),
                    "pct": round(100.0 * int(n) / total, 1),
                }
            )
        out[ym_key] = rows
    return out


def _build_pulse_period_payload(
    bundle: BRBDataBundle,
    *,
    data_inicio=None,
    data_fim=None,
    generated_at: datetime | None = None,
) -> dict:
    generated_at = generated_at or datetime.now()
    series = _monthly_series(bundle, data_fim)
    months_out: list[dict] = []
    for row in series:
        ym = row.get("ym")
        if ym is None:
            continue
        ts = pd.Period(str(ym), freq="M").to_timestamp()
        aval = int(row.get("avaliacoes") or 0)
        proc = int(row.get("procedentes") or 0)
        months_out.append(
            {
                "ym": str(ym),
                "year": int(ts.year),
                "month": int(ts.month) - 1,
                "label": row.get("label") or "",
                "short": row.get("short") or "",
                "falhas": int(row.get("falhas") or 0),
                "avaliacoes": aval,
                "procedentes": proc,
                "improcedentes": max(0, aval - proc),
                "horas": float(row.get("horas") or 0),
                "taxa": row.get("taxa"),
                "parcial": bool(row.get("parcial")),
            }
        )
    years = sorted({m["year"] for m in months_out})
    defaults = _infer_pulse_default_filter(data_inicio, data_fim, years, generated_at)
    return {
        "months": months_out,
        "motivosByYm": _motivos_by_month(bundle),
        "years": years,
        "defaultYear": defaults["year"],
        "defaultPreset": defaults["preset"],
        "defaultMonth": defaults["month"],
        "partialYm": _partial_month_ym(data_fim) or "",
        "monthLabels": list(_MONTH_CHIP_LABELS),
        "mesFull": dict(_MES_FULL),
        "generatedAt": generated_at.strftime("%Y-%m-%d"),
        "metaProcedencia": _META_PROCEDENCIA,
    }


def _pulse_period_filter_bar_html() -> str:
    month_btns = "".join(
        f'<button type="button" class="period-chip" data-month="{i}">{lbl}</button>'
        for i, lbl in enumerate(_MONTH_CHIP_LABELS)
    )
    return f"""
  <div class="period-bar" id="pulse-period-bar">
    <div class="period-bar-inner">
      <div class="period-row">
        <span class="period-bar-label">Período</span>
        <div class="period-chips" id="preset-chips" role="group" aria-label="Presets de período">
          <button type="button" class="period-chip" data-preset="ytd">YTD</button>
          <button type="button" class="period-chip" data-preset="mtd">MTD</button>
          <button type="button" class="period-chip" data-preset="ano">Ano</button>
        </div>
      </div>
      <div class="period-row" id="year-row">
        <span class="period-bar-label">Ano</span>
        <div class="period-chips" id="year-chips" role="group" aria-label="Ano"></div>
      </div>
      <div class="period-row">
        <span class="period-bar-label">Mês</span>
        <div class="period-chips" id="month-chips" role="group" aria-label="Mês">{month_btns}</div>
      </div>
      <p class="period-hint">Exibindo: <strong id="period-display">—</strong></p>
    </div>
  </div>"""


def _pulse_period_filter_js() -> str:
    return r"""
(function(){
  const DATA=window.__PULSE_PERIOD__;
  if(!DATA||!DATA.months||!DATA.months.length) return;
  const fmtInt=n=>Number(n||0).toLocaleString("pt-BR");
  const fmtPct=(n,d=1)=>{if(n==null||Number.isNaN(n)) return "—"; let s=Number(n).toFixed(d).replace(".",","); return s.replace(/,?0+$/,"").replace(/,$/,"");};
  const mesFull=DATA.mesFull||{};
  const monthLabels=DATA.monthLabels||[];
  let state={year:DATA.defaultYear,preset:DATA.defaultPreset||"ytd",month:DATA.defaultMonth};

  function monthsForYear(y){return DATA.months.filter(m=>m.year===y);}
  function lastClosedMonth(rows){const closed=rows.filter(m=>!m.parcial); return closed.length?closed[closed.length-1]:rows[rows.length-1];}
  function selectedRows(){
    const rows=monthsForYear(state.year);
    if(!rows.length) return [];
    if(state.preset==="month"&&state.month!=null){
      return rows.filter(m=>m.month===state.month);
    }
    if(state.preset==="mtd"){
      const partial=rows.find(m=>m.parcial);
      if(partial) return [partial];
      const last=rows[rows.length-1];
      return last?[last]:[];
    }
    if(state.preset==="ano") return rows;
    return rows.filter(m=>!m.parcial||m===rows[rows.length-1]);
  }
  function aggregate(rows){
    const aval=rows.reduce((s,m)=>s+(m.avaliacoes||0),0);
    const proc=rows.reduce((s,m)=>s+(m.procedentes||0),0);
    const improc=rows.reduce((s,m)=>s+(m.improcedentes||0),0);
    const horas=rows.reduce((s,m)=>s+(m.horas||0),0);
    const taxa=aval?Math.round(1000*proc/aval)/10:null;
    return {aval,proc,improc,horas,taxa};
  }
  function mergeMotivos(rows){
    const acc={};
    rows.forEach(m=>{
      (DATA.motivosByYm[m.ym]||[]).forEach(x=>{
        acc[x.motivo]=(acc[x.motivo]||0)+x.n;
      });
    });
    const total=Object.values(acc).reduce((s,n)=>s+n,0);
    return Object.entries(acc).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([motivo,n])=>({
      motivo,n,pct:total?Math.round(1000*n/total)/10:0
    }));
  }
  function periodLabel(rows){
    if(!rows.length) return "Sem dados no recorte";
    if(state.preset==="month"&&state.month!=null){
      const m=rows[0];
      return (m.label||`${monthLabels[state.month]}/${state.year}`);
    }
    if(state.preset==="mtd"){
      const m=rows[0];
      return (m.label||`MTD ${state.year}`)+(m.parcial?" · parcial":"");
    }
    if(state.preset==="ano") return `Ano ${state.year}`;
    const first=rows[0], last=rows[rows.length-1];
    return `${first.short||""} – ${last.short||""} ${state.year}`.trim();
  }
  function sparkHtml(rows){
    const taxas=rows.map(r=>r.taxa).filter(t=>t!=null);
    const peak=Math.max(...taxas,1);
    return rows.map(r=>{
      const h=r.taxa==null?8:Math.max(8,Math.round(100*r.taxa/peak));
      return `<i style="height:${h}%"></i>`;
    }).join("");
  }
  function barsHtml(rows){
    const trend=rows.filter(r=>r.taxa!=null&&!r.parcial);
    const use=trend.length?trend:rows.filter(r=>r.taxa!=null);
    const peak=Math.max(...use.map(r=>r.taxa||0),1);
    return use.map((r,i)=>{
      const px=Math.max(8,Math.round(192*(r.taxa||0)/peak));
      const cls=i===use.length-1?"bar":"bar";
      const barCls=i===use.length-1?`${cls} last`:cls;
      return `<div class="${barCls}"><b>${fmtPct(r.taxa)}%</b><i style="height:${px}px"></i><small>${r.short||""}</small></div>`;
    }).join("");
  }
  function comboHtml(rows){
    const maxTaxa=Math.max(...rows.map(r=>r.taxa||0),1);
    const maxH=Math.max(...rows.map(r=>r.horas||0),1);
    let prev=null;
    return rows.map(r=>{
      const taxa=r.taxa;
      let delta,deltaCls,bottom,taxaLbl;
      if(taxa==null){delta='<span class="delta">—</span>';bottom=20;taxaLbl="—";}
      else{
        bottom=Math.max(20,Math.round(153*taxa/maxTaxa));
        taxaLbl=`${fmtPct(taxa)}%`;
        if(prev==null) delta='<span class="delta">base</span>';
        else{
          const d=Math.round(10*(taxa-prev))/10;
          if(Math.abs(d)<0.05) delta='<span class="delta">0 p.p.</span>';
          else{
            deltaCls=d>0?"delta up":"delta";
            delta=`<span class="${deltaCls}">${d>0?"+":"−"}${fmtPct(Math.abs(d))} p.p.</span>`;
          }
        }
        prev=taxa;
      }
      const horas=r.horas||0;
      const th=horas?Math.max(2,Math.round(112*horas/maxH)):2;
      const hs=horas?String(horas).replace(".",","):"";
      const trainInner=horas?`<b>${hs}</b>`:"";
      const partialCls=r.parcial?" partial-bg":"";
      const label=`${r.short||""}${r.parcial?"*":""}`;
      return `<div class="cm${partialCls}">${delta}<div class="point" style="bottom:${bottom}px"><b>${taxaLbl}</b><i></i></div><div class="train" style="height:${th}px">${trainInner}</div><small>${label}</small></div>`;
    }).join("");
  }
  function paretoHtml(items){
    if(!items.length) return '<div class="row"><span class="rank">—</span><div><b>Sem dados no recorte</b></div><div class="value"><b>0</b></div></div>';
    return items.map((x,i)=>`<div class="row"><span class="rank">${String(i+1).padStart(2,"0")}</span><div><b>${x.motivo}</b><div class="track"><i style="width:${Math.min(100,x.pct)}%"></i></div></div><div class="value"><b>${fmtInt(x.n)}</b><small>${fmtPct(x.pct)}%</small></div></div>`).join("");
  }
  function heroCopy(rows, agg){
    const closed=rows.filter(r=>r.taxa!=null&&!r.parcial);
    const trend=closed.length?closed:rows.filter(r=>r.taxa!=null);
    const last=trend[trend.length-1];
    const prev=trend.length>=2?trend[trend.length-2]:null;
    const peak=trend.reduce((a,b)=>(!a||((b.taxa||0)>(a.taxa||0))?b:a),null);
    let h1="Pulso da operação<br>de Qualidade & CS";
    let p=`No recorte, <b>${fmtInt(agg.proc)}</b> procedentes em <b>${fmtInt(agg.aval)}</b> contestações (<b>${fmtPct(agg.taxa)}%</b>).`;
    let chip="● Leitura do recorte";
    let signalLabel=last?`Taxa em ${(last.short||"").toLowerCase()}${last.parcial?" · parcial":""}`:"Taxa acumulada";
    let signalVal=last&&last.taxa!=null?last.taxa:agg.taxa;
    let deltaCls="down", delta=`Acumulado ${fmtPct(agg.taxa)}%`;
    if(last&&peak&&last.taxa!=null&&peak.taxa!=null){
      const drop=Math.round(10*(peak.taxa-last.taxa))/10;
      const mom=prev&&prev.taxa!=null?Math.round(10*(last.taxa-prev.taxa))/10:null;
      if(mom!=null&&mom>=1&&last.taxa>DATA.metaProcedencia){
        h1="A taxa de falhas confirmadas subiu.<br>Hora de priorizar o motivo #1.";
        chip="● Atenção necessária"; deltaCls="up"; delta=`↑ ${fmtPct(mom)} p.p. vs ${(prev.short||"").toLowerCase()}`;
      }else if(drop>=1){
        h1="A taxa de falhas confirmadas caiu.<br>Agora é hora de sustentar.";
        chip="● Tendência favorável";
        const peakName=mesFull[(peak.short||"").toLowerCase()]||peak.short;
        delta=`↓ ${fmtPct(drop)} p.p. desde o pico de ${peakName}`;
      }
    }
    return {h1,p,chip,signalLabel,signalVal,deltaCls,delta};
  }
  function setChipActive(container, attr, value){
    container.querySelectorAll(".period-chip").forEach(btn=>{
      btn.classList.toggle("is-on", btn.dataset[attr]===String(value));
      if(attr==="month"){
        const m=monthsForYear(state.year).some(x=>x.month===Number(btn.dataset.month));
        btn.disabled=!m;
      }
    });
  }
  function render(){
    const rows=selectedRows();
    const agg=aggregate(rows);
    const hero=heroCopy(rows, agg);
    const motivos=mergeMotivos(rows);
    const top=motivos[0];
    const el=id=>document.getElementById(id);
    if(el("meta-period")) el("meta-period").textContent=periodLabel(rows);
    if(el("period-display")) el("period-display").textContent=periodLabel(rows);
    if(el("footer-period")) el("footer-period").textContent=periodLabel(rows);
    if(el("hero-h1")) el("hero-h1").innerHTML=hero.h1;
    if(el("hero-p")) el("hero-p").innerHTML=hero.p;
    if(el("hero-chips")) el("hero-chips").innerHTML=`<span class="chip">${hero.chip}</span>`;
    if(el("signal-label")) el("signal-label").textContent=hero.signalLabel;
    if(el("signal-value")) el("signal-value").textContent=`${fmtPct(hero.signalVal)}%`;
    const sigDelta=el("signal-delta");
    if(sigDelta){sigDelta.textContent=hero.delta; sigDelta.className=hero.deltaCls;}
    if(el("spark")) el("spark").innerHTML=sparkHtml(rows);
    if(el("kpi-taxa")) el("kpi-taxa").textContent=`${fmtPct(agg.taxa)}%`;
    if(el("kpi-taxa-sub")) el("kpi-taxa-sub").textContent=`${fmtInt(agg.proc)} de ${fmtInt(agg.aval)} recebidas`;
    if(el("kpi-proc")) el("kpi-proc").textContent=`${fmtInt(agg.proc)} / ${fmtInt(agg.aval)}`;
    if(el("kpi-proc-sub")) el("kpi-proc-sub").textContent=`${fmtInt(agg.improc)} não confirmaram falha`;
    if(el("kpi-motivo")) el("kpi-motivo").textContent=top?`${fmtPct(top.pct)}%`:"—";
    if(el("kpi-motivo-sub")) el("kpi-motivo-sub").textContent=top?`${fmtInt(top.n)} das falhas confirmadas`:"—";
    if(el("kpi-train")) el("kpi-train").textContent=agg.horas?`${String(Math.round(agg.horas*10)/10).replace(".",",")} h`:"0 h";
    if(el("kpi-train-sub")) el("kpi-train-sub").textContent="horas por pessoa no recorte";
    if(el("proc-summary")) el("proc-summary").textContent=`${fmtInt(agg.aval)} contestações recebidas: ${fmtInt(agg.proc)} confirmaram falha e ${fmtInt(agg.improc)} não confirmaram.`;
    const donut=el("donut");
    if(donut) donut.style.setProperty("--proc", `${agg.taxa||0}%`);
    if(el("donut-value")) el("donut-value").textContent=`${fmtPct(agg.taxa)}%`;
    if(el("legend-proc")) el("legend-proc").innerHTML=`<i class="a"></i><b>${fmtInt(agg.proc)}</b> Falhas confirmadas`;
    if(el("legend-improc")) el("legend-improc").innerHTML=`<i class="b"></i><b>${fmtInt(agg.improc)}</b> Sem falha confirmada`;
    if(el("bars-procedencia")) el("bars-procedencia").innerHTML=barsHtml(rows);
    const combo=el("combo-chart");
    if(combo){ combo.innerHTML=comboHtml(rows); combo.style.gridTemplateColumns=`repeat(${Math.max(rows.length,1)},1fr)`; }
    if(el("pareto-rows")) el("pareto-rows").innerHTML=paretoHtml(motivos);
    if(el("causas-head")) el("causas-head").textContent=top?`${fmtInt(top.n)} de ${fmtInt(agg.proc)} falhas confirmadas (${fmtPct(top.pct)}%) correspondem ao principal motivo` :"Motivos das falhas confirmadas";
    if(el("causas-proc-n")) el("causas-proc-n").textContent=`${fmtInt(agg.proc)} falhas confirmadas`;
    setChipActive(document.getElementById("preset-chips"),"preset",state.preset);
    document.querySelectorAll("#month-chips .period-chip").forEach(btn=>{
      const m=Number(btn.dataset.month);
      const has=monthsForYear(state.year).some(x=>x.month===m);
      btn.disabled=!has;
      btn.classList.toggle("is-on", state.preset==="month"&&state.month===m);
    });
    document.querySelectorAll("#year-chips .period-chip").forEach(btn=>{
      btn.classList.toggle("is-on", Number(btn.dataset.year)===state.year);
    });
  }
  function bind(){
    const yearRow=document.getElementById("year-row");
    const yearChips=document.getElementById("year-chips");
    if(yearChips){
      yearChips.innerHTML=DATA.years.map(y=>`<button type="button" class="period-chip" data-year="${y}">${y}</button>`).join("");
      yearRow.style.display=DATA.years.length>1?"flex":"none";
      yearChips.addEventListener("click",e=>{
        const btn=e.target.closest("[data-year]");
        if(!btn) return;
        state.year=Number(btn.dataset.year);
        if(state.preset==="month"&&state.month!=null){
          const ok=monthsForYear(state.year).some(m=>m.month===state.month);
          if(!ok) state.preset="ytd";
        }
        render();
      });
    }
    document.getElementById("preset-chips").addEventListener("click",e=>{
      const btn=e.target.closest("[data-preset]");
      if(!btn) return;
      state.preset=btn.dataset.preset;
      state.month=null;
      render();
    });
    document.getElementById("month-chips").addEventListener("click",e=>{
      const btn=e.target.closest("[data-month]");
      if(!btn||btn.disabled) return;
      state.preset="month";
      state.month=Number(btn.dataset.month);
      render();
    });
    render();
  }
  bind();
})();
"""


def _build_finding_focus_map(fg: pd.DataFrame) -> dict[str, dict]:
    if fg.empty:
        return {}
    work = fg.copy()
    if "duplicado_contestacao" in work.columns:
        work = work[~work["duplicado_contestacao"].fillna(False)]
    type_col = _find_fg_col(work, "tipo", "falha")
    scenario_col = next(
        (col for col in ("Novo cenário", "Cenário", "descricao_padrao") if col in work.columns),
        None,
    )
    etapa_col = next(
        (col for col in ("Etapa", "Etapa da Análise", "Etapa Análise") if col in work.columns),
        None,
    )
    if not type_col:
        return {}
    work["_type"] = work[type_col].map(_normalize_tipo_falha_fg)
    if etapa_col:
        work["Etapa"] = work[etapa_col].fillna("").astype(str).str.strip()
    focus: dict[str, dict] = {}
    for label in ("Automático", "Mapeamento", "Manual", "Processual"):
        subset = work[work["_type"].eq(label)]
        if subset.empty:
            continue
        scenarios = (
            subset[scenario_col].fillna("").astype(str).str.strip()
            if scenario_col
            else pd.Series(dtype=str)
        )
        # O detalhamento usa o mesmo rótulo canônico do ranking principal,
        # evitando misturar maiúsculas/minúsculas e abreviações do master.
        scenarios = scenarios.map(padronizar_descricao)
        visible = scenarios[scenarios.ne("") & ~scenarios.map(is_sensitive_scenario)]
        scenario_counts = visible.value_counts()
        visible_rows = [
            {"label": str(scenario), "count": int(count), "pct": round(100 * count / len(subset), 1)}
            for scenario, count in scenario_counts.head(3).items()
        ]
        focus[label] = {
            "type": label,
            "total": len(subset),
            "opportunity": FINDING_TYPE_GUIDANCE.get(label, "Validar classificação."),
            "scenarios": visible_rows,
            "cs_validation": cs_validation_items(label, subset, scenario_counts),
        }
    return focus


def _pulse_css() -> str:
    """CSS do quality_pulse_BRB_final (canônico de design)."""
    return """
    :root{--ink:#12213f;--muted:#65718a;--line:#e4e9f1;--bg:#f3f6fa;--navy:#102b5b;--blue:#265ee8;--green:#168565;--coral:#f1665b;--amber:#d88922}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 Arial,sans-serif}button{font:inherit}
    header{height:70px;display:flex;align-items:center;justify-content:space-between;padding:0 max(22px,calc((100vw - 1180px)/2));background:#fff;border-bottom:1px solid var(--line)}
    .brand{display:flex;align-items:center;gap:14px}.brand-logo{height:52px;width:auto;display:block;background:transparent;border:0;outline:0;box-shadow:none}
    .brand-sep{width:1px;height:36px;background:var(--line);flex-shrink:0}
    .brand div{display:flex;flex-direction:column}.brand small,.meta{color:var(--muted);font-size:11px}.meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.tag{padding:5px 8px;border-radius:6px;background:#edf1f6;font-weight:700}.client-badge{display:inline-flex;align-items:center;gap:5px;padding:5px 9px;border:1px solid #dbe6f8;border-radius:7px;background:#f5f8ff;color:#6b82a8;font-weight:700}.client-badge b{color:#49658f}
    .btn-export{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;background:var(--navy);color:#fff!important;text-decoration:none;font-size:11px;font-weight:800;border:0;white-space:nowrap}
    .btn-export:hover{background:#1a3f7a}
    nav{position:sticky;top:0;z-index:5;background:#ffffffee;border-bottom:1px solid var(--line);backdrop-filter:blur(10px);display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 max(22px,calc((100vw - 1180px)/2))}.tabs{max-width:1180px;margin:auto;display:flex;overflow:auto;flex:1}
    nav.analytic-expanded{padding-inline:max(16px,calc((100vw - 1420px)/2));gap:8px}.analytic-expanded .tabs{max-width:none;overflow:visible;flex-wrap:wrap;align-items:stretch}.analytic-expanded .tabs button{flex:1 1 auto;min-width:0;padding-inline:10px}.analytic-expanded .btn-analytic-toggle{margin-left:4px}
    nav button{min-height:48px;padding:15px 14px;border:0;border-bottom:3px solid transparent;background:transparent;color:var(--muted);font-size:13px;font-weight:700;white-space:nowrap;cursor:pointer}
    nav button.active{color:var(--blue);border-color:var(--blue)}.nav-tab-analytic.hidden{display:none}
    .btn-analytic-toggle{flex-shrink:0;margin:8px 0;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--muted);font-size:11px;font-weight:800;cursor:pointer;white-space:nowrap}
    .btn-analytic-toggle[aria-pressed="true"]{background:var(--navy);color:#fff;border-color:var(--navy)}
    main{max-width:1180px;min-height:calc(100vh - 160px);margin:auto;padding:26px 20px 45px}.panel-page{display:none}.panel-page.active{display:block;animation:in .2s ease}@keyframes in{from{opacity:.3;transform:translateY(4px)}}
    .hero{display:grid;grid-template-columns:1.6fr .7fr;min-height:310px;border-radius:20px;overflow:hidden;color:#fff;background:linear-gradient(125deg,#10264e,#123a79 68%,#17519e);box-shadow:0 17px 40px #15356424}
    .hero-copy{padding:44px}.eyebrow{display:block;margin-bottom:8px;color:var(--blue);font-size:10px;font-weight:900;letter-spacing:.13em;text-transform:uppercase}.hero .eyebrow{color:#9dbdfd}
    h1{margin:0 0 16px;font-size:50px;line-height:1.02;letter-spacing:-.045em}.hero p{max-width:650px;color:#d6e2f6;font-size:16px}.chips{display:flex;gap:9px;margin-top:23px}.chip{padding:7px 9px;border:1px solid #ffffff21;border-radius:7px;background:#ffffff10;font-size:11px;font-weight:700}
    .signal{margin:28px 28px 28px 0;padding:25px;border:1px solid #ffffff25;border-radius:16px;background:#ffffff0f}.signal strong{display:block;margin:22px 0 4px;font-size:54px}.down{color:#62dbb5;font-weight:800}.signal .up{color:#f5a39c;font-weight:800}.spark{height:80px;display:flex;align-items:end;gap:7px;margin-top:25px;border-bottom:1px solid #ffffff24}.spark i{flex:1;border-radius:4px 4px 0 0;background:#5788ed}.spark i:last-child{background:#48d1a5}
    .grid4,.grid3{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-top:17px}.grid3{grid-template-columns:repeat(3,1fr)}
    .card{padding:20px;background:#fff;border:1px solid var(--line);border-radius:14px}.card.accent-blue{border-top:3px solid var(--blue)}.card.accent-amber{border-top:3px solid var(--amber)}.card.accent-green{border-top:3px solid var(--green)}.card .label{color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.07em;text-transform:uppercase}.card strong{display:block;margin-top:7px;color:var(--navy);font-size:29px}.card small{color:var(--muted);font-size:12px}
    .section-head{display:grid;grid-template-columns:1fr 1fr;align-items:end;gap:35px;margin:4px 0 23px}.section-head h2{margin:0;font-size:35px;line-height:1.08;letter-spacing:-.04em}.section-head p{margin:0;color:var(--muted)}
    .layout{display:grid;grid-template-columns:1.5fr .75fr;gap:17px}.box{padding:24px;background:#fff;border:1px solid var(--line);border-radius:16px}.box h3{margin:0 0 18px;font-size:20px}
    .scope{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;align-items:center;gap:12px;margin-top:17px;padding:15px 18px;border:1px solid #d6e0f1;border-radius:12px;background:#eaf0fb}.scope div{display:flex;flex-direction:column}.scope span{color:#93a1b8;font-size:10px;font-weight:900}.scope b{font-size:12px}.scope small{color:var(--muted);font-size:10px}.scope i{color:#8190a7;font-style:normal}
    .exec-timeline{margin-top:17px;padding:18px 20px 16px;border:1px solid #d6e0f1;border-radius:14px;background:#fff}.exec-timeline-head{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:13px}.exec-timeline-head b{color:var(--navy);font-size:13px}.exec-timeline-head small{color:var(--muted);font-size:10px}.exec-events{position:relative;display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.exec-events:before{content:"";position:absolute;left:8%;right:8%;top:31px;height:2px;background:#dfe6f1}.exec-event{position:relative;z-index:1;min-width:0}.exec-event>span{display:block;height:19px;color:var(--muted);font-size:9px;font-weight:900;text-transform:uppercase}.exec-event>i{display:block;width:13px;height:13px;margin:6px 0 10px;border:3px solid #fff;border-radius:50%;background:var(--blue);box-shadow:0 0 0 2px var(--blue)}.exec-event.training>i,.exec-event.result>i{background:var(--green);box-shadow:0 0 0 2px var(--green)}.exec-event.attention>i{background:var(--amber);box-shadow:0 0 0 2px var(--amber)}.exec-event.risk>i{background:#d78618;box-shadow:0 0 0 2px #d78618}.exec-event.next-step>i{background:#6d4bc3;box-shadow:0 0 0 2px #6d4bc3}.exec-event.next-step>span{color:#6d4bc3}.exec-event.next-step>strong{color:#5b3ba9}.exec-event b,.exec-event strong,.exec-event small{display:block}.exec-event b{font-size:11px}.exec-event strong{margin-top:3px;color:var(--navy);font-size:19px}.exec-event small{color:var(--muted);font-size:9px;line-height:1.3}.exec-timeline-note{margin:12px 0 0;padding-top:10px;border-top:1px solid #edf0f4;color:var(--muted);font-size:10px}
    .insights{display:grid}.insight{padding:14px 0;border-top:1px solid #edf0f4}.insight:first-child{border:0}.insight b{display:block}.insight p{margin:4px 0 0;color:var(--muted);font-size:12px}
    .decision{color:#fff;background:var(--navy);border:0}.decision .eyebrow{color:#91b5fb}.decision p{color:#bdcce5}.goal{padding:16px 0;border-top:1px solid #ffffff20}.goal strong{font-size:34px;color:#62dbb5}
    .donut-wrap{position:relative;display:flex;align-items:center;justify-content:center;gap:35px;padding-top:50px}.definition{position:absolute;top:16px;left:20px;padding:6px 8px;border-radius:6px;color:var(--blue);background:#edf2ff;font-size:11px;font-weight:800}.donut{width:180px;aspect-ratio:1;display:grid;place-items:center;border-radius:50%;background:conic-gradient(var(--coral) var(--proc),#183a72 0)}.donut:before{content:"";width:132px;aspect-ratio:1;grid-area:1/1;border-radius:50%;background:#fff}.donut div{position:relative;grid-area:1/1;text-align:center}.donut strong{font-size:28px}.donut span{display:block;color:var(--muted);font-size:10px}
    .legend div{margin:15px 0}.legend i{display:inline-block;width:9px;height:9px;margin-right:7px;border-radius:3px}.legend .a{background:var(--coral)}.legend .b{background:#183a72}
    .bars{height:220px;display:flex;align-items:end;gap:18px;padding:25px 12px 20px;border-bottom:1px solid var(--line);background:repeating-linear-gradient(#fff 0,#fff 54px,#eef1f5 55px)}
    .bar{height:100%;flex:1;display:flex;flex-direction:column;align-items:center;justify-content:end}.bar b{font-size:10px}.bar i{width:30px;max-width:75%;min-height:8px;margin:5px 0;border-radius:5px 5px 0 0;background:linear-gradient(#4c7ff0,#2054c8)}.bar:last-child i{background:var(--green)}.bar small{margin-bottom:-18px}
    .pareto{display:grid}.row{display:grid;grid-template-columns:30px 1fr 55px;align-items:center;gap:12px;padding:14px 0;border-top:1px solid #edf0f4}.row:first-child{border:0}.rank{color:#a8b2c2;font-size:10px}.track{height:7px;margin-top:7px;background:#eef1f5;border-radius:5px;overflow:hidden}.track i{display:block;height:100%;background:var(--blue)}.value{text-align:right}.value b{display:block;font-size:17px}.value small{color:var(--muted)}
    .recurrence-panel{margin-top:16px;padding:22px 24px;background:#fff;border:1px solid var(--line);border-radius:16px}.recurrence-head{display:flex;align-items:end;justify-content:space-between;gap:18px}.recurrence-head h3{margin:0;color:var(--navy);font-size:20px}.recurrence-head p{margin:4px 0 0;color:var(--muted);font-size:11px}.recurrence-controls{display:grid;gap:8px;min-width:340px}.recurrence-source{display:grid;grid-template-columns:1fr 1fr;padding:3px;border:1px solid #d8e1ed;border-radius:9px;background:#f5f7fb}.recurrence-source button{padding:7px 9px;border:0;border-radius:6px;background:transparent;color:var(--muted);font-size:10px;font-weight:800;cursor:pointer}.recurrence-source button.active{background:#fff;color:var(--blue);box-shadow:0 1px 4px #183a721c}.recurrence-picker{display:flex;flex-direction:column;gap:4px}.recurrence-picker label{color:var(--muted);font-size:9px;font-weight:900;text-transform:uppercase}.recurrence-picker select{width:100%;padding:9px 11px;border:1px solid #d8e1ed;border-radius:8px;background:#fff;color:var(--ink);font:700 11px Arial,sans-serif}.recurrence-summary{display:grid;grid-template-columns:repeat(3,minmax(0,auto));justify-content:start;gap:8px;margin-top:16px}.recurrence-summary span{padding:7px 10px;border-left:3px solid var(--blue);border-radius:6px;background:#f4f7fd;color:var(--navy);font-size:10px;font-weight:800}.recurrence-summary span:nth-child(2){border-color:var(--amber)}.recurrence-summary span:nth-child(3){border-color:var(--green)}.recurrence-scroll{overflow-x:auto;padding:5px 2px 2px}.recurrence-track{--rec-cols:4;position:relative;display:grid;grid-template-columns:repeat(var(--rec-cols),minmax(145px,1fr));gap:14px;min-width:max(100%,calc(var(--rec-cols) * 145px));margin-top:13px}.recurrence-track:before{content:"";position:absolute;left:5%;right:5%;top:31px;height:2px;background:#dfe6f1}.recurrence-event{position:relative;z-index:1}.recurrence-event time{display:block;height:18px;color:var(--muted);font-size:9px;font-weight:900;text-transform:uppercase}.recurrence-event i{display:block;width:13px;height:13px;margin:7px 0 11px;border:3px solid #fff;border-radius:50%;background:var(--blue);box-shadow:0 0 0 2px var(--blue)}.recurrence-event.repeated i{background:var(--amber);box-shadow:0 0 0 2px var(--amber)}.recurrence-event.latest i{background:var(--green);box-shadow:0 0 0 2px var(--green)}.recurrence-event b,.recurrence-event strong,.recurrence-event small{display:block}.recurrence-event b{font-size:10px}.recurrence-event strong{margin-top:3px;color:var(--navy);font-size:18px}.recurrence-event small{color:var(--muted);font-size:9px;line-height:1.35}.recurrence-note{margin:13px 0 0;padding-top:11px;border-top:1px solid #edf0f4;color:var(--muted);font-size:10px}
    .finding-context{margin-top:16px;padding:24px;background:#fff;border:1px solid var(--line);border-radius:16px}.finding-context-head{display:grid;grid-template-columns:1.2fr .8fr;gap:24px;align-items:end}.finding-context-head h3{margin:0;color:var(--navy);font-size:20px}.finding-context-head p{margin:0;color:var(--muted);font-size:11px;line-height:1.45}.finding-composition{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin-top:16px}.finding-type{padding:13px;border-top:3px solid var(--type-color);border-radius:10px;background:#f8fafc;cursor:pointer;border:1px solid transparent}.finding-type.is-active{outline:2px solid #265ee855;background:#fff;border-color:#d8e1ed}.finding-type strong,.finding-type b,.finding-type small{display:block}.finding-type strong{color:var(--navy);font-size:21px}.finding-type b{margin-top:4px;font-size:10px}.finding-type small{margin-top:3px;color:var(--muted);font-size:9px;line-height:1.3}.finding-type-detail{margin-top:14px;padding:15px 17px;border:1px solid var(--line);border-radius:12px;background:#f8fafc}.finding-type-detail[hidden]{display:none}.finding-type-detail h4{margin:0 0 8px;color:#168565;font-size:10px;text-transform:uppercase}.finding-type-detail ul{margin:0;padding-left:18px;color:#43546d;font-size:10px;line-height:1.45}.finding-type-detail li+li{margin-top:4px}.finding-type-detail .focus-head{display:flex;justify-content:space-between;gap:12px;margin-bottom:10px}.finding-type-detail .focus-head b{color:var(--navy);font-size:11px}.finding-type-detail .focus-head span{color:var(--muted);font-size:10px}.type-scenario{display:grid;grid-template-columns:1fr 26px 1fr;align-items:center;gap:12px;margin-top:14px;padding:14px 16px;border-radius:10px;background:#f4f7fd}.type-scenario article b,.type-scenario article span{display:block}.type-scenario article b{color:var(--navy);font-size:11px}.type-scenario article span{margin-top:4px;color:var(--muted);font-size:10px;line-height:1.4}.type-scenario i{font-style:normal;color:var(--blue);font-size:18px;text-align:center}.mapping-focus{display:grid;grid-template-columns:.7fr 1.3fr;gap:16px;margin-top:14px;padding:15px 17px;border-left:4px solid #7457c8;border-radius:9px;background:#f6f3ff}.mapping-focus strong,.mapping-focus b,.mapping-focus small{display:block}.mapping-focus strong{color:#49358a;font-size:24px}.mapping-focus small{color:#756b91;font-size:9px}.mapping-focus b{color:var(--navy);font-size:11px}.mapping-focus p{margin:5px 0 0;color:#625a78;font-size:10px;line-height:1.4}.automatic-opportunity{margin-top:16px;padding:24px;background:#fff;border:1px solid var(--line);border-radius:16px}.automatic-op-head{display:grid;grid-template-columns:1fr 1fr;align-items:end;gap:24px;margin-bottom:16px}.automatic-op-head h3{margin:0;color:var(--navy);font-size:20px}.automatic-op-head p{margin:0;color:var(--muted);font-size:11px}.auto-lanes{display:grid;grid-template-columns:1.35fr .85fr;gap:14px}.auto-lane{overflow:hidden;border:1px solid var(--line);border-radius:13px;background:#fbfcfe}.auto-lane>header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 17px;background:#edf3ff;border-bottom:1px solid #dce6f5}.auto-lane.contest>header{background:#fff7e9;border-bottom-color:#f0ddba}.auto-lane header div{display:grid;grid-template-columns:auto auto;align-items:center;column-gap:10px}.auto-lane header span{grid-column:1/-1;color:var(--muted);font-size:9px;font-weight:900;text-transform:uppercase}.auto-lane header strong{color:var(--navy);font-size:28px;line-height:1}.auto-lane header small{max-width:130px;color:var(--muted);font-size:9px;line-height:1.25}.auto-lane header>b{color:var(--blue);font-size:9px;text-transform:uppercase}.auto-lane.contest header>b{color:#a76812}.auto-op-row{display:grid;grid-template-columns:.85fr 1.15fr;gap:14px;padding:13px 16px;border-top:1px solid #edf0f4}.auto-op-row:first-of-type{border-top:0}.auto-op-row b,.auto-op-row small{display:block}.auto-op-row b{color:var(--navy);font-size:11px}.auto-op-row small{margin-top:3px;color:var(--muted);font-size:9px}.auto-op-row p{margin:0;color:#53627a;font-size:10px;line-height:1.4}.auto-empty{padding:16px;color:var(--muted);font-size:11px}.auto-decision{display:grid;grid-template-columns:180px 1fr;gap:16px;margin-top:14px;padding:14px 16px;border-left:4px solid var(--green);border-radius:8px;background:#edf8f4}.auto-decision b{color:var(--green);font-size:10px;text-transform:uppercase}.auto-decision span{color:#43546d;font-size:11px}
    .dark{color:#fff;background:var(--navy);border:0}.dark .eyebrow{color:#8db1f7}.big{display:flex;align-items:center;gap:15px;padding:20px 0;border-top:1px solid #ffffff20;border-bottom:1px solid #ffffff20}.big strong{font-size:40px}.dark ul{padding-left:18px;color:#c8d5e8}
    table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:visible}th,td{padding:12px 14px;text-align:left;vertical-align:middle;border-bottom:1px solid #edf0f4}th{color:var(--muted);background:#f8fafc;font-size:11px;text-transform:uppercase}th:first-child{border-radius:14px 0 0 0}th:last-child{border-radius:0 14px 0 0}th.num,td.num{text-align:right;font-variant-numeric:tabular-nums}.partial{padding:3px 5px;border-radius:4px;background:#fff2dc;color:#9e661a;font-size:10px;font-weight:800}.partial-row td{background:#fffbf3}.partial-row td:first-child{box-shadow:inset 4px 0 0 #efb34f}.volume-row td{background:#f7faff}.volume-row td:first-child{box-shadow:inset 4px 0 0 #6e92e8}.attention-cell{color:#a76812;font-weight:800}
    th.tip{position:relative;cursor:help;border-bottom:1px dotted #9aa4b4}th.tip:hover{color:var(--navy)}th.tip:hover:after{content:attr(data-tip);position:absolute;left:50%;bottom:calc(100% + 8px);transform:translateX(-50%);z-index:20;width:max-content;max-width:220px;padding:8px 10px;border-radius:8px;background:#12213f;color:#fff;font-size:11px;font-weight:600;line-height:1.35;text-transform:none;letter-spacing:0;white-space:normal;box-shadow:0 8px 20px #12213f33;pointer-events:none}th.tip:hover:before{content:"";position:absolute;left:50%;bottom:calc(100% + 2px);transform:translateX(-50%);border:6px solid transparent;border-top-color:#12213f;z-index:21;pointer-events:none}th.tip.num:hover:after{left:auto;right:0;transform:none}
    .table-insights{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:0 0 14px}.table-insights article{display:flex;align-items:center;gap:12px;padding:13px 15px;border:1px solid var(--line);border-radius:11px;background:#fff}.table-insights article.good{border-left:4px solid var(--green)}.table-insights article.warn{border-left:4px solid var(--amber)}.table-insights article.training{border-left:4px solid #1c9d90}.table-insights strong{color:var(--navy);font-size:18px;white-space:nowrap}.table-insights span{color:var(--muted);font-size:10px;line-height:1.35}.rate-cell{position:relative;overflow:hidden}.rate-cell:before{content:"";position:absolute;inset:6px auto 6px 6px;width:var(--bar);border-radius:5px;background:#e8efff}.rate-cell span{position:relative;z-index:1;font-weight:800}.delta-pill,.volume-pill,.hours-pill{display:inline-block;padding:4px 7px;border-radius:6px;font-size:11px;font-weight:800}.delta-pill.down{color:#127659;background:#e8f6f1}.delta-pill.up{color:#ae4741;background:#fdeceb}.delta-pill.base{color:#718096;background:#eef2f6}.volume-pill{color:#9b6414;background:#fff1d8}.hours-pill{color:#107668;background:#e6f6f3}.muted-zero{color:#9aa4b4}.evolution-table tbody tr:hover td{background-color:#f8faff}
    .note{display:flex;gap:12px;margin-top:15px;padding:15px;border:1px solid #f0ddba;border-radius:10px;background:#fff7e9;color:#765d35}.note b{display:block}.note p{margin:3px 0 0;font-size:12px}
    .info-details{margin:0 0 16px;border:1px solid #d6e0f1;border-radius:11px;background:#f7faff;color:var(--muted)}.info-details summary{display:flex;align-items:center;gap:8px;padding:12px 15px;color:var(--blue);font-size:12px;font-weight:800;cursor:pointer;list-style:none}.info-details summary::-webkit-details-marker{display:none}.info-details summary:before{content:'+';display:grid;place-items:center;width:18px;height:18px;border-radius:50%;background:#e7efff;color:var(--blue);font-size:15px;line-height:1}.info-details[open] summary:before{content:'−'}.info-details .info-body{padding:0 15px 14px;font-size:12px;line-height:1.5}.info-details .info-body p{margin:0}.info-details .info-body b{color:var(--ink)}
    .age-list{display:grid;gap:13px;margin-top:16px}.age-item{display:grid;grid-template-columns:125px 1fr 100px;gap:12px;align-items:center}.age-item>span{color:var(--muted);font-size:12px}.age-track{height:10px;border-radius:8px;background:#edf1f6;overflow:hidden}.age-track i{display:block;height:100%;min-width:3px;border-radius:8px;background:linear-gradient(90deg,#4c7ff0,#265ee8)}.age-item b{text-align:right;font-size:12px}.history-kpis{margin-bottom:16px}.history-table td:first-child{font-weight:800}.old-table{margin-top:12px}.old-table td{font-size:12px}.history-period{color:var(--blue);font-weight:800}
    .analysis-bridge{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:12px;align-items:stretch;margin:0 0 16px}.bridge-card{padding:18px 20px;border:1px solid var(--line);border-radius:14px;background:#fff}.bridge-card.current{border-top:4px solid var(--blue)}.bridge-card.previous{border-top:4px solid var(--amber)}.bridge-card.total{border-top:4px solid var(--navy);background:#f7f9fd}.bridge-card span,.bridge-card small{display:block}.bridge-card span{color:var(--muted);font-size:11px;font-weight:800}.bridge-card strong{display:block;margin:5px 0;color:var(--navy);font-size:32px}.bridge-card small{color:var(--muted);font-size:11px}.bridge-op{display:grid;place-items:center;color:#8a96a9;font-size:26px;font-weight:900}.trend-rate{display:inline-block;min-width:58px;padding:4px 7px;border-radius:6px;text-align:center;font-weight:900}.trend-rate.down{color:#127659;background:#e8f6f1}.trend-rate.up{color:#ae4741;background:#fdeceb}.trend-rate.base{color:#59677e;background:#eef2f6}
    .temporal-universe{margin-bottom:16px}.temporal-total{display:flex;align-items:end;gap:13px;margin:4px 0 14px}.temporal-total strong{color:var(--navy);font-size:42px;line-height:1}.temporal-total span{max-width:420px;color:var(--muted);font-size:13px}.scope-split{display:flex;height:12px;margin-bottom:14px;border-radius:8px;overflow:hidden;background:#edf1f6}.scope-split i:first-child{background:var(--blue)}.scope-split i:last-child{background:var(--amber)}.scope-breakdown{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.scope-breakdown article{padding:15px;border:1px solid var(--line);border-radius:10px;background:#f9fbfd}.scope-breakdown article.current{border-left:4px solid var(--blue)}.scope-breakdown article.previous{border-left:4px solid var(--amber)}.scope-breakdown article.protocols{border-left:4px solid var(--navy)}.scope-breakdown strong,.scope-breakdown span,.scope-breakdown small{display:block}.scope-breakdown strong{font-size:26px;color:var(--navy)}.scope-breakdown span{font-size:12px;font-weight:800}.scope-breakdown small{margin-top:4px;color:var(--muted);font-size:10px}
    .actions{display:grid;gap:9px}.action{display:grid;grid-template-columns:55px 1.5fr .65fr 1fr 85px;gap:18px;align-items:center;padding:15px 17px;background:#fff;border:1px solid var(--line);border-radius:11px}.prio{padding:5px;text-align:center;border-radius:5px;color:#ac4640;background:#feebea;font-size:10px;font-weight:900}.action span:not(.prio){color:var(--muted);font-size:11px}.status{justify-self:end;padding:6px 8px!important;border-radius:5px;background:#edf2ff;color:var(--blue)!important;font-weight:800}
    .business{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;padding:0;background:var(--line);overflow:hidden}.business article{display:grid;grid-template-columns:auto 1fr;gap:12px;padding:19px;background:#fff}.business strong{color:var(--navy);font-size:25px;line-height:1}.business b{font-size:12px}.business p{margin:5px 0 0;color:var(--muted);font-size:11px}.rule{display:flex;align-items:center;gap:10px;margin-top:14px;padding:13px 16px;border-radius:9px;background:#eef2f7;color:#5d6b82;font-size:12px}
    .client-impact{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:1px;margin:16px 0;background:var(--line);border:1px solid var(--line);border-radius:14px;overflow:hidden}.client-impact article{padding:18px 20px;background:#fff}.client-impact article:first-child{background:#f5f8ff;border-top:4px solid var(--blue)}.client-impact small,.client-impact b,.client-impact strong,.client-impact p{display:block}.client-impact small{color:var(--muted);font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.04em}.client-impact strong{margin:7px 0;color:var(--navy);font-size:24px;line-height:1.08}.client-impact b{color:var(--navy);font-size:12px}.client-impact p{margin:6px 0 0;color:var(--muted);font-size:11px;line-height:1.42}.plan-context{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0 0 15px;padding:13px 16px;border-left:4px solid var(--green);border-radius:9px;background:#edf8f4;color:#40556e;font-size:11px}.plan-context strong{color:var(--green);font-size:12px}.plan-context span{color:var(--muted)}
    .fg{display:grid;grid-template-columns:1.2fr repeat(3,.7fr);gap:16px;align-items:center;margin-top:16px}.fg>div:not(:first-child){padding-left:16px;border-left:1px solid var(--line)}.fg strong,.fg small{display:block}.fg strong{font-size:21px;color:var(--navy)}.fg small{color:var(--muted);font-size:11px}.fg p{grid-column:1/-1;margin:0;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}
    .cap-align{display:grid;grid-template-columns:1.1fr 1.4fr;gap:14px;margin-top:16px}.cap-align article{padding:18px 20px;background:#fff;border:1px solid var(--line);border-radius:14px}.cap-align article.good{border-left:4px solid var(--green)}.cap-align article.warn{border-left:4px solid var(--amber)}.cap-align .chip-inline{display:inline-block;margin-top:8px;padding:4px 8px;border-radius:6px;font-size:10px;font-weight:800}.cap-align .chip-inline.good{color:#127659;background:#e8f6f1}.cap-align .chip-inline.warn{color:#9e661a;background:#fff2dc}.cap-align h3{margin:6px 0 8px;font-size:18px;color:var(--navy)}.cap-align p{margin:0;color:var(--muted);font-size:12px;line-height:1.45}.cap-align ul{margin:10px 0 0;padding-left:18px;color:var(--muted);font-size:12px}
    .cap-duas{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}.cap-duas article{padding:20px 22px;background:#fff;border:1px solid var(--line);border-radius:14px}.cap-duas article.sala{border-top:3px solid #168565}.cap-duas article.alcance{border-top:3px solid #265ee8}.cap-duas .tag-leitura{display:inline-block;padding:3px 7px;border-radius:5px;font-size:10px;font-weight:800;letter-spacing:.04em;text-transform:uppercase}.cap-duas .sala .tag-leitura{color:#127659;background:#e8f6f1}.cap-duas .alcance .tag-leitura{color:#265ee8;background:#edf2ff}.cap-duas h3{margin:10px 0 4px;font-size:32px;color:var(--navy);line-height:1}.cap-duas .pergunta{margin:0 0 10px;color:var(--muted);font-size:12px;font-weight:700}.cap-duas p{margin:8px 0 0;color:var(--muted);font-size:12px;line-height:1.45}.cap-duas .formula{margin-top:10px;padding-top:10px;border-top:1px solid #edf0f4;color:#7a8699;font-size:11px}
    .combo{margin-bottom:16px}.combo-head{display:flex;justify-content:space-between;gap:15px;align-items:flex-start}.combo-legend{display:flex;gap:16px;color:var(--muted);font-size:11px}.combo-legend i{display:inline-block;width:10px;height:10px;margin-right:5px}.key-rate{border:3px solid var(--blue);border-radius:50%}.key-training{background:var(--green);border-radius:2px}.combo-chart{height:245px;display:grid;grid-template-columns:repeat(7,1fr);gap:10px;align-items:end;margin-top:14px;padding:0 10px 25px;border-bottom:1px solid var(--line);background:repeating-linear-gradient(#fff 0,#fff 55px,#eef1f5 56px)}.cm{position:relative;height:100%;display:flex;align-items:end;justify-content:center}.cm.partial-bg{background:repeating-linear-gradient(135deg,transparent,transparent 8px,#fff7e8 8px,#fff7e8 16px)}.cm>small{position:absolute;bottom:-22px;color:var(--muted);font-size:11px;font-weight:800}.delta{position:absolute;top:4px;padding:3px 5px;border-radius:4px;background:#eaf8f3;color:var(--green);font-size:9px;font-weight:800}.delta.up{background:#feebea;color:#b24d45}.point{position:absolute;left:50%;z-index:2;display:flex;flex-direction:column;align-items:center;transform:translateX(-50%)}.point b{padding:2px 4px;background:#fff;color:var(--blue);font-size:10px}.point i{width:12px;height:12px;border:3px solid var(--blue);border-radius:50%;background:#fff}.train{width:29px;min-height:2px;display:flex;justify-content:center;border-radius:5px 5px 0 0;background:linear-gradient(#39ae8c,#188263)}.train b{margin-top:5px;color:#fff;font-size:9px}.plan-metrics{padding:30px;display:grid;grid-template-columns:repeat(3,1fr);gap:20px;align-items:center}
    @media(max-width:800px){.client-impact{grid-template-columns:1fr}.plan-context{display:block}.plan-context span{display:block;margin-top:5px}}
    footer{max-width:1180px;margin:auto;padding:20px;color:#8a95a6;border-top:1px solid var(--line);font-size:10px}
    @media(max-width:800px){.meta span:not(.tag){display:none}.hero,.layout,.section-head,.cap-align,.cap-duas,.automatic-op-head,.auto-lanes,.finding-context-head,.mapping-focus{grid-template-columns:1fr}.finding-composition{grid-template-columns:repeat(2,1fr)}.type-scenario{grid-template-columns:1fr}.type-scenario i{transform:rotate(90deg)}.plan-hero{grid-template-columns:1fr!important}.signal{margin:0 18px 18px}.grid4,.grid3{grid-template-columns:repeat(2,1fr)}h1{font-size:37px}.section-head{gap:8px}.scope{grid-template-columns:1fr}.scope i{transform:rotate(90deg);justify-self:center}.exec-timeline-head,.recurrence-head{align-items:flex-start;flex-direction:column}.exec-events{grid-template-columns:1fr;gap:13px;padding-left:8px}.exec-events:before{left:13px;right:auto;top:10px;bottom:12px;width:2px;height:auto}.exec-event{display:grid;grid-template-columns:20px 1fr;column-gap:10px}.exec-event>span{grid-column:2}.exec-event>i{grid-column:1;grid-row:1/5;margin-top:7px}.exec-event>b,.exec-event>strong,.exec-event>small{grid-column:2}.recurrence-controls{min-width:0;width:100%}.recurrence-summary{grid-template-columns:1fr}.recurrence-scroll{overflow:visible}.recurrence-track{display:block;min-width:0;padding-left:8px}.recurrence-track:before{left:13px;right:auto;top:10px;bottom:12px;width:2px;height:auto}.recurrence-event{display:grid;grid-template-columns:20px 1fr;column-gap:10px;margin-bottom:13px}.recurrence-event time{grid-column:2}.recurrence-event i{grid-column:1;grid-row:1/5;margin-top:7px}.recurrence-event b,.recurrence-event strong,.recurrence-event small{grid-column:2}.auto-op-row,.auto-decision{grid-template-columns:1fr}.business,.table-insights,.scope-breakdown{grid-template-columns:1fr}.analysis-bridge{grid-template-columns:1fr}.bridge-op{height:20px}.fg{grid-template-columns:1fr 1fr}.fg>div:first-child{grid-column:1/-1}.fg>div:nth-child(even){padding-left:0;border-left:0}.action{grid-template-columns:55px 1fr auto}.action>div:nth-of-type(n+2){display:none}.action .status{display:block}.plan-metrics{grid-template-columns:1fr;padding:22px}.combo-chart{gap:3px;padding-inline:2px;grid-template-columns:repeat(auto-fit,minmax(48px,1fr))}.train{width:20px}.delta{font-size:8px;transform:rotate(-35deg)}table{display:block;overflow-x:auto;white-space:nowrap}.btn-export{padding:7px 9px}}
    """


def render_quality_pulse_html(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    analytics: BRBAnalytics | None = None,
    *,
    periodo_label: str = "",
    data_inicio=None,
    data_fim=None,
    generated_at: datetime | None = None,
    excel_export_href: str | None = None,
    rca_export_href: str | None = None,
    contestacao_historica: pd.DataFrame | None = None,
    modo_executivo: bool = True,
) -> str:
    generated_at = generated_at or datetime.now()
    client_short = config_brb.CLIENT.get("nome_curto", "Cliente")
    cs_target_rate = float(_cs_plan_context()["confirmed_rate"])
    period_h = _period_human(data_inicio, data_fim, periodo_label)
    temporal_source = contestacao_historica if contestacao_historica is not None else bundle.contestacao
    if data_fim is not None and not temporal_source.empty and "Data" in temporal_source.columns:
        temporal_dates = parse_excel_date(temporal_source["Data"])
        temporal_mask = temporal_dates <= pd.Timestamp(data_fim)
        analysis_col = next(
            (c for c in temporal_source.columns if "an" in str(c).lower() and "lis" in str(c).lower()),
            None,
        )
        if analysis_col:
            analysis_dates = parse_excel_date(temporal_source[analysis_col])
            temporal_mask &= analysis_dates <= pd.Timestamp(data_fim)
        temporal_source = temporal_source.loc[temporal_mask].copy()
    temporal_generated_at = (
        datetime.combine(pd.Timestamp(data_fim).date(), datetime.min.time())
        if data_fim is not None
        else generated_at
    )
    temporal = _contestacao_temporal(temporal_source, temporal_generated_at)

    proc_n = int(metrics.conforme_nao or 0)
    improc_n = int(metrics.conforme_sim or 0)
    total_aval = proc_n + improc_n
    taxa_acum = float(metrics.pct_procedente or 0.0)
    if not taxa_acum and total_aval:
        taxa_acum = round(100.0 * proc_n / total_aval, 1)

    series = _monthly_series(bundle, data_fim)
    closed_series = [r for r in series if not r.get("parcial")]
    sparks = _spark_heights(closed_series or series)
    hero = _hero_scenario(
        series,
        taxa_acum=taxa_acum,
        proc_n=proc_n,
        total_aval=total_aval,
        meta_procedencia=cs_target_rate,
    )
    last = hero["last"]
    peak = hero["peak"]
    last_taxa = hero["last_taxa"]
    peak_taxa = hero["peak_taxa"]
    peak_month_full = hero["peak_month_full"]
    last_short = hero["last_short"]
    last_partial = hero["last_partial"]
    partial_latest = hero["partial_latest"]
    drop_pp = hero["drop_pp"]
    hero_h1 = hero["hero_h1"]
    hero_p = hero["hero_p"]
    trend_chip = hero["trend_chip"]
    signal_label = hero["signal_label"]
    signal_cls = hero["signal_cls"]
    signal_delta = hero["signal_delta"]
    decision_h3 = hero["decision_h3"]
    decision_p = hero["decision_p"]
    evo_lead = hero["evo_lead"]

    na_falhas = int(metrics.na_falhas_registros or 0)
    na_prot = int(metrics.na_falhas_protocolos or 0)
    train_h = float(getattr(metrics, "treinamentos_horas", 0) or 0)
    train_sessoes = int(getattr(metrics, "treinamentos_sessoes", 0) or 0)
    fg_n = int(metrics.casos_unicos_fg or 0) or len(bundle.falhas_gerais)
    fg_prot = int(metrics.protocolos_distintos_fg or 0)
    aud_casos = int(metrics.auditados_casos or 0)
    aud_regs = int(metrics.auditados_registros or 0)
    demandas = int(metrics.demandas_na_registros or 0)
    proto_info = int(metrics.protocolos_na or 0)

    # Cruzamento FG × NA (protocolo único)
    _cruz = (analytics.cruzamentos if analytics and getattr(analytics, "cruzamentos", None) else {}) or {}
    fg_match_na = int(_cruz.get("fg_protocolos_com_match_na") or 0)
    fg_sem_na = int(_cruz.get("fg_protocolos_sem_match_na") or 0)
    pct_fg_na = float(_cruz.get("pct_protocolos_fg_com_na") or 0)
    if not (fg_match_na or fg_sem_na):
        try:
            from report_brb.brb_analytics import build_base_consolidada, compute_cruzamentos

            _base = build_base_consolidada(bundle)
            _cx = compute_cruzamentos(_base, bundle)
            fg_match_na = int(_cx.get("fg_com_match_na") or 0)
            fg_sem_na = int(_cx.get("fg_sem_match_na") or 0)
            pct_fg_na = float(_cx.get("pct_fg_com_na") or 0)
        except Exception:
            pass
    if fg_match_na or fg_sem_na:
        fg_match_note = (
            "As notificações válidas seguem a regra de mudança entre COM RISCO e SEM RISCO, nos dois sentidos. "
            f"Na revisão geral, {format_int_br(fg_match_na)} dos {format_int_br(fg_prot)} protocolos também aparecem "
            "na base de notificações. Os demais correspondem a cenários que não fazem parte da regra vigente. "
            "As duas bases têm objetivos diferentes, portanto essa comparação não mede a cobertura das notificações."
        )
    else:
        fg_match_note = (
            "Leitura complementar dos achados encontrados em auditoria "
            "(FG ≠ falhas notificadas ao CS)."
        )

    motivo_df = _group_procedentes(bundle.contestacao)
    proc_base = _procedentes_contestacao(bundle.contestacao)
    tipo_df, _ = _group_procedentes_by_tipo(proc_base)
    man_pct, _man_n, _tipo_total = _manual_share(tipo_df)
    top_motivo = str(motivo_df.iloc[0]["Motivo"]) if not motivo_df.empty else "—"
    top_motivo_pct = _parse_pct_str(motivo_df.iloc[0]["Pct"]) if not motivo_df.empty else 0.0
    qtd_col = "Quantidade" if "Quantidade" in motivo_df.columns else "Casos"
    top_motivo_n = int(motivo_df.iloc[0][qtd_col]) if not motivo_df.empty else 0
    impact_scope = (
        f"{format_int_br(top_motivo_n)} falhas confirmadas no período"
        if top_motivo_n
        else "base sem falhas confirmadas no período"
    )
    client_implication = (
        f"A concentração em {_short_motivo(top_motivo, 52).lower()} indica uma barreira de decisão "
        "que precisa ser reforçada antes da conclusão da análise."
        if top_motivo_n
        else "O período não trouxe volume suficiente para apontar uma causa dominante."
    )
    cs_trigger = (
        f"Revisar no próximo fechamento e acionar a governança após 2 ciclos acima de {_pct(cs_target_rate)}%."
    )
    contestacao_recurrence = _motivo_recurrence_data(
        proc_base,
        motivo_df,
        data_fim=data_fim,
    )
    automatic_recurrence = _automatic_recurrence_data(
        bundle.falhas_gerais,
        data_fim=data_fim,
    )
    automatic_contestation = _automatic_contestation_data(
        proc_base,
        data_fim=data_fim,
    )
    fg_tipo_df, _, _ = _fg_perfil_por_tipo(bundle.falhas_gerais)
    fg_tipo_dom = _fg_predominante(fg_tipo_df)
    finding_profile_df = pd.DataFrame(columns=["Tipo de falha", "Casos"])
    if not bundle.falhas_gerais.empty:
        finding_type_col = _find_fg_col(bundle.falhas_gerais, "tipo", "falha")
        if finding_type_col:
            finding_counts = bundle.falhas_gerais[finding_type_col].map(
                _normalize_tipo_falha_fg
            ).value_counts()
            finding_order = ["Automático", "Mapeamento", "Manual", "Processual", "Não informado"]
            finding_rows = [
                {"Tipo de falha": label, "Casos": int(finding_counts[label])}
                for label in finding_order
                if label in finding_counts.index
            ]
            finding_rows.extend(
                {"Tipo de falha": str(label), "Casos": int(cases)}
                for label, cases in finding_counts.items()
                if label not in finding_order
            )
            finding_profile_df = pd.DataFrame(finding_rows)
    finding_total = int(finding_profile_df["Casos"].sum()) if not finding_profile_df.empty else 0
    type_guidance = {
        label: (color, FINDING_TYPE_GUIDANCE.get(label, "Validar classificação."))
        for label, color in {
            "Automático": "#265ee8",
            "Mapeamento": "#7457c8",
            "Manual": "#d78618",
            "Processual": "#168565",
            "Não informado": "#8b96a8",
        }.items()
    }
    finding_focus_map = _build_finding_focus_map(bundle.falhas_gerais)
    default_finding_type = next(
        (str(row["Tipo de falha"]) for _, row in finding_profile_df.iterrows()),
        None,
    )
    finding_focus_json = json.dumps(finding_focus_map, ensure_ascii=False).replace("</", "<\\/")
    finding_types_html = "".join(
        f'<article class="finding-type{" is-active" if str(row["Tipo de falha"]) == default_finding_type else ""}" '
        f'data-finding-type="{_esc(row["Tipo de falha"])}" role="button" tabindex="0" '
        f'style="--type-color:{type_guidance.get(str(row["Tipo de falha"]), ("#8b96a8", "Validar classificação."))[0]}">'
        f'<strong>{format_int_br(int(row["Casos"]))}</strong><b>{_esc(row["Tipo de falha"])}</b>'
        f'<small>{_pct((int(row["Casos"]) / finding_total * 100) if finding_total else 0)}% · '
        f'{_esc(type_guidance.get(str(row["Tipo de falha"]), ("#8b96a8", "Validar classificação."))[1])}</small></article>'
        for _, row in finding_profile_df.iterrows()
    )
    mapping_total = int(
        finding_profile_df.loc[finding_profile_df["Tipo de falha"].eq("Mapeamento"), "Casos"].sum()
    ) if not finding_profile_df.empty else 0
    mapping_top_name = "—"
    mapping_top_n = 0
    if mapping_total and not bundle.falhas_gerais.empty:
        mapping_work = bundle.falhas_gerais.copy()
        if "duplicado_contestacao" in mapping_work.columns:
            mapping_work = mapping_work[~mapping_work["duplicado_contestacao"].fillna(False)]
        mapping_type_col = _find_fg_col(mapping_work, "tipo", "falha")
        mapping_scenario_col = next(
            (col for col in ("Novo cenário", "Cenário", "descricao_padrao") if col in mapping_work.columns),
            None,
        )
        if mapping_type_col and mapping_scenario_col:
            mapping_work = mapping_work[
                mapping_work[mapping_type_col].map(_normalize_tipo_falha_fg).eq("Mapeamento")
            ].copy()
            mapping_scenarios = (
                mapping_work[mapping_scenario_col].fillna("").astype(str).str.strip().replace("", pd.NA).dropna()
            )
            if not mapping_scenarios.empty:
                mapping_counts = mapping_scenarios.value_counts()
                mapping_top_name = _short_motivo(_presentation_motivo(str(mapping_counts.index[0])), 58)
                mapping_top_n = int(mapping_counts.iloc[0])
    mapping_other_n = max(0, mapping_total - mapping_top_n)
    mapping_top_pct = (mapping_top_n / mapping_total * 100) if mapping_total else 0
    finding_context_html = f'''
      <div class="finding-context">
        <div class="finding-context-head">
          <div><span class="eyebrow">Composição dos achados da auditoria</span><h3>De que são formados os {format_int_br(finding_total)} achados</h3></div>
          <p>O <b>tipo</b> orienta a frente de tratamento. O <b>cenário</b> descreve o que foi encontrado. Um mesmo cenário pode aparecer em tipos diferentes sem representar duplicidade. A composição mantém os registros em sua base de origem.</p>
        </div>
        <div class="finding-composition">{finding_types_html}</div>
        <div class="type-scenario">
          <article><b>Tipo do achado</b><span>Clique em um tipo para ver o que o CS precisa validar — especialmente fluxo e produto em Processual.</span></article>
          <i aria-hidden="true">→</i>
          <article><b>Cenário encontrado</b><span>Formatação, sobreposição, ilegibilidade e outros motivos: descreve a situação observada.</span></article>
        </div>
        <div class="finding-type-detail" id="findingTypeDetail"></div>
        <div class="mapping-focus">
          <div><strong>{format_int_br(mapping_total)}</strong><small>achados de Mapeamento · {_pct((mapping_total / finding_total * 100) if finding_total else 0)}% do total</small></div>
          <div><b>{format_int_br(mapping_top_n)} casos ({_pct(mapping_top_pct)}%) em {_esc(mapping_top_name).lower()}</b><p>É a principal concentração de Mapeamento. A oportunidade é validar cobertura, critério de classificação e exceções da regra; os demais {format_int_br(mapping_other_n)} casos permanecem agrupados para análise direcionada.</p></div>
        </div>
      </div>'''
    recurrence_data = contestacao_recurrence + automatic_recurrence
    recurrence_json = json.dumps(recurrence_data, ensure_ascii=False).replace("</", "<\\/")
    recurrence_options_html = "".join(
        f'<option value="{index}">{_esc(_presentation_motivo(item["short"]))}</option>'
        for index, item in enumerate(recurrence_data)
        if item.get("source_key") == "contestacao"
    )
    recurrence_panel_html = (
        f'<div class="recurrence-panel">'
        f'<div class="recurrence-head"><div><span class="eyebrow">Recorrência por motivo</span>'
        f'<h3>Quando o mesmo cenário voltou a aparecer</h3>'
        f'<p>Compare falhas confirmadas e achados automáticos pelo mês em que a análise ocorreu.</p></div>'
        f'<div class="recurrence-controls"><div class="recurrence-source" role="group" aria-label="Fonte da recorrência">'
        f'<button type="button" class="active" data-recurrence-source="contestacao" aria-pressed="true">Falhas confirmadas</button>'
        f'<button type="button" data-recurrence-source="automatico" aria-pressed="false">Automáticos</button></div>'
        f'<div class="recurrence-picker"><label for="recurrence-select">Motivo analisado</label>'
        f'<select id="recurrence-select">{recurrence_options_html}</select></div></div></div>'
        f'<div class="recurrence-summary" id="recurrence-summary" aria-live="polite"></div>'
        f'<div class="recurrence-scroll"><div class="recurrence-track" id="recurrence-track" '
        f'role="list" aria-label="Ocorrências do motivo ao longo do tempo"></div></div>'
        f'<p class="recurrence-note">As fontes permanecem separadas: Contestação mostra falhas confirmadas; Auditoria mostra achados automáticos. Recorrência não comprova, isoladamente, a mesma causa-raiz ou o mesmo protocolo.</p>'
        f'</div>'
        if recurrence_data
        else ""
    )
    automatic_audit_total = int(
        fg_tipo_df.loc[
            fg_tipo_df["Tipo de falha"].eq("Automático"), "Casos"
        ].sum()
    ) if not fg_tipo_df.empty else 0
    automatic_audit_rows = "".join(
        f'<article class="auto-op-row"><div><b>{_esc(_presentation_motivo(item["short"]))}</b>'
        f'<small>{format_int_br(item["total"])} casos · {format_int_br(item["unique_protocols"])} protocolos · '
        f'{format_int_br(item["months"])} meses com registro</small></div>'
        f'<p>{_esc(_automatic_opportunity(item["motivo"]))}</p></article>'
        for item in automatic_recurrence[:4]
    ) or '<p class="auto-empty">Sem achados automáticos classificados no período.</p>'
    automatic_cont_rows = "".join(
        f'<article class="auto-op-row"><div><b>{_esc(_presentation_motivo(item["short"]))}</b>'
        f'<small>{format_int_br(item["cases"])} {"caso" if item["cases"] == 1 else "casos"} · '
        f'{format_int_br(item["protocols"])} {"protocolo" if item["protocols"] == 1 else "protocolos"} · {_esc(item["period"])}</small></div>'
        f'<p>{_esc(item["opportunity"])}</p></article>'
        for item in automatic_contestation
    ) or '<p class="auto-empty">Nenhuma falha automática foi confirmada nas contestações do período.</p>'
    auto_cont_total = sum(item["cases"] for item in automatic_contestation)
    automatic_opportunity_html = f'''
      <div class="automatic-opportunity">
        <div class="automatic-op-head">
          <div><span class="eyebrow">Oportunidades nos casos automáticos</span><h3>Onde revisar regras, critérios e direcionamentos</h3></div>
          <p>As duas fontes são analisadas separadamente. Auditoria mostra achados; Contestação mostra falhas confirmadas.</p>
        </div>
        <div class="auto-lanes">
          <section class="auto-lane audit"><header><div><span>Auditoria</span><strong>{format_int_br(automatic_audit_total)}</strong><small>achados classificados como automáticos</small></div><b>Amplitude para priorização</b></header>{automatic_audit_rows}</section>
          <section class="auto-lane contest"><header><div><span>Contestação</span><strong>{format_int_br(auto_cont_total)}</strong><small>falhas automáticas confirmadas</small></div><b>Baixa base: leitura caso a caso</b></header>{automatic_cont_rows}</section>
        </div>
        <div class="auto-decision"><b>Leitura recomendada ao CS</b><span>Priorizar pela concentração e recorrência da auditoria; usar as contestações automáticas como evidência pontual para validar a regra e as exceções, sem comparar taxas entre as fontes.</span></div>
      </div>'''

    # Causa #1 também pelo FG (Novo cenário) — alinhamento capacitação
    causa_alinh = top_motivo
    if not bundle.falhas_gerais.empty:
        fg_cen = bundle.falhas_gerais.get("Novo cenário", bundle.falhas_gerais.get("Cenário"))
        if fg_cen is not None and not fg_cen.empty:
            vc_fg = fg_cen.fillna("").astype(str).map(lambda x: x.strip()).replace("", pd.NA).dropna()
            if len(vc_fg):
                causa_alinh = str(vc_fg.value_counts().index[0])
    cap = _capacitacao_resumo(bundle, metrics, causa_top=causa_alinh)
    # Pulse: visão de horas das pessoas (atribuição × duração), não soma de salas
    train_hpessoa = float(cap["hora_pessoa"] or 0)
    train_h_series = round(sum(float(r.get("horas") or 0) for r in series), 1)
    train_h = train_hpessoa or train_h_series or float(cap["horas_sessao"] or 0)
    train_sessoes = int(cap["sessoes"] or train_sessoes)
    train_agentes = int(cap["agentes"] or 0)
    train_tema = str(cap["tema_short"] or "—")
    train_causa = str(cap["causa_short"] or "—")
    train_alinhado = bool(cap["alinhado"])
    if train_alinhado:
        align_chip = "Tema alinhado ao motivo #1"
        align_note = (
            f"O Update <b>{_esc(train_tema)}</b> trata a mesma família do motivo #1 "
            f"(<b>{_esc(train_causa).lower()}</b> — formatação/fonte, sobreposição etc.)."
        )
        align_cls = "good"
    else:
        align_chip = "Tema diferente do motivo #1"
        align_note = (
            f"Foi capacitado <b>{_esc(train_tema)}</b>, mas o motivo #1 dos achados é "
            f"<b>{_esc(train_causa).lower()}</b>."
        )
        align_cls = "warn"

    train_span = _train_span_label(series)
    train_peak_months = [r for r in series if (r.get("horas") or 0) > 0]
    train_top2 = sum(
        sorted((float(r.get("horas") or 0) for r in train_peak_months), reverse=True)[:2]
    )
    train_conc_note = ""
    if train_h and train_top2:
        train_conc_note = (
            f"{_fmt_horas(train_top2)} dos {_fmt_horas(train_h)} nos meses de maior capacitação"
        )

    pre_training = []
    for item in series:
        if float(item.get("horas") or 0) > 0:
            break
        if item.get("taxa") is not None:
            pre_training.append(item)
    baseline = pre_training or [r for r in series[:3] if r.get("taxa") is not None]
    baseline_rates = [float(r["taxa"]) for r in baseline if r.get("taxa") is not None]
    baseline_period = (
        baseline[0]["short"].lower()
        if len(baseline) == 1
        else f'{baseline[0]["short"].lower()}–{baseline[-1]["short"].lower()}'
        if baseline
        else "início"
    )
    baseline_value = (
        f"{_pct(min(baseline_rates))}%–{_pct(max(baseline_rates))}%"
        if baseline_rates and min(baseline_rates) != max(baseline_rates)
        else f"{_pct(baseline_rates[0])}%" if baseline_rates else "—"
    )
    executive_timeline = [
        ("attention", "Pico de falhas", peak_month_full, f"{_pct(peak_taxa)}%" if peak_taxa is not None else "—", "maior taxa observada"),
        ("result", "Queda observada", f"{_MES_FULL.get('jun', 'jun')}–{last_short}", f"{_pct(last_taxa)}%" if last_taxa is not None else "—", "melhora recente a sustentar"),
        ("risk", "Risco concentrado", "período", f"{_pct(top_motivo_pct)}%", _short_motivo(top_motivo, 34).lower()),
        ("next-step", "Próximo ciclo", "decisão do CS", "03 definições", "prioridade, referência e governança"),
    ]
    executive_timeline_html = "".join(
        f'<article class="exec-event {kind}"><span>{_esc(period)}</span><i aria-hidden="true"></i>'
        f'<b>{_esc(title)}</b><strong>{_esc(value)}</strong><small>{_esc(detail)}</small></article>'
        for kind, title, period, value, detail in executive_timeline
    )

    # Hero (cenários: queda | alta | estavel | fallback)
    chips = f'<span class="chip">{trend_chip}</span>'
    if partial_latest:
        partial_name = partial_latest["label"].split(" ")[0] if partial_latest.get("label") else "Mês"
        chips += (
            f'<span class="chip">● {_esc(partial_name)} parcial · '
            f'{format_int_br(partial_latest.get("avaliacoes", 0))} contestações · fora da tendência</span>'
        )

    spark_html = "".join(f'<i style="height:{h}%"></i>' for h in sparks)
    bars_html = _bars_procedencia_html(series)
    combo_cols = _combo_chart_html(series)
    n_months = max(len(series), 1)

    # Insights visão
    insights = [hero["insight"]]
    if top_motivo and top_motivo_pct >= 50:
        insights.append(
            (
                "! Risco concentrado",
                f"{format_int_br(top_motivo_n)} de {format_int_br(proc_n)} falhas confirmadas "
                f"({_pct(top_motivo_pct)}%) estão em "
                f"{_esc(_short_motivo(top_motivo, 28).lower())}"
                + (f"; {int(man_pct)}% são manuais." if man_pct else "."),
            )
        )
    insights_html = "".join(
        f'<div class="insight"><b>{t}</b><p>{b}</p></div>' for t, b in insights[:3]
    )

    # Pareto
    pareto_rows = []
    for i, row in enumerate(motivo_df.head(5).itertuples(index=False), start=1):
        pct_v = _parse_pct_str(getattr(row, "Pct", 0))
        casos = int(getattr(row, "Quantidade", getattr(row, "Casos", 0)) or 0)
        motivo = _short_motivo(getattr(row, "Motivo", ""), 48)
        pareto_rows.append(
            f'<div class="row"><span class="rank">{i:02d}</span>'
            f'<div><b>{_esc(motivo)}</b><div class="track"><i style="width:{min(100, pct_v)}%"></i></div></div>'
            f'<div class="value"><b>{format_int_br(casos)}</b><small>{_pct(pct_v)}%</small></div></div>'
        )
    pareto_html = "".join(pareto_rows) or "<p>Sem procedentes no período.</p>"

    # Evolução table + insights (data-driven highlights)
    peak_taxa_bar = max((r["taxa"] or 0) for r in series) or 1.0 if series else 1.0
    falhas_vals = [int(r["falhas"]) for r in series]
    falhas_sorted = sorted(falhas_vals, reverse=True)
    vol_threshold = falhas_sorted[1] if len(falhas_sorted) > 1 else (falhas_sorted[0] if falhas_sorted else 0)
    # spike = top-2 months by falhas (or anything >= second max)
    evo_rows = []
    prev = None
    for r in series:
        taxa = r["taxa"]
        horas = float(r.get("horas") or 0)
        falhas_n = int(r["falhas"] or 0)
        row_cls = ""
        if r["parcial"]:
            row_cls = ' class="partial-row"'
        elif falhas_n and falhas_n >= vol_threshold and vol_threshold > 0 and falhas_n == max(falhas_vals):
            row_cls = ' class="volume-row"'

        if taxa is None:
            delta_html = "—"
            taxa_cell = '<td class="num">—</td>'
        else:
            bar_pct = max(8, int(round(100 * taxa / peak_taxa_bar)))
            taxa_cell = (
                f'<td class="num rate-cell" style="--bar:{bar_pct}%">'
                f"<span>{_pct(taxa)}%</span></td>"
            )
            if prev is None:
                delta_html = '<span class="delta-pill base">base</span>'
            else:
                d = round(taxa - prev, 1)
                if abs(d) < 0.05:
                    delta_html = '<span class="delta-pill base">0 p.p.</span>'
                else:
                    cls = "up" if d > 0 else "down"
                    sign = "+" if d > 0 else "−"
                    delta_html = (
                        f'<span class="delta-pill {cls}">{sign}{_pct(abs(d))} p.p.</span>'
                    )
            prev = taxa

        if falhas_n >= vol_threshold and vol_threshold > 0 and falhas_n > 0:
            falhas_cell = f'<span class="volume-pill">{format_int_br(falhas_n)}</span>'
        else:
            falhas_cell = format_int_br(falhas_n)

        if horas > 0.05:
            horas_cell = f'<span class="hours-pill">{_fmt_horas(horas)}</span>'
        else:
            horas_cell = '<span class="muted-zero">0 h</span>'

        mes = _esc(r["label"].split(" ")[0] if r["label"] else r["short"])
        if r["parcial"]:
            mes += ' <span class="partial">parcial</span>'
        evo_rows.append(
            f"<tr{row_cls}><td>{mes}</td>"
            f"{taxa_cell}"
            f'<td class="num">{delta_html}</td>'
            f'<td class="num">{falhas_cell}</td>'
            f'<td class="num">{format_int_br(r["avaliacoes"])}</td>'
            f'<td class="num">{format_int_br(r["procedentes"])}</td>'
            f'<td class="num">{horas_cell}</td></tr>'
        )

    # table-insights strip
    table_insights_parts = []
    if drop_pp is not None and drop_pp > 0 and peak_month_full:
        table_insights_parts.append(
            f'<article class="good"><strong>−{_pct(drop_pp)} p.p.</strong>'
            f"<span>desde o pico de {peak_month_full}</span></article>"
        )
    # falhas concentration: last 2 months (or top-volume months)
    if series and na_falhas:
        last2 = series[-2:] if len(series) >= 2 else series
        falhas_last2 = sum(int(r["falhas"] or 0) for r in last2)
        span_lbl = "–".join(
            r["short"].lower() for r in last2
        ) if last2 else "período"
        table_insights_parts.append(
            f'<article class="warn"><strong>{format_int_br(falhas_last2)} de {format_int_br(na_falhas)}</strong>'
            f"<span>notificações concentradas em {span_lbl}; base distinta das contestações</span></article>"
        )
    if train_h and train_top2:
        # top months by hours for label
        by_h = sorted(
            [r for r in series if (r.get("horas") or 0) > 0],
            key=lambda x: float(x.get("horas") or 0),
            reverse=True,
        )[:2]
        top_span = "–".join(r["short"].lower() for r in sorted(by_h, key=lambda x: str(x.get("ym") or "")))
        table_insights_parts.append(
            f'<article class="training"><strong>{_fmt_horas(train_top2)} de {_fmt_horas(train_h)}</strong>'
            f"<span>horas das pessoas concentradas em {top_span or train_span}</span></article>"
        )
    table_insights_html = (
        f'<div class="table-insights">{"".join(table_insights_parts)}</div>'
        if table_insights_parts
        else ""
    )

    # Histórico da Contestação: quando as análises foram realizadas e sua dispersão.
    age_html = "".join(
        f'<div class="age-item"><span>{_esc(item["label"])}</span>'
        f'<div class="age-track"><i style="width:{min(100, item["pct"])}%"></i></div>'
        f'<b>{format_int_br(item["qtd"])} · {_pct(item["pct"])}%</b></div>'
        for item in temporal["faixas"]
    ) or '<p>Não há datas de análise disponíveis.</p>'
    history_year_rows = "".join(
        f'<tr><td>{item["ano"]}</td>'
        f'<td class="num">{format_int_br(item["analises"])}</td>'
        f'<td class="num"><b>{_pct(item["participacao"])}%</b></td>'
        f'<td class="num">{format_int_br(item["protocolos"])}</td>'
        f'<td class="num">{format_int_br(item["falhas"])}</td></tr>'
        for item in temporal["anos"]
    ) or '<tr><td colspan="5">Não há histórico disponível.</td></tr>'
    history_month_parts = []
    for item in temporal["meses"]:
        mes_html = _esc(item["mes"])
        if item.get("parcial"):
            mes_html += ' <span class="partial">parcial</span>'
        leitura_html = (
            '<span class="partial">base pequena</span>'
            if item.get("base_baixa")
            else '<span class="delta-pill base">base ≥ 10</span>'
        )
        history_month_parts.append(
            f'<tr><td>{mes_html}</td>'
            f'<td class="num">{format_int_br(item["analises"])}</td>'
            f'<td class="num"><b>{_pct(item["participacao"])}%</b></td>'
            f'<td class="num">{format_int_br(item["protocolos"])}</td>'
            f'<td class="num">{format_int_br(item["falhas"])}</td>'
            f'<td class="num">{leitura_html}</td></tr>'
        )
    history_month_rows = "".join(history_month_parts) or (
        '<tr><td colspan="6">Não há histórico disponível.</td></tr>'
    )
    temporal_partial = next(
        (item["mes"] for item in reversed(temporal["meses"]) if item.get("parcial")),
        "",
    )
    oldest_rows = "".join(
        f'<tr><td>{_esc(item["protocolo"])}</td><td>{_esc(item["data_referencia"])}</td>'
        f'<td>{_esc(item["data_analise"])}</td>'
        f'<td class="num">{_esc(item["idade"])}</td><td>{_esc(item["resultado"])}</td>'
        f'<td>{_esc(_short_motivo(item["tipo"], 34))}</td><td>{_esc(item["etapa"])}</td></tr>'
        for item in temporal["antigos"]
    ) or '<tr><td colspan="7">Não há histórico disponível.</td></tr>'
    mediana_dias = int(temporal["mediana_dias"] or 0)
    if mediana_dias >= 365:
        anos_med = mediana_dias // 365
        dias_med = mediana_dias % 365
        mediana_label = f'{anos_med} ano' + ('s' if anos_med != 1 else '')
        if dias_med:
            mediana_label += f' e {dias_med} dias'
    else:
        mediana_label = f'{mediana_dias} dias'
    temporal_total = int(temporal["total"] or 0)
    temporal_excluded = max(total_aval - temporal_total, 0)
    temporal_scope_text = (
        f"{format_int_br(temporal_total)} das {format_int_br(total_aval)} contestações "
        "possuem datas válidas de recebimento e análise para esta visão temporal."
        if total_aval and temporal_total != total_aval
        else f"As {format_int_br(temporal_total)} contestações possuem as datas necessárias para esta visão temporal."
    )
    temporal_excluded_text = (
        f" {format_int_br(temporal_excluded)} registro"
        f"{'s foram' if temporal_excluded != 1 else ' foi'} excluído"
        f"{'s' if temporal_excluded != 1 else ''} apenas desta análise por ausência ou inconsistência de data."
        if temporal_excluded
        else ""
    )
    temporal_pct_2026 = (
        round(100.0 * int(temporal["analises_2026"] or 0) / temporal_total, 1)
        if temporal_total
        else 0.0
    )
    temporal_pct_anteriores = round(100.0 - temporal_pct_2026, 1) if temporal_total else 0.0

    # Combo note (design tone; values from data)
    train_note_months = ""
    if train_h:
        by_h_note = sorted(
            [r for r in series if (r.get("horas") or 0) > 0],
            key=lambda x: float(x.get("horas") or 0),
            reverse=True,
        )[:2]
        if by_h_note:
            train_note_months = "–".join(
                r["short"].lower()
                for r in sorted(by_h_note, key=lambda x: str(x.get("ym") or ""))
            )
    if train_note_months:
        train_note_display = " e ".join(
            _MES_FULL.get(part, part)
            for part in train_note_months.split("–")
        )
        combo_note = (
            f"A capacitação se concentrou em {train_note_display} e a taxa caiu nos fechamentos seguintes. "
            "Essa coincidência temporal orienta o acompanhamento, mas não comprova que o treinamento causou a queda. "
            "Os próximos fechamentos ajudam a avaliar a sustentação do resultado."
        )
    else:
        combo_note = (
            "Horas = capacitação das pessoas (duração do Update × quem foi atribuído). "
            "A relação com a queda deve ser validada antes de qualquer conclusão."
        )

    # Auditoria mensal
    aud_rows = []
    mensal = metrics.auditados_mensal
    partial_ym = _partial_month_ym(data_fim)
    peak_aud_share = 0.0
    peak_aud_label = "—"
    peak_aud_casos = 0
    peak_aud_falhas = 0
    aud_coverage_note = ""
    if not bundle.auditados.empty and "Data" in bundle.auditados.columns:
        aud_dates = parse_excel_date(bundle.auditados["Data"]).dropna()
        if len(aud_dates):
            aud_coverage_note = f" A cobertura disponível vai até {aud_dates.max().strftime('%d/%m/%Y')}."
    if mensal is not None and not mensal.empty:
        cutoff_ym = pd.Timestamp(data_fim).strftime("%Y-%m") if data_fim is not None else None
        if cutoff_ym and "ym" in mensal.columns:
            mensal = mensal.loc[mensal["ym"].astype(str) <= cutoff_ym].copy()
        if mensal.empty:
            mensal = None
    if mensal is not None and not mensal.empty:
        total_c = int(mensal["casos"].sum()) or 1
        # Share vs KPI de casos (evita divergência se a série mensal ≠ total)
        denom = int(aud_casos) if aud_casos else total_c
        top = mensal.loc[mensal["casos"].astype(int).idxmax()]
        peak_aud_casos = int(top["casos"])
        peak_aud_falhas = int(top.get("falhas") or top.get("na_falhas") or 0)
        peak_aud_share = round(100.0 * peak_aud_casos / max(denom, 1), 1)
        peak_aud_label = str(top.get("label") or top.get("mes") or "—")
        for _, row in mensal.iterrows():
            label = str(row.get("label") or "")
            ym_key = str(row["ym"]) if "ym" in row.index and row["ym"] is not None else ""
            is_partial = bool(partial_ym and ym_key == partial_ym)
            badge = ' <span class="partial">parcial</span>' if is_partial else ""
            casos = int(row.get("casos") or 0)
            falhas = int(row.get("falhas") or row.get("na_falhas") or 0)
            tr_cls = ""
            if is_partial:
                tr_cls = ' class="partial-row"'
            elif casos == peak_aud_casos and peak_aud_casos > 0:
                tr_cls = ' class="volume-row"'
            casos_cell = (
                f"<b>{format_int_br(casos)}</b>"
                if casos == peak_aud_casos and peak_aud_casos > 0
                else format_int_br(casos)
            )
            aud_rows.append(
                f"<tr{tr_cls}><td>{_esc(label)}{badge}</td>"
                f'<td class="num">{casos_cell}</td>'
                f'<td class="num">{format_int_br(falhas)}</td></tr>'
            )
    aud_table = "".join(aud_rows) or "<tr><td colspan='3'>Sem série mensal de auditados.</td></tr>"

    drop_title = (
        f"Queda desde {peak_month_full} · −{_pct(drop_pp)} p.p."
        if drop_pp is not None and drop_pp > 0
        else "Evolução mensal das falhas confirmadas"
    )

    logo_uri = _serasa_logo_data_uri()
    logo_img = (
        f'<img class="brand-logo" src="{logo_uri}" alt="Serasa Experian" width="116" height="52"/>'
        if logo_uri
        else ""
    )
    logo_sep = '<span class="brand-sep" aria-hidden="true"></span>' if logo_uri else ""

    excel_btn = ""
    if excel_export_href:
        href = _esc(excel_export_href)
        excel_btn = (
            f'<a class="btn-export" href="{href}" download '
            f'title="Base Excel com falhas notificadas e contestação">'
            f"⬇ Base Excel</a>"
        )
    rca_btn = ""
    if rca_export_href:
        rca_btn = f'<a class="btn-export" href="{_esc(rca_export_href)}" download title="Baixar Root Cause Analysis em PDF">⬇ RCA</a>'

    nav_html = _build_pulse_nav(exec_mode=modo_executivo)
    partial_bars_note = ""
    if partial_latest and any(r.get("parcial") for r in series):
        partial_name = (partial_latest.get("label") or "Mês parcial").split(" ")[0]
        partial_bars_note = (
            f'<div class="rule" style="margin-top:10px"><span>ⓘ</span>'
            f"<span>O gráfico de tendência usa apenas meses fechados. "
            f"{_esc(partial_name)} está fora da série (base parcial).</span></div>"
        )

    cap_align_details = ""
    if train_h:
        cap_align_details = (
            f'<details class="info-details"><summary>Capacitação × motivo #1</summary>'
            f'<div class="info-body"><p>{align_note}</p>'
            f"<ul><li>Tema capacitado: {_esc(train_tema)}</li>"
            f"<li>Motivo #1 (achados): {_esc(train_causa)}</li>"
            f"<li>{_esc(align_chip)}</li>"
            f"<li>Newsletter não entra nesta conta de horas</li></ul></div></details>"
        )

    exec_mode_js = "true" if modo_executivo else "false"
    analytic_ids_js = json.dumps(sorted(PULSE_ANALYTIC_TABS))
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Quality Overview · {_esc(client_short)}</title>
  <style>{_pulse_css()}</style>
</head>
<body>
  <header>
    <div class="brand">
      {logo_img}
      {logo_sep}
      <div><b>Quality Overview</b><small>Planejamento · visão para decisão do CS</small></div>
    </div>
    <div class="meta">
      <span class="client-badge">Cliente <b>{_esc(client_short)}</b></span>
      <span id="meta-period">{_esc(period_h)}</span>
      {excel_btn}
      {rca_btn}
    </div>
  </header>
  <nav>
    {nav_html}
  </nav>
  <main>

    <section id="visao" class="panel-page active">
      <div class="hero">
        <div class="hero-copy">
          <span class="eyebrow">Planejamento · leitura das bases da Qualidade</span>
          <h1 id="hero-h1">{hero_h1}</h1>
          <p id="hero-p">{hero_p}</p>
          <div class="chips" id="hero-chips">{chips}</div>
        </div>
        <div class="signal">
          <span id="signal-label">{_esc(signal_label)}</span>
          <strong id="signal-value">{_pct(last_taxa) if last_taxa is not None else _pct(taxa_acum)}%</strong>
          <span id="signal-delta" class="{signal_cls}">{_esc(signal_delta)}</span>
          <div class="spark" id="spark">{spark_html}</div>
        </div>
      </div>
      <div class="client-impact" aria-label="Impacto para o cliente">
        <article><small>Impacto observado</small><strong>{_esc(impact_scope)}</strong><p>O número é a dimensão observada da oportunidade; não representa clientes únicos nem perda financeira.</p></article>
        <article><small>O que isso significa para o {_esc(client_short)}</small><b>{_esc(client_implication)}</b><p>O foco é reduzir inconsistência no ponto de decisão, antes que o caso avance para retrabalho ou contestação.</p></article>
        <article><small>Gatilho de acompanhamento</small><b>{_esc(cs_trigger)}</b><p>Revisão no próximo fechamento com foco na causa dominante.</p></article>
      </div>
      <div class="exec-timeline">
        <div class="exec-timeline-head"><b>Linha do tempo executiva do período</b><small>Resultados observados, resposta registrada e decisão seguinte</small></div>
        <div class="exec-events">{executive_timeline_html}</div>
        <p class="exec-timeline-note">A ordem cronológica organiza os eventos do período e orienta o próximo ciclo de acompanhamento.</p>
      </div>
      <div class="grid4">
        <div class="card accent-blue"><span class="label">Contestações que confirmaram falha</span><strong id="kpi-taxa">{_pct(taxa_acum)}%</strong><small id="kpi-taxa-sub">{format_int_br(proc_n)} de {format_int_br(total_aval)} recebidas</small></div>
        <div class="card"><span class="label">Resultado das contestações</span><strong id="kpi-proc">{format_int_br(proc_n)} / {format_int_br(total_aval)}</strong><small id="kpi-proc-sub">{format_int_br(improc_n)} não confirmaram falha</small></div>
        <div class="card accent-amber"><span class="label">Principal motivo encontrado</span><strong id="kpi-motivo">{_pct(top_motivo_pct)}%</strong><small id="kpi-motivo-sub">{format_int_br(top_motivo_n)} das falhas confirmadas</small></div>
        <div class="card accent-green"><span class="label">Análises com mais de 90 dias</span><strong>{_pct(temporal["pct_mais_90"])}%</strong><small>{format_int_br(temporal["mais_90"])} contestações elegíveis</small></div>
      </div>
      <div class="layout" style="margin-top:17px">
        <div class="box">
          <span class="eyebrow">Leitura do período</span>
          <h3>O que o CS precisa saber</h3>
          <div class="insights">{insights_html}</div>
        </div>
        <aside class="box decision">
          <span class="eyebrow">Hipótese para validação</span>
          <h3>{_esc(decision_h3)}</h3>
          <p>{_esc(decision_p)}</p>
          <div class="goal"><small>Meta de referência</small><strong>≤ {_pct(cs_target_rate)}%</strong></div>
        </aside>
      </div>
    </section>

    <section id="procedencia" class="panel-page">
      <div class="section-head">
        <div>
          <span class="eyebrow">Resultado das contestações recebidas</span>
          <h2>A maioria das contestações não confirmou falha</h2>
        </div>
        <p id="proc-summary">{format_int_br(total_aval)} contestações recebidas: {format_int_br(proc_n)} confirmaram falha e {format_int_br(improc_n)} não confirmaram.</p>
      </div>
      <div class="layout">
        <div class="box donut-wrap">
          <span class="definition">Falha confirmada = a Contestação reconheceu a falha</span>
          <div class="donut" id="donut" style="--proc:{taxa_acum}%"><div><strong id="donut-value">{_pct(taxa_acum)}%</strong><span>com falha confirmada</span></div></div>
          <div class="legend">
            <div id="legend-proc"><i class="a"></i><b>{format_int_br(proc_n)}</b> Falhas confirmadas</div>
            <div id="legend-improc"><i class="b"></i><b>{format_int_br(improc_n)}</b> Sem falha confirmada</div>
          </div>
        </div>
        <div class="box">
          <span class="eyebrow">Evolução mensal</span>
          <h3>{_esc(drop_title)}</h3>
          <div class="bars" id="bars-procedencia">{bars_html}</div>
          {partial_bars_note}
        </div>
      </div>
    </section>

    <section id="tempo" class="panel-page">
      <div class="section-head">
        <div>
          <span class="eyebrow">Contestações recebidas desde janeiro/2026</span>
          <h2>{format_int_br(temporal["mais_90"])} de {format_int_br(temporal["total"])} contestações elegíveis tratam de análises com mais de 90 dias</h2>
        </div>
        <p>{_esc(temporal_scope_text)} Recebimentos de <span class="history-period">{_esc(temporal["inicio_ref"])}</span> a <span class="history-period">{_esc(temporal["fim_ref"])}</span>.</p>
      </div>
      <details class="info-details"><summary>Como esta visão foi calculada</summary><div class="info-body"><p>A data registrada ao lado do Auditor informa quando a Contestação foi recebida. A outra data informa quando ocorreu a análise contestada. {_esc(temporal_scope_text + temporal_excluded_text)} Entre os registros elegíveis, {format_int_br(temporal["analises_2026"])} tratam de análises de 2026 e {format_int_br(temporal["analises_anteriores_2026"])} tratam de análises anteriores.</p></div></details>
      <div class="box temporal-universe">
        <span class="eyebrow">Um único universo</span>
        <div class="temporal-total"><strong>{format_int_br(temporal["total"])}</strong><span>contestações elegíveis para análise temporal</span></div>
        <div class="scope-split"><i style="width:{temporal_pct_2026}%"></i><i style="width:{temporal_pct_anteriores}%"></i></div>
        <div class="scope-breakdown">
          <article class="current"><strong>{format_int_br(temporal["analises_2026"])}</strong><span>sobre análises feitas em 2026</span><small>{_pct(temporal_pct_2026)}% das {format_int_br(temporal["total"])} contestações</small></article>
          <article class="previous"><strong>{format_int_br(temporal["analises_anteriores_2026"])}</strong><span>sobre análises feitas antes de 2026</span><small>{_pct(temporal_pct_anteriores)}% das contestações recebidas</small></article>
          <article class="protocols"><strong>{format_int_br(temporal["protocolos"])}</strong><span>protocolos diferentes</span><small>dentro das {format_int_br(temporal["total"])} contestações</small></article>
        </div>
      </div>
      <div class="grid3 history-kpis">
        <div class="card accent-amber"><span class="label">Falhas em análises anteriores</span><strong>{format_int_br(temporal["falhas_anteriores_2026"])}</strong><small>entre as {format_int_br(temporal["analises_anteriores_2026"])} contestações sobre análises anteriores</small></div>
        <div class="card"><span class="label">Tempo mediano até a Contestação</span><strong style="font-size:24px">{_esc(mediana_label)}</strong><small>entre a análise contestada e o recebimento</small></div>
        <div class="card"><span class="label">Recebidas após mais de 90 dias</span><strong>{_pct(temporal["pct_mais_90"])}%</strong><small>{format_int_br(temporal["mais_90"])} das {format_int_br(temporal["total"])} contestações</small></div>
      </div>
      <div class="layout">
        <div class="box">
          <span class="eyebrow">Diferença entre as duas datas</span>
          <h3>Quanto tempo separa a análise contestada do recebimento</h3>
          <div class="age-list">{age_html}</div>
        </div>
        <div class="box">
          <span class="eyebrow">Resumo pelo ano original</span>
          <h3>De quais anos são as análises contestadas em 2026</h3>
          <table class="history-table">
            <thead><tr>{_th_tip("Ano da análise", "Ano em que a análise contestada foi realizada.")}{_th_tip("Contestações", "Contestações recebidas em 2026 sobre análises desse ano.", num=True)}{_th_tip("Participação", "Participação no total de contestações recebidas.", num=True)}{_th_tip("Protocolos", "Protocolos diferentes nessas contestações.", num=True)}{_th_tip("Com falha", "Contestações que confirmaram falha.", num=True)}</tr></thead>
            <tbody>{history_year_rows}</tbody>
          </table>
        </div>
      </div>
      <div class="box" style="margin-top:16px">
        <span class="eyebrow">Mês e ano da análise contestada</span>
        <h3>Quando ocorreram as análises contestadas recebidas em 2026</h3>
        <table class="history-table">
          <thead><tr>{_th_tip("Mês da análise", "Mês e ano em que a análise contestada foi realizada.")}{_th_tip("Contestações", "Contestações recebidas em 2026 sobre análises desse mês.", num=True)}{_th_tip("Participação", "Participação no total de contestações recebidas.", num=True)}{_th_tip("Protocolos", "Protocolos diferentes nessas contestações.", num=True)}{_th_tip("Com falha", "Contestações que confirmaram falha.", num=True)}{_th_tip("Leitura", "Sinaliza meses com menos de 10 contestações.", num=True)}</tr></thead>
          <tbody>{history_month_rows}</tbody>
        </table>
        <div class="rule"><span>ⓘ</span><span>Esta tabela mostra quando ocorreram as análises que foram contestadas em 2026; não representa o mês de recebimento. Meses com menos de 10 contestações aparecem como base pequena.{(' ' + _esc(temporal_partial) + ' é parcial.') if temporal_partial else ''}</span></div>
      </div>
      <details class="info-details" style="margin-top:16px"><summary>Ver as 12 contestações com maior tempo desde a análise</summary><div class="info-body"><p>O tempo compara a data da análise contestada com a data de recebimento da Contestação.</p><table class="old-table"><thead><tr><th>Protocolo</th><th>Recebimento</th><th>Análise contestada</th><th class="num">Tempo</th><th>Resultado</th><th>Tipo de falha</th><th>Etapa</th></tr></thead><tbody>{oldest_rows}</tbody></table></div></details>
      <div class="rule"><span>ⓘ</span><span>O recorte começa em janeiro/2026 pela data de recebimento da Contestação; a análise contestada pode ser de um período anterior.</span></div>
    </section>

    <section id="causas" class="panel-page">
      <div class="section-head">
        <div>
          <span class="eyebrow">Onde as falhas se concentram</span>
          <h2 id="causas-head">{format_int_br(top_motivo_n)} de {format_int_br(proc_n)} falhas confirmadas ({_pct(top_motivo_pct)}%) correspondem ao principal motivo</h2>
        </div>
        <p>Esta leitura mostra onde concentrar controle, checklist e acompanhamento.</p>
      </div>
      <div class="layout">
        <div class="box">
          <span class="eyebrow">Motivos das falhas confirmadas</span>
          <h3 id="causas-proc-n">{format_int_br(proc_n)} falhas confirmadas</h3>
          <div class="pareto" id="pareto-rows">{pareto_html}</div>
        </div>
        <aside class="box dark">
          <span class="eyebrow">Leitura para o CS</span>
          <h3>A maior oportunidade está na execução manual</h3>
          <div class="big"><strong>{_pct(man_pct, 0)}%</strong><span>das falhas confirmadas são classificadas como manuais</span></div>
          <ul>
            <li>Foco: {_esc(_short_motivo(top_motivo, 40).lower())}</li>
            <li>Resposta: checklist visual antes da conclusão</li>
            <li>Validação: amostra semanal por 30 dias</li>
          </ul>
        </aside>
      </div>
      {recurrence_panel_html}
      {finding_context_html}
      {automatic_opportunity_html}
      <div class="box fg">
        <div><span class="eyebrow">Base: achados da auditoria</span><h3>Problemas encontrados nas etapas analisadas</h3></div>
        <div><strong>{format_int_br(fg_n)}</strong><small>achados operacionais</small></div>
        <div><strong>{format_int_br(fg_prot)}</strong><small>protocolos únicos</small></div>
        <div><strong>{_esc(fg_tipo_dom)}</strong><small>tipo mais frequente</small></div>
        <p>{_esc(fg_match_note)}</p>
      </div>
      <div class="rule"><span>ⓘ</span><span>O tipo mais frequente nos achados da auditoria é <b>{_esc(fg_tipo_dom.lower())}</b>, enquanto {_pct(man_pct, 0)}% das falhas confirmadas nas contestações são classificadas como manuais. Não há contradição: são bases diferentes, com objetivos e critérios próprios.</span></div>
    </section>

    <section id="auditoria" class="panel-page">
      <div class="section-head">
        <div>
          <span class="eyebrow">Base: auditoria</span>
          <h2>{_esc(peak_aud_label.split(" ")[0] if peak_aud_label != "—" else "O período")} teve o maior volume de protocolos revisados</h2>
        </div>
        <p>{format_int_br(aud_casos)} protocolos foram revisados; {_pct(peak_aud_share)}% aparecem em {_esc(peak_aud_label.split(" ")[0].lower() if peak_aud_label != "—" else "um mês")}.</p>
      </div>
      <div class="rule" style="margin-bottom:16px"><span>ⓘ</span><span>Este bloco apresenta volumes de bases diferentes. Um protocolo pode ter várias etapas; por isso, registros e protocolos são contagens distintas.</span></div>
      <details class="info-details"><summary>Como interpretar esta visão</summary><div class="info-body"><p>Protocolos e etapas revisados mostram a atividade da auditoria. Falhas notificadas mostram o que foi comunicado ao CS. Os números ajudam a contextualizar o período, mas não devem ser divididos entre si nem interpretados como cobertura ou taxa formal de erro.</p></div></details>
      <div class="grid3">
        <div class="card"><span class="label">Protocolos revisados</span><strong>{format_int_br(aud_casos)}</strong><small>protocolos diferentes no período</small></div>
        <div class="card"><span class="label">Etapas revisadas</span><strong>{format_int_br(aud_regs)}</strong><small>registros analisados</small></div>
        <div class="card"><span class="label">Falhas notificadas</span><strong>{format_int_br(na_falhas)}</strong><small>notificações válidas ao CS no período</small></div>
      </div>
      <table style="margin-top:17px">
        <thead><tr>{_th_tip("Mês", "Mês de referência da série.")}{_th_tip("Protocolos revisados", "Protocolos diferentes revisados no mês.", num=True)}{_th_tip("Notificações válidas", "Notificações válidas ao CS no mês.", num=True)}</tr></thead>
        <tbody>{aud_table}</tbody>
      </table>
      {"<div class='note' style='margin-top:10px'><span>ⓘ</span><div><b>Maior carga de revisão no período</b><p>" + _esc(peak_aud_label) + " concentrou " + format_int_br(peak_aud_casos) + " protocolos revisados e " + format_int_br(peak_aud_falhas) + " falhas comunicadas ao CS." + _esc(aud_coverage_note) + "</p></div></div>" if peak_aud_share >= 40 else ""}
    </section>

    <section id="evolucao" class="panel-page">
      <div class="section-head">
        <div>
          <span class="eyebrow">Base: contestações recebidas</span>
          <h2>Evolução mensal das falhas confirmadas</h2>
        </div>
        <p>{_esc(evo_lead)} A leitura mostra tendência e sinaliza quando o CS deve revisar a causa dominante.</p>
      </div>
      <div class="box">
        <span class="eyebrow">Taxa por mês</span>
        <h3>{_esc(drop_title)}</h3>
        <div class="bars" id="bars-evolucao">{bars_html}</div>
        {partial_bars_note}
      </div>
      {table_insights_html}
      <table class="evolution-table" style="margin-top:16px">
        <thead><tr>{_th_tip("Mês", "Mês de recebimento da Contestação.")}{_th_tip("% de falhas confirmadas", "Contestações que confirmaram falha ÷ total recebido no mês.", num=True)}{_th_tip("Mudança", "Mudança em pontos percentuais em relação ao mês anterior.", num=True)}{_th_tip("Notificações válidas", "Notificações válidas ao CS no mês.", num=True)}{_th_tip("Contestações recebidas", "Total de contestações recebidas no mês.", num=True)}{_th_tip("Com falha", "Contestações recebidas que confirmaram falha.", num=True)}</tr></thead>
        <tbody>{"".join(evo_rows)}</tbody>
      </table>
    </section>

    <section id="metodo" class="panel-page">
      <div class="section-head">
        <div>
          <span class="eyebrow">Contexto do período</span>
          <h2>O que cada visão ajuda o CS a entender</h2>
        </div>
        <p>Uma leitura simples do que foi aberto, comunicado, confirmado, revisado e respondido no período.</p>
      </div>
      <details class="info-details"><summary>Como juntar as leituras</summary><div class="info-body"><p>As solicitações mostram o que foi aberto; as notificações válidas mostram o que foi comunicado ao CS; as contestações mostram o que foi confirmado ou não; os protocolos revisados mostram o universo observado; e os achados mostram problemas encontrados em etapas. Cada visão responde a uma pergunta diferente, por isso os números não precisam ser iguais nem representar os mesmos protocolos.</p></div></details>
      <div class="box business">
        <article><strong>{format_int_br(demandas)}</strong><div><b>Solicitações abertas</b><p>{format_int_br(proto_info)} protocolos informados.</p></div></article>
        <article><strong>{format_int_br(na_falhas)}</strong><div><b>Notificações válidas ao CS</b><p>{format_int_br(na_prot)} protocolos sinalizados.</p></div></article>
        <article><strong>{format_int_br(total_aval)}</strong><div><b>Contestações recebidas</b><p>{format_int_br(proc_n)} confirmaram falha; {format_int_br(improc_n)} não.</p></div></article>
        <article><strong>{format_int_br(aud_casos)}</strong><div><b>Protocolos revisados</b><p>Universo observado no período.</p></div></article>
        <article><strong>{format_int_br(fg_n)}</strong><div><b>Problemas encontrados</b><p>{format_int_br(fg_prot)} protocolos; tipo mais frequente: {_esc(fg_tipo_dom.lower())}.</p></div></article>
      </div>
      <div class="rule"><span>ⓘ</span><span>São perspectivas diferentes do mesmo período, com bases e objetivos próprios.</span></div>
      {"<div class='rule' style='margin-top:10px'><span>⬇</span><span>Base detalhada disponível no botão <b>Base Excel</b>.</span></div>" if excel_export_href else ""}
    </section>

  </main>
  <footer>Quality Overview · Serasa Experian · {_esc(client_short)} · <span id="footer-period">{_esc(period_h)}</span></footer>
  <script>
    const execMode={exec_mode_js};
    const buttons=[...document.querySelectorAll("[data-tab]")],pages=[...document.querySelectorAll(".panel-page")];
    const analyticIds=new Set({analytic_ids_js});
    const toggleBtn=document.getElementById("toggle-analytic");
    function setAnalyticVisible(on){{
      buttons.forEach(b=>{{
        if(analyticIds.has(b.dataset.tab)) b.classList.toggle("hidden",!on);
      }});
      document.querySelector("nav")?.classList.toggle("analytic-expanded",on);
      if(toggleBtn){{
        toggleBtn.setAttribute("aria-pressed",on?"true":"false");
        toggleBtn.textContent=on?"Ocultar detalhes analíticos":"Ver detalhes analíticos";
      }}
    }}
    let analyticOpen=new URLSearchParams(location.search).get("modo")==="completo";
    if(execMode) setAnalyticVisible(analyticOpen);
    if(toggleBtn) toggleBtn.addEventListener("click",()=>{{
      analyticOpen=!analyticOpen;
      setAnalyticVisible(analyticOpen);
    }});
    buttons.forEach(button=>button.addEventListener("click",()=>{{
      buttons.forEach(b=>b.classList.toggle("active",b===button));
      pages.forEach(p=>p.classList.toggle("active",p.id===button.dataset.tab));
      window.scrollTo({{top:0,behavior:"smooth"}});
    }}));
    const recurrenceData={recurrence_json};
    const recurrenceSelect=document.getElementById("recurrence-select");
    const recurrenceTrack=document.getElementById("recurrence-track");
    const recurrenceSummary=document.getElementById("recurrence-summary");
    const recurrenceSourceButtons=[...document.querySelectorAll("[data-recurrence-source]")];
    const plural=(n,one,many)=>n===1?one:many;
    const fmtPctValue=n=>`${{n.toFixed(1).replace(".",",")}}%`;
    const displayMotivo=text=>{{
      const clean=String(text||"").replace(/^doc\\.\\s*/i,"Documento ");
      return clean?clean.charAt(0).toUpperCase()+clean.slice(1):clean;
    }};
    function renderRecurrence(index){{
      const item=recurrenceData[index];
      if(!item||!recurrenceTrack||!recurrenceSummary) return;
      recurrenceTrack.replaceChildren();
      recurrenceSummary.replaceChildren();
      recurrenceTrack.style.setProperty("--rec-cols",String(Math.max(item.events.length,1)));
      const gaps=item.events.slice(1).map(event=>event.gap_months||0);
      const continuous=item.events.length>1&&gaps.every(gap=>gap===1);
      const pattern=item.events.length===1?"Ocorrência isolada":continuous?"Presença contínua":gaps.some(gap=>gap>1)?"Recorrência após intervalo":"Cenário recorrente";
      const first=item.events[0],latest=item.events[item.events.length-1];
      const peak=item.events.reduce((best,event)=>event.occurrences>best.occurrences?event:best,item.events[0]);
      const variation=peak.occurrences?100*(latest.occurrences-peak.occurrences)/peak.occurrences:0;
      const range=first.label===latest.label?first.label:`${{first.label}}–${{latest.label}}`;
      const latestRead=latest===peak
        ?`Último mês no pico: ${{latest.occurrences}} casos`
        :`Último mês: ${{latest.occurrences}} casos · ${{variation>0?"+":""}}${{fmtPctValue(variation)}} vs pico`;
      const patternJoin=continuous?"por":"em";
      const summaryItems=[
        `${{item.total}} casos · ${{item.unique_protocols}} ${{plural(item.unique_protocols,"protocolo","protocolos")}}`,
        `${{pattern}} ${{patternJoin}} ${{item.months}} ${{plural(item.months,"mês","meses")}} · ${{range}}`,
        latestRead
      ];
      summaryItems.forEach(text=>{{
        const span=document.createElement("span");
        span.textContent=text;
        recurrenceSummary.appendChild(span);
      }});
      item.events.forEach((event,eventIndex)=>{{
        const isFirst=eventIndex===0;
        const isLast=eventIndex===item.events.length-1;
        const article=document.createElement("article");
        article.className=`recurrence-event ${{isLast&&item.events.length>1?"latest":isFirst?"first":"repeated"}}`;
        article.setAttribute("role","listitem");
        const time=document.createElement("time");
        time.dateTime=event.period;
        time.textContent=event.label;
        const dot=document.createElement("i");
        dot.setAttribute("aria-hidden","true");
        const label=document.createElement("b");
        const itemName=item.source_key==="automatico"?"achado":"registro";
        label.textContent=item.events.length===1?`${{itemName[0].toUpperCase()+itemName.slice(1)}} no período`:isFirst?`Primeiro ${{itemName}}`:isLast?"Mais recente":event.gap_months===1?"Continuidade":"Recorrência";
        const value=document.createElement("strong");
        value.textContent=`${{event.occurrences}} ${{plural(event.occurrences,"ocorrência","ocorrências")}}`;
        const detail=document.createElement("small");
        const protocolText=`${{event.protocols}} ${{plural(event.protocols,"protocolo","protocolos")}}`;
        const gapText=event.gap_months>1?` · voltou após ${{event.gap_months}} meses`:"";
        detail.textContent=protocolText+gapText;
        article.append(time,dot,label,value,detail);
        recurrenceTrack.appendChild(article);
      }});
    }}
    function selectRecurrenceSource(source){{
      if(!recurrenceSelect) return;
      recurrenceSourceButtons.forEach(button=>{{
        const active=button.dataset.recurrenceSource===source;
        button.classList.toggle("active",active);
        button.setAttribute("aria-pressed",active?"true":"false");
      }});
      recurrenceSelect.replaceChildren();
      recurrenceData.forEach((item,index)=>{{
        if(item.source_key!==source) return;
        const option=document.createElement("option");
        option.value=String(index);
        option.textContent=displayMotivo(item.short);
        recurrenceSelect.appendChild(option);
      }});
      if(recurrenceSelect.options.length) renderRecurrence(Number(recurrenceSelect.value));
    }}
    if(recurrenceSelect){{
      recurrenceSelect.addEventListener("change",()=>renderRecurrence(Number(recurrenceSelect.value)));
      recurrenceSourceButtons.forEach(button=>button.addEventListener("click",()=>selectRecurrenceSource(button.dataset.recurrenceSource)));
      selectRecurrenceSource("contestacao");
    }}
    const FINDING_FOCUS={finding_focus_json};
    const findingTypeCards=[...document.querySelectorAll(".finding-type[data-finding-type]")];
    const findingTypeDetail=document.getElementById("findingTypeDetail");
    function renderFindingTypeDetail(type){{
      if(!findingTypeDetail) return;
      const focus=FINDING_FOCUS[type];
      if(!focus){{
        findingTypeDetail.hidden=true;
        findingTypeDetail.innerHTML="";
        return;
      }}
      const validationTitle=type==="Processual"?"O que o CS precisa validar no produto":"O que o CS precisa validar";
      const validation=(focus.cs_validation||[]).map(item=>"<li>"+escHtml(item)+"</li>").join("");
      const scenarios=(focus.scenarios||[]).map(item=>(
        "<li><b>"+escHtml(item.label)+"</b> — "+escHtml(String(item.count))+" achado(s) · "
        +escHtml(String(item.pct))+"%</li>"
      )).join("");
      findingTypeDetail.hidden=false;
      findingTypeDetail.innerHTML=(
        '<div class="focus-head"><b>'+escHtml(type)+' · '+escHtml(String(focus.total))+' achados</b>'
        +'<span>'+escHtml(focus.opportunity||"")+'</span></div>'
        +(validation?('<h4>'+validationTitle+'</h4><ul>'+validation+'</ul>'):"")
        +(scenarios?('<h4 style="margin-top:10px">Cenários recorrentes</h4><ul>'+scenarios+'</ul>'):"")
      );
    }}
    function selectFindingType(type){{
      findingTypeCards.forEach(card=>{{
        const active=card.dataset.findingType===type;
        card.classList.toggle("is-active",active);
        card.setAttribute("aria-pressed",active?"true":"false");
      }});
      renderFindingTypeDetail(type);
    }}
    findingTypeCards.forEach(card=>{{
      card.addEventListener("click",()=>selectFindingType(card.dataset.findingType||""));
      card.addEventListener("keydown",event=>{{
        if(event.key==="Enter"||event.key===" "){{ event.preventDefault(); selectFindingType(card.dataset.findingType||""); }}
      }});
    }});
    const defaultFindingType=findingTypeCards.find(card=>card.classList.contains("is-active"))?.dataset.findingType
      || findingTypeCards[0]?.dataset.findingType;
    if(defaultFindingType) selectFindingType(defaultFindingType);
  </script>
</body>
</html>"""


def write_quality_pulse(
    path: Path | str,
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    analytics: BRBAnalytics | None = None,
    *,
    periodo_label: str = "",
    data_inicio=None,
    data_fim=None,
    generated_at: datetime | None = None,
    excel_export_href: str | None = None,
    rca_export_href: str | None = None,
    contestacao_historica: pd.DataFrame | None = None,
    modo_executivo: bool = True,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html = render_quality_pulse_html(
        bundle,
        metrics,
        analytics,
        periodo_label=periodo_label,
        data_inicio=data_inicio,
        data_fim=data_fim,
        generated_at=generated_at,
        excel_export_href=excel_export_href,
        rca_export_href=rca_export_href,
        contestacao_historica=contestacao_historica,
        modo_executivo=modo_executivo,
    )
    out.write_text(html, encoding="utf-8")
    return out


def render_pulse_html(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    periodo_label: str,
    out_path: Path,
    analytics: BRBAnalytics | None = None,
    data_fim=None,
    data_inicio=None,
    excel_export_href: str | None = "export_BRB_cliente.xlsx",
    rca_export_href: str | None = None,
    contestacao_historica: pd.DataFrame | None = None,
    modo_executivo: bool = True,
) -> Path:
    """API do CLI — gera Quality Pulse BRB (entregável cliente)."""
    return write_quality_pulse(
        out_path,
        bundle,
        metrics,
        analytics,
        periodo_label=periodo_label,
        data_inicio=data_inicio,
        data_fim=data_fim,
        excel_export_href=excel_export_href,
        rca_export_href=rca_export_href,
        contestacao_historica=contestacao_historica,
        modo_executivo=modo_executivo,
    )
