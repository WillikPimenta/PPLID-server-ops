# -*- coding: utf-8 -*-
"""HTML executivo multi-cliente — visual alinhado ao Quality Overview."""
from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
from typing import Any

from report_brb.brb_format import format_int_br
from report_brb.brb_render_pulse import _pulse_css, _serasa_logo_data_uri

_MONTH_ABBR = ("", "JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html_lib.escape(str(value))


def _pct(value: float) -> str:
    try:
        return f"{float(value):.1f}".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def _int(value: object) -> str:
    try:
        return format_int_br(int(value))
    except (TypeError, ValueError):
        return "—"


def _hours(value: object) -> str:
    try:
        return f"{float(value):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " h"
    except (TypeError, ValueError):
        return "—"


def _mediana_label(dias: int) -> str:
    if dias >= 365:
        anos = dias // 365
        resto = dias % 365
        label = f"{anos} ano" + ("s" if anos != 1 else "")
        if resto:
            label += f" e {resto} dias"
        return label
    return f"{dias} dias"


def _faixa_tip_html(item: dict[str, Any]) -> str:
    clientes = item.get("clientes") or []
    if not clientes:
        return ""
    rows = "".join(
        f'<li><span>{_esc(c.get("nome"))}</span><b>{_int(c.get("qtd"))}</b></li>'
        for c in clientes
    )
    outros = int(item.get("outros_clientes") or 0)
    mais = (
        f'<div class="exec-age-tip-more">+ {_int(outros)} outros clientes</div>'
        if outros
        else ""
    )
    return (
        f'<div class="exec-age-tip" role="tooltip">'
        f'<div class="exec-age-tip-head">Principais clientes</div>'
        f"<ul>{rows}</ul>{mais}</div>"
    )


def _contestacao_faixas_html(faixas: list[dict[str, Any]]) -> str:
    if not faixas:
        return ""
    items = []
    for item in faixas:
        tip = _faixa_tip_html(item)
        has_tip = " has-tip" if tip else ""
        items.append(
            f'<div class="exec-age-item{has_tip}" tabindex="0">'
            f'<span>{_esc(item.get("label"))}</span>'
            f'<div class="exec-age-track"><i style="width:{min(100, float(item.get("pct") or 0))}%"></i></div>'
            f'<b>{_int(item.get("qtd"))} · {_pct(float(item.get("pct") or 0))}%</b>{tip}</div>'
        )
    return f'<div class="exec-age-grid">{"".join(items)}</div>'


def _ano_tip_html(item: dict[str, Any]) -> str:
    clientes = item.get("clientes") or []
    if not clientes:
        return ""
    rows = "".join(
        f'<li><span>{_esc(c.get("nome"))}</span>'
        f'<b>{_int(c.get("qtd"))}</b>'
        f'<em class="recv">{_esc(c.get("recebimento") or "—")}</em></li>'
        for c in clientes
    )
    outros = int(item.get("outros_clientes") or 0)
    mais = (
        f'<div class="exec-age-tip-more">+ {_int(outros)} outros clientes</div>'
        if outros
        else ""
    )
    return (
        f'<div class="exec-age-tip exec-years-tip" role="tooltip">'
        f'<div class="exec-age-tip-head">Cliente · qtd · recebidas no recorte</div>'
        f"<ul>{rows}</ul>{mais}</div>"
    )


def _contestacao_anos_note(*, periodo: str = "") -> str:
    recorte = periodo.strip() or "deste recorte"
    return (
        f"Contestações recebidas entre {recorte}. "
        "A tabela agrupa pelo ano da análise contestada — quando a auditoria ocorreu, "
        "não quando a contestação chegou."
    )


def _contestacao_anos_html(
    anos: list[dict[str, Any]],
    *,
    periodo: str = "",
) -> str:
    if not anos:
        return ""
    rows = []
    for item in anos:
        analises = int(item.get("analises") or 0)
        proc = int(item.get("procedentes") if item.get("procedentes") is not None else item.get("falhas") or 0)
        improc = int(item.get("improcedentes") or max(0, analises - proc))
        tip = _ano_tip_html(item)
        row_class = "exec-years-row has-tip" if tip else "exec-years-row"
        row_attrs = ' tabindex="0"' if tip else ""
        rows.append(
            f'<tr class="{row_class}"{row_attrs}>'
            f'<td><b>{_esc(item.get("ano"))}</b>{tip}</td>'
            f'<td class="num">{_int(analises)}</td>'
            f'<td class="num">{_pct(float(item.get("participacao") or 0))}%</td>'
            f'<td class="num proc">{_int(proc)}</td>'
            f'<td class="num improc">{_int(improc)}</td>'
            f'<td class="num">{_pct(float(item.get("taxa") or 0))}%</td>'
            f"</tr>"
        )
    return (
        f'<div class="exec-timing-years">'
        f"<h4>Análises contestadas por ano de origem</h4>"
        f'<p class="exec-timing-years-note">{_esc(_contestacao_anos_note(periodo=periodo))}</p>'
        f'<table class="exec-years-table"><thead><tr>'
        f"<th>Ano da análise</th><th class=\"num\">Contestações</th><th class=\"num\">Participação</th>"
        f"<th class=\"num\">Procedentes</th><th class=\"num\">Improcedentes</th><th class=\"num\">Taxa proc.</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _slim_clients_for_embed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "slug",
        "nome_curto",
        "ok",
        "auditados",
        "auditados_registros",
        "achados_fg",
        "contestacoes",
        "falhas_confirmadas",
        "sem_falha",
        "contestacoes_decididas",
        "taxa_confirmada_pct",
        "treinamentos",
        "treinamentos_sessoes",
        "treinamentos_agentes",
        "treinamentos_horas",
        "share_confirmadas_pct",
        "monthly_confirmadas",
        "monthly_contestacoes",
        "monthly_achados",
        "monthly_treinamentos_horas",
        "monthly_treinamentos_sessoes",
        "contestacao_timing",
        "diagnostics",
        "workflows",
    )
    workflow_keys = (
        "key",
        "nome",
        "id_workflow",
        "auditados",
        "achados_fg",
        "contestacoes",
        "falhas_confirmadas",
        "sem_falha",
        "contestacoes_decididas",
        "taxa_confirmada_pct",
        "monthly_confirmadas",
        "monthly_contestacoes",
        "monthly_achados",
        "diagnostics",
        "contestacao_timing",
        "sem_carga_periodo",
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("ok"):
            continue
        slim = {key: row.get(key) for key in keys}
        slim["workflows"] = [
            {wk: wf.get(wk) for wk in workflow_keys}
            for wf in (row.get("workflows") or [])
        ]
        out.append(slim)
    return sorted(out, key=lambda r: str(r.get("nome_curto") or "").casefold())


def _month_abbr(ym: str) -> str:
    try:
        return _MONTH_ABBR[int(ym.split("-")[1])]
    except (IndexError, ValueError):
        return ym


def _spark_heights(values: list[int], *, max_px: int = 100) -> list[int]:
    if not values:
        return []
    peak = max(values) or 1
    return [max(12, int(round(max_px * v / peak))) for v in values]


def _executive_v2_css() -> str:
    return """
    nav.exec-nav{position:sticky;top:0;z-index:5;background:#ffffffed;backdrop-filter:blur(10px);border-bottom:1px solid var(--line);display:flex;gap:4px;padding:0 max(20px,calc((100vw - 1240px)/2));overflow:auto}
    nav.exec-nav button{border:0;border-bottom:3px solid transparent;background:transparent;color:var(--muted);padding:15px 14px;white-space:nowrap;font-weight:800;font-size:12px;cursor:pointer}
    nav.exec-nav button.active{color:var(--blue);border-color:var(--blue)}
    .exec-page{display:none}.exec-page.active{display:block}
    .exec-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:15px 0 0}
    .exec-kpis-row2{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0 0}
    .exec-kpis .card b,.exec-kpis-row2 .card b{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}
    .exec-kpis .card strong,.exec-kpis-row2 .card strong{display:block;color:var(--navy);font-size:27px;margin:5px 0}
    .exec-kpis .card small,.exec-kpis-row2 .card small{color:var(--muted);font-size:11px;line-height:1.35}
    .exec-scope-bar{display:grid;grid-template-columns:auto minmax(280px,1fr);gap:8px 14px;align-items:start;margin:0 0 14px;padding:12px 14px;background:#f8fbff;border:1px solid var(--line);border-radius:12px}
    .exec-scope-bar label{font-size:10px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding-top:10px;grid-column:1;grid-row:1}
    .exec-scope-hint{font-size:11px;color:var(--muted);margin:0;grid-column:1/-1;line-height:1.4}
    .exec-client-search{position:relative;grid-column:2;grid-row:1;min-width:0}
    .exec-client-search input{width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;font-size:13px;background:#fff}
    .exec-client-search input:focus{outline:2px solid #cfe0ff;border-color:var(--blue)}
    .exec-export-bar{grid-column:1/-1;display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:2px;padding:10px 12px;border:1px dashed var(--line);border-radius:10px;background:#fff}
    .exec-export-bar[hidden]{display:none!important}
    .exec-export-label{font-size:10px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-right:4px}
    .exec-export-btn{display:inline-flex;align-items:center;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--navy);font-size:11px;font-weight:800;text-decoration:none;cursor:pointer}
    .exec-export-btn:hover{background:#f5f8fd}
    .exec-export-btn.primary{background:var(--navy);color:#fff;border-color:var(--navy)}
    .exec-export-btn.primary:hover{background:#0d2248}
    body.standalone-client .exec-scope-bar{display:none}
    body.standalone-client #portfolioUniverseSection{display:none}
    body.standalone-client #kpiConcentracaoCard{display:none}
    body.standalone-client #exportBar{display:none!important}
    .exec-workflow-details{grid-column:1/-1;border:1px solid var(--line);border-radius:10px;background:#fff;overflow:hidden}
    .exec-workflow-details[hidden]{display:none!important}
    .exec-workflow-details>summary{list-style:none;cursor:pointer;padding:10px 12px;font-size:11px;font-weight:800;color:var(--navy);display:flex;align-items:center;justify-content:space-between;gap:10px;user-select:none;background:#fff}
    .exec-workflow-details>summary::-webkit-details-marker{display:none}
    .exec-workflow-details>summary:after{content:'▸';color:var(--muted);font-size:12px;transition:transform .15s ease}
    .exec-workflow-details[open]>summary:after{transform:rotate(90deg)}
    .exec-workflow-details>summary .exec-workflow-summary-meta{font-weight:600;color:var(--muted);font-size:10px;text-transform:none;letter-spacing:0}
    .exec-workflow-body{padding:0 12px 12px;display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);background:#fafbfd}
    .exec-workflow-wrap input[type=search]{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;background:#fff}
    .exec-workflow-actions{display:flex;gap:8px;flex-wrap:wrap}
    .exec-workflow-actions button{padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;font-size:11px;font-weight:700;cursor:pointer;color:var(--ink)}
    .exec-workflow-actions button:hover{background:#f8fafc}
    .exec-workflow-list{max-height:168px;overflow:auto;border:1px solid var(--line);border-radius:8px;background:#fff;padding:4px;display:flex;flex-direction:column;gap:2px}
    .exec-workflow-option{display:flex;align-items:flex-start;gap:8px;padding:6px 8px;border-radius:6px;font-size:12px;line-height:1.35;cursor:pointer}
    .exec-workflow-option:hover{background:#f8fafc}
    .exec-workflow-option input{margin-top:2px;flex:0 0 auto}
    .exec-workflow-option .meta{display:block;color:var(--muted);font-size:10px;margin-top:2px}
    .exec-workflow-option.muted{opacity:.72}
    .exec-workflow-note{font-size:11px;color:var(--muted);margin:0}
    .exec-client-dropdown{position:absolute;left:0;right:0;top:calc(100% + 4px);max-height:min(320px,52vh);overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 24px #173b7220;z-index:20}
    .exec-client-dropdown-footer{position:sticky;bottom:0;padding:8px 12px;background:#f8fafc;border-top:1px solid var(--line);font-size:10px;color:var(--muted);text-align:center;line-height:1.35}
    .exec-client-dropdown[hidden]{display:none}
    .exec-client-option{display:block;width:100%;border:0;background:transparent;text-align:left;padding:10px 12px;font-size:13px;color:var(--ink);cursor:pointer}
    .exec-client-option:hover,.exec-client-option.active{background:#edf3ff}
    .exec-client-option small{display:block;color:var(--muted);font-size:10px;margin-top:2px}
    .exec-client-option.portfolio{font-weight:800;border-bottom:1px solid var(--line)}
    .exec-kpi-hidden{display:none!important}
    .monthly-action-plan{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:center;margin:14px 0;padding:15px 18px;border:1px solid #b8e2c8;border-radius:13px;background:#f3fff7}
    .monthly-action-plan>b,.monthly-action-plan div>b{display:block;color:var(--navy);font-size:14px}
    .monthly-action-plan p{margin:4px 0 0;color:var(--muted);font-size:11px;line-height:1.45}
    .monthly-action-stats{display:flex;gap:14px;color:var(--muted);font-size:10px;white-space:nowrap}
    .monthly-action-stats b{display:block;color:#15803d;font-size:16px}
    .monthly-bars .training-mark{display:block;color:#15803d;font-size:9px;font-style:normal;font-weight:800;margin-top:2px}
    .exec-timing-box{margin-top:14px;padding:18px 20px;border:1px solid var(--line);border-radius:15px;background:#fff}
    .exec-timing-box h3{margin:0 0 4px;color:var(--navy);font-size:14px}
    .exec-timing-box>p{margin:0 0 12px;color:var(--muted);font-size:11px}
    .exec-age-grid{display:grid;gap:8px}
    .exec-age-item{display:grid;grid-template-columns:110px 1fr auto;gap:10px;align-items:center;font-size:11px}
    .exec-age-item span{color:var(--muted)}
    .exec-age-track{height:8px;background:#edf1f5;border-radius:99px;overflow:hidden}
    .exec-age-track i{display:block;height:100%;background:var(--blue);border-radius:99px}
    .exec-age-item b{color:var(--navy);font-size:11px;white-space:nowrap}
    .exec-age-item.has-tip{position:relative;cursor:default}
    .exec-age-item.has-tip .exec-age-track{cursor:help}
    .exec-age-tip{position:absolute;left:50%;bottom:calc(100% + 8px);transform:translateX(-50%) translateY(4px);min-width:210px;max-width:280px;padding:10px 12px;background:var(--navy);color:#fff;border-radius:10px;box-shadow:0 12px 32px #173b7245;font-size:11px;line-height:1.35;opacity:0;visibility:hidden;pointer-events:none;transition:opacity .15s ease,transform .15s ease,visibility .15s;z-index:30}
    .exec-age-item.has-tip:hover .exec-age-tip,.exec-age-item.has-tip:focus-within .exec-age-tip,.exec-years-row.has-tip:hover .exec-years-tip,.exec-years-row.has-tip:focus-within .exec-years-tip{opacity:1;visibility:visible;transform:translateX(-50%) translateY(0)}
    .exec-age-tip:after{content:'';position:absolute;top:100%;left:50%;margin-left:-5px;border:5px solid transparent;border-top-color:var(--navy)}
    .exec-age-tip-head{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:#9dbdfd;margin-bottom:6px}
    .exec-age-tip ul{list-style:none;margin:0;padding:0}
    .exec-age-tip li{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-top:1px solid #ffffff18}
    .exec-age-tip li:first-child{border-top:0}
    .exec-age-tip li span{color:#e8eef8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
    .exec-age-tip li b{color:#fff;font-size:11px;white-space:nowrap}
    .exec-age-tip-more{margin-top:6px;font-size:10px;color:#9dbdfd}
    .exec-timing-years{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}
    .exec-timing-years h4{margin:0 0 4px;color:var(--navy);font-size:13px}
    .exec-timing-years-note{margin:0 0 10px;color:var(--muted);font-size:10px;line-height:1.4}
    .exec-years-table{width:100%;border-collapse:collapse;font-size:11px}
    .exec-years-table th,.exec-years-table td{padding:8px 10px;border-bottom:1px solid #edf0f4;text-align:left}
    .exec-years-table th.num,.exec-years-table td.num{text-align:right}
    .exec-years-table th{background:#f8fafc;color:var(--muted);font-size:9px;text-transform:uppercase;font-weight:800;letter-spacing:.04em}
    .exec-years-table td b{color:var(--navy)}
    .exec-years-table td.proc{color:#b24640;font-weight:700}
    .exec-years-table td.improc{color:#15765b;font-weight:700}
    .exec-years-row.has-tip{cursor:help}
    .exec-years-row.has-tip td:first-child{position:relative}
    .exec-years-row .exec-years-tip{left:0;right:auto;transform:translateY(4px);min-width:300px;max-width:360px}
    .exec-years-row.has-tip:hover .exec-years-tip,.exec-years-row.has-tip:focus-within .exec-years-tip{transform:translateY(0)}
    .exec-years-tip li{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(96px,auto);gap:8px;align-items:center}
    .exec-years-tip li em.recv{font-style:normal;color:#9dbdfd;font-size:10px;text-align:right;white-space:nowrap}
    .exec-grid2{display:grid;grid-template-columns:1fr;gap:15px;margin-top:15px}
    .exec-story-compact{display:grid;gap:0}
    .exec-story-compact article{display:grid;grid-template-columns:30px 1fr;gap:12px;padding:13px 0;border-top:1px solid #edf0f4}
    .exec-story-compact article:first-child{border-top:0}
    .exec-story-compact .step{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;background:#edf3ff;color:var(--blue);font-weight:900;font-size:11px}
    .exec-story-compact b{color:var(--navy)}.exec-story-compact p{margin:3px 0 0;color:var(--muted);font-size:12px}
    .monthly-panel{background:#fff;border:1px solid var(--line);border-radius:15px;padding:21px;margin-top:15px}
    .monthly-note{padding:11px 13px;border-left:4px solid var(--amber);background:#fff7e9;color:#725a35;font-size:11px;margin-bottom:15px}
    .monthly-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
    .monthly-kpi{padding:14px 15px;border:1px solid var(--line);border-radius:12px;background:#fff}
    .monthly-kpi b{display:block;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
    .monthly-kpi strong{display:block;font-size:22px;color:var(--navy);margin:5px 0 2px;line-height:1.1}
    .monthly-kpi small{display:block;font-size:10px;color:var(--muted);line-height:1.35}
    .monthly-kpi.trend-up strong{color:var(--red)}.monthly-kpi.trend-down strong{color:var(--green)}
    .monthly-bars{display:grid;grid-template-columns:repeat(var(--n-months,8),1fr);gap:8px;align-items:end;height:240px;border-bottom:1px solid var(--line);background:repeating-linear-gradient(#fff 0,#fff 54px,#eef1f5 55px);padding-bottom:4px}
    .month-col{height:100%;display:flex;flex-direction:column;justify-content:end;align-items:center;gap:6px;position:relative;padding-bottom:24px}
    .month-col i{display:block;width:42%;min-height:8px;border-radius:5px 5px 0 0;background:var(--green);border:1px solid #9fd4b8;transition:opacity .15s}
    .month-col:hover i{opacity:.88}
    .month-col.empty i{background:repeating-linear-gradient(135deg,#fff0c9,#fff0c9 6px,#ffe3a5 6px,#ffe3a5 12px);border:1px solid #e9c778}
    .month-col.partial i{background:linear-gradient(180deg,var(--green) 0%,#ffe3a5 100%)}
    .month-col strong{font-size:10px;color:var(--navy);text-align:center;line-height:1.25}
    .month-col.empty strong{color:var(--amber)}
    .month-col small{position:absolute;bottom:0;color:var(--muted);font-size:10px;font-weight:800}
    .month-col-group{display:flex;gap:4px;align-items:flex-end;justify-content:center;width:88%;min-height:8px}
    .month-col-group i{width:46%;min-width:10px}
    .month-col-group i.audit{background:#6d4bc3;border-color:#5a3da8}
    .month-col-group i.confirm{background:var(--green);border:1px solid #9fd4b8}
    .month-col-group i.contest{background:#265ee8;border:1px solid #9dbdfd}
    .month-delta{font-size:9px;font-weight:800;line-height:1.2}
    .month-delta.up{color:var(--red)}.month-delta.down{color:var(--green)}.month-delta.flat{color:var(--muted)}
    .monthly-legend{display:flex;gap:18px;margin-top:28px;color:var(--muted);font-size:11px;flex-wrap:wrap}
    .monthly-legend i{display:inline-block;width:10px;height:10px;margin-right:5px;border-radius:3px;background:var(--green);vertical-align:middle}
    .monthly-legend .audit i{background:#6d4bc3}
    .monthly-legend .contest i{background:#265ee8}
    .monthly-legend .empty i{background:#ffe3a5}
    .monthly-insight{margin-top:14px;padding:12px 14px;border-radius:10px;background:#f5f8fd;border:1px solid #dbe5f2;font-size:11px;color:#4a5d78;line-height:1.45}
    .monthly-table-wrap{margin-top:18px;padding:18px;border:1px solid var(--line);border-radius:15px;background:#fff}
    .monthly-table-head{margin-bottom:12px}.monthly-table-head b{display:block;color:var(--navy);font-size:15px}
    .monthly-table-head small{display:block;margin-top:4px;color:var(--muted);font-size:11px}
    .monthly-table{width:100%;border-collapse:collapse;font-size:11px}
    .monthly-table th,.monthly-table td{padding:10px 12px;border-bottom:1px solid #edf0f4;text-align:right}
    .monthly-table th:first-child,.monthly-table td:first-child{text-align:left}
    .monthly-table th{background:#f8fafc;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.03em}
    .monthly-table tr.peak td{background:#f5f8fd;font-weight:700}
    .monthly-table tr.partial td:first-child{color:var(--amber)}
    .monthly-table .num-muted{color:var(--muted)}
    .diag-grid{display:grid;grid-template-columns:1fr 1.2fr;gap:18px}
    .tipo-cards{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:12px}
    .tipo-card{padding:14px;border:1px solid var(--line);border-radius:12px;background:#fff;border-top:3px solid var(--blue)}
    .tipo-card{cursor:pointer}.tipo-card:focus,.tipo-card:hover{outline:2px solid #265ee855;border-color:#d8e1ed}
    .tipo-card.manual{border-top-color:var(--amber)}.tipo-card.mapeamento{border-top-color:#6d4bc3}
    .tipo-card.processual{border-top-color:var(--green)}.tipo-card.nao-classificado{border-top-color:#98a6b8}
    .tipo-card b{display:block;color:var(--muted);font-size:10px}
    .tipo-card strong{display:block;color:var(--navy);font-size:24px;margin:4px 0}
    .tipo-card span{display:block;color:var(--muted);font-size:10px;line-height:1.35}
    .pareto-rows{margin-top:10px}
    .pareto-row{display:grid;grid-template-columns:28px 1fr 62px;gap:10px;align-items:center;padding:12px 0;border-top:1px solid #edf0f4}
    .pareto-row:first-child{border-top:0}
    .pareto-rank{color:#a8b2c2;font-size:10px;font-weight:900}
    .pareto-track{height:7px;margin-top:6px;background:#eef1f5;border-radius:5px;overflow:hidden}
    .pareto-track i{display:block;height:100%;background:var(--blue);border-radius:5px}
    .pareto-value{text-align:right}.pareto-value b{display:block;color:var(--navy);font-size:16px}
    .pareto-value small{display:block;color:var(--muted);font-size:10px}
    .motivo-timeline{margin-top:18px;padding:20px;border:1px solid var(--line);border-radius:15px;background:#fff}
    .motivo-timeline-head{margin-bottom:14px}.motivo-timeline-head b{display:block;color:var(--navy);font-size:16px}
    .motivo-timeline-head small{color:var(--muted);font-size:11px}
    .motivo-track{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
    .motivo-month{padding:12px;border:1px solid var(--line);border-radius:11px;background:#f8fbff}
    .motivo-month span{display:block;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase}
    .motivo-month b{display:block;color:var(--navy);font-size:12px;margin:6px 0;line-height:1.35}
    .motivo-month strong{display:block;color:var(--blue);font-size:18px}
    .motivo-month small{display:block;margin-top:4px;color:var(--muted);font-size:10px}
    .diag-note{margin-top:12px;padding:11px 13px;border-left:4px solid var(--blue);background:#f5f8fd;color:#4a5d78;font-size:11px;line-height:1.45}
    .tipo-detail{margin-top:12px;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:#f8fafc}.tipo-detail h4{margin:0 0 8px;color:var(--green);font-size:10px;text-transform:uppercase}.tipo-detail p{margin:0;color:#43546d;font-size:10px}.tipo-detail ul{margin:0;padding-left:18px;color:#43546d;font-size:10px;line-height:1.45}.tipo-detail li+li{margin-top:4px}.tipo-export{margin-top:12px;padding:8px 11px;border:0;border-radius:7px;background:var(--navy);color:#fff;font-size:10px;font-weight:800;cursor:pointer}
    .diag-source{margin-top:22px;padding-top:22px;border-top:1px solid var(--line)}
    .diag-source:first-of-type{margin-top:0;padding-top:0;border-top:0}
    .diag-source-head{margin-bottom:14px}.diag-source-head b{display:block;color:var(--navy);font-size:17px}
    .diag-source-head p{margin:6px 0 0;color:var(--muted);font-size:11px;line-height:1.45}
    .diag-source-head .eyebrow{display:block;color:var(--blue);font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
    .diag-source.auditoria .tipo-card{border-top-color:#6d4bc3}
    .diag-source.auditoria .pareto-track i{background:#6d4bc3}
    .diag-source.auditoria .motivo-month strong{color:#6d4bc3}
    .event-timeline{margin-top:18px;padding:20px;border:1px solid var(--line);border-radius:15px;background:#fff}
    .event-timeline-head{margin-bottom:16px}.event-timeline-head b{display:block;color:var(--navy);font-size:16px}
    .event-timeline-head small{color:var(--muted);font-size:11px}
    .event-track{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
    .event-card{padding:14px;border:1px solid var(--line);border-radius:12px;background:#f8fbff;position:relative;overflow:hidden}
    .event-card:before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--blue)}
    .event-card.peak:before{background:var(--amber)}.event-card.attention:before{background:var(--red)}
    .event-card.risk:before{background:#6d4bc3}.event-card.close:before{background:var(--green)}
    .event-card span{display:block;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase;margin-bottom:4px}
    .event-card b{display:block;color:var(--navy);font-size:12px;margin-bottom:6px}
    .event-card strong{display:block;color:var(--navy);font-size:22px;line-height:1}
    .event-card small{display:block;margin-top:6px;color:var(--muted);font-size:11px;line-height:1.4}
    .risk-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
    .risk-summary article{padding:14px;border:1px solid var(--line);border-radius:11px;background:#fff}
    .risk-summary strong{display:block;color:var(--navy);font-size:21px}.risk-summary span{color:var(--muted);font-size:11px}
    .risk-table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}
    .risk-table th,.risk-table td{padding:12px 13px;border-bottom:1px solid #edf0f4;text-align:left;font-size:12px}
    .risk-table th{background:#f8fafc;color:var(--muted);font-size:10px;text-transform:uppercase}
    .risk-table td.num,.risk-table th.num{text-align:right}
    .pill{display:inline-block;padding:4px 7px;border-radius:6px;font-size:10px;font-weight:900}
    .pill.red{background:#fdebea;color:#b24640}.pill.amber{background:#fff0d7;color:#986016}.pill.green{background:#e7f6f0;color:#15765b}
    .risk-bar{height:7px;min-width:80px;background:#edf1f6;border-radius:5px;overflow:hidden}.risk-bar i{display:block;height:100%;background:var(--blue);border-radius:5px}.risk-bar.red i{background:var(--red)}
    .glossary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
    .glossary article{padding:13px;border:1px solid var(--line);border-radius:10px;background:#fff}
    .glossary b{display:block;color:var(--navy);font-size:11px}.glossary span{display:block;margin-top:4px;color:var(--muted);font-size:11px}
    .portfolio-table-wrap{margin-top:18px;overflow:auto}
    .exec-filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
    .exec-filters select{padding:8px 10px;border:1px solid #d8e1ed;border-radius:8px;background:#fff;color:var(--ink);font-size:11px}
    .month-col.audit i{background:#6d4bc3;border-color:#5a3da8}
    .month-col.contest i{background:#265ee8;border-color:#9dbdfd}
    .exec-scope-pill{display:inline-flex;align-items:center;padding:8px 12px;border-radius:8px;background:#edf3ff;border:1px solid #cfe0ff;color:var(--navy);font-size:12px;font-weight:800}
    .exec-scope-pill[hidden]{display:none!important}
    .exec-filters .scope-wrap[hidden]{display:none!important}
    .event-card.audit:before{background:#6d4bc3}
    .card.accent-purple{border-top:3px solid #6d4bc3}
    @media(max-width:850px){.exec-kpis,.exec-kpis-row2{grid-template-columns:repeat(2,1fr)}.exec-grid2,.risk-summary,.glossary,.diag-grid,.tipo-cards,.monthly-kpis{grid-template-columns:repeat(2,1fr)}.monthly-bars{grid-template-columns:repeat(var(--n-months,8),90px);overflow:auto}.monthly-panel,.monthly-table-wrap{overflow:auto}.exec-scope-pill{margin-left:0}.exec-age-item{grid-template-columns:1fr;gap:4px}}
    """


def _compact_story_html(story: dict[str, Any]) -> str:
    titles = ["Duas lentes", "Concentração", "Pressão de qualidade", "Próxima decisão"]
    beats = story.get("beats") or []
    items = []
    for index, beat in enumerate(beats[:4]):
        label = titles[index] if index < len(titles) else str(beat.get("title") or "")
        body = beat.get("body") or ""
        if " · " in str(beat.get("title") or ""):
            body = body or str(beat.get("title"))
        items.append(
            f'<article><div class="step">{index + 1}</div><div>'
            f"<b>{_esc(label)}</b><p>{_esc(body)}</p></div></article>"
        )
    return f'<div class="exec-story-compact">{"".join(items)}</div>'


def _monthly_chart_html(monthly: dict[str, Any]) -> str:
    months = monthly.get("months") or []
    confirmadas = monthly.get("confirmadas") or {}
    partial = monthly.get("partial_month")
    has_data = bool(monthly.get("has_data"))
    if not months:
        return "<p>Sem recorte mensal disponível.</p>"

    peak = max((int(confirmadas.get(m, 0)) for m in months), default=0) or 1
    cols = []
    for ym in months:
        value = int(confirmadas.get(ym, 0))
        height = max(8, int(round(180 * value / peak))) if value else 8
        classes = ["month-col"]
        if value == 0:
            classes.append("empty")
        elif ym == partial:
            classes.append("partial")
        display = _int(value) if value else "—"
        cols.append(
            f'<div class="{" ".join(classes)}"><i style="height:{height}px"></i>'
            f"<strong>{display}</strong><small>{_month_abbr(ym)}</small></div>"
        )

    note = ""
    if not has_data:
        note = (
            '<div class="monthly-note"><b>Sem série mensal:</b> nenhuma contestação com data válida '
            "foi encontrada no recorte.</div>"
        )
    elif partial:
        note = (
            f'<div class="monthly-note"><b>Mês parcial:</b> {_month_abbr(partial)} reflete apenas '
            f"os dias incluídos no recorte — compare com cautela.</div>"
        )

    return (
        f'{note}<div class="monthly-bars" style="--n-months:{len(months)}">{"".join(cols)}</div>'
        '<div class="monthly-legend">'
        '<span><i></i>Falhas confirmadas por mês</span>'
        '<span class="empty"><i></i>Sem confirmação no mês</span>'
        "</div>"
    )


def _timeline_events_html(events: list[dict[str, str]]) -> str:
    if not events:
        return ""
    cards = []
    for event in events:
        kind = event.get("kind") or "open"
        cards.append(
            f'<article class="event-card {kind}"><span>{_esc(event.get("when"))}</span>'
            f"<b>{_esc(event.get('title'))}</b>"
            f"<strong>{_esc(event.get('value'))}</strong>"
            f"<small>{_esc(event.get('detail'))}</small></article>"
        )
    return (
        '<div class="event-timeline"><div class="event-timeline-head">'
        "<b>Linha do tempo de eventos</b>"
        "<small>Marcos reais do recorte · pico mensal · concentração · fechamento</small>"
        "</div>"
        f'<div class="event-track">{"".join(cards)}</div></div>'
    )


def _risk_table_html(risk_rows: list[dict[str, Any]]) -> str:
    body = []
    for row in risk_rows:
        pill = row.get("pill") or "green"
        bar_cls = "risk-bar red" if pill == "red" else "risk-bar"
        reading = row.get("reading") or ""
        reading_html = (
            f'<div class="{bar_cls}"><i style="width:{int(row.get("bar_pct") or 0)}%"></i></div>'
            if pill != "amber" or "amostra" not in reading.lower()
            else _esc(reading)
        )
        body.append(
            f"<tr><td><b>{_esc(row.get('nome_curto'))}</b></td>"
            f"<td class='num'>{_int(row.get('auditados'))}</td>"
            f"<td class='num'>{_int(row.get('contestacoes_decididas'))}</td>"
            f"<td class='num'>{_int(row.get('falhas_confirmadas'))}</td>"
            f"<td class='num'><span class='pill {pill}'>{_pct(row.get('taxa_confirmada_pct', 0))}%</span></td>"
            f"<td>{reading_html}</td></tr>"
        )
    return (
        '<table class="risk-table"><thead><tr>'
        "<th>Cliente</th><th class='num'>Auditados</th><th class='num'>Decisões</th>"
        "<th class='num'>Confirmadas</th><th class='num'>Taxa</th><th>Leitura</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def _portfolio_table(rows: list[dict[str, Any]], *, highlight_slugs: set[str]) -> str:
    peak_taxa = max((float(r.get("taxa_confirmada_pct") or 0) for r in rows if r.get("ok")), default=0.0) or 1.0
    body = []
    for index, row in enumerate(rows, start=1):
        slug = row.get("slug", "")
        cls_parts = ["volume-row"] if slug in highlight_slugs else []
        tr_class = f' class="{" ".join(cls_parts)}"' if cls_parts else ""
        if not row.get("ok"):
            body.append(
                f"<tr{tr_class}><td class='num'>{index}</td>"
                f"<td>{_esc(row.get('nome_curto'))}</td>"
                f"<td colspan='7' class='attention-cell'>{_esc(row.get('error') or 'Erro')}</td></tr>"
            )
            continue
        taxa = float(row.get("taxa_confirmada_pct") or 0)
        bar_pct = max(8, int(round(100 * taxa / peak_taxa))) if taxa else 0
        body.append(
            f"<tr{tr_class}><td class='num'>{index}</td>"
            f"<td><b>{_esc(row.get('nome_curto'))}</b></td>"
            f"<td class='num'>{_int(row.get('auditados'))}</td>"
            f"<td class='num'>{_int(row.get('achados_fg'))}</td>"
            f"<td class='num'>{_int(row.get('contestacoes'))}</td>"
            f"<td class='num'>{_int(row.get('falhas_confirmadas'))}</td>"
            f"<td class='num rate-cell' style='--bar:{bar_pct}%'><span>{_pct(taxa)}%</span></td>"
            f"<td class='num'>{_int(row.get('na_notificadas'))}</td>"
            f"<td class='num'>{_pct(row.get('share_confirmadas_pct', 0))}%</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th class='num'>#</th><th>Cliente</th><th class='num'>Auditados</th>"
        "<th class='num'>Achados</th><th class='num'>Contestações</th>"
        "<th class='num'>Confirmadas</th><th class='num'>Taxa</th>"
        "<th class='num'>NA</th><th class='num'>Share</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def _tipo_card_class(label: str) -> str:
    key = (label or "").strip().lower()
    if key.startswith("manual"):
        return "manual"
    if key.startswith("mapeamento"):
        return "mapeamento"
    if key.startswith("processual"):
        return "processual"
    if "classificado" in key or key.startswith("não informado"):
        return "nao-classificado"
    return "automatico"


def _tipo_cards_html(
    tipos: list[dict[str, Any]],
    total: int,
    *,
    intro: str,
    empty_note: str,
) -> str:
    if not tipos:
        return f'<p class="diag-note">{_esc(empty_note)}</p>'
    cards = []
    for row in tipos:
        label = str(row.get("label") or "—")
        cards.append(
            f'<article class="tipo-card {_tipo_card_class(label)}">'
            f"<b>{_esc(label)}</b>"
            f"<strong>{_int(row.get('casos'))}</strong>"
            f"<span>{_pct(row.get('pct', 0))}% · {_esc(row.get('interpretacao') or '')}</span>"
            "</article>"
        )
    return (
        f"<p>{_esc(intro.format(total=_int(total)))}</p>"
        f'<div class="tipo-cards">{"".join(cards)}</div>'
    )


def _pareto_motivos_html(items: list[dict[str, Any]], *, empty_note: str | None = None) -> str:
    if not items:
        note = empty_note or "Sem motivos dominantes no recorte."
        return f'<p class="diag-note">{_esc(note)}</p>'
    rows = []
    for index, item in enumerate(items, start=1):
        bar = min(100, max(8, int(round(float(item.get("pct") or 0)))))
        rows.append(
            f'<div class="pareto-row">'
            f'<span class="pareto-rank">{index:02d}</span>'
            f"<div><b>{_esc(item.get('motivo'))}</b>"
            f'<div class="pareto-track"><i style="width:{bar}%"></i></div>'
            f"<small>{_esc(item.get('tipo') or '—')}</small></div>"
            f'<div class="pareto-value"><b>{_int(item.get("quantidade"))}</b>'
            f"<small>{_pct(item.get('pct', 0))}%</small></div>"
            "</div>"
        )
    return f'<div class="pareto-rows">{"".join(rows)}</div>'


def _motivo_timeline_html(
    motivos_por_mes: list[dict[str, Any]],
    *,
    title: str,
    subtitle: str,
    count_label: str,
) -> str:
    if not motivos_por_mes:
        return ""
    cards = []
    for row in motivos_por_mes:
        cards.append(
            f'<article class="motivo-month">'
            f"<span>{_esc(row.get('label') or row.get('ym'))}</span>"
            f"<b>{_esc(row.get('top_motivo'))}</b>"
            f"<strong>{_int(row.get('top_quantidade'))}</strong>"
            f"<small>{_pct(row.get('top_pct', 0))}% do mês · {_int(row.get('total'))} {count_label}</small>"
            "</article>"
        )
    return (
        '<div class="motivo-timeline"><div class="motivo-timeline-head">'
        f"<b>{_esc(title)}</b>"
        f"<small>{_esc(subtitle)}</small>"
        "</div>"
        f'<div class="motivo-track">{"".join(cards)}</div></div>'
    )


def _diag_source_section_html(
    source: dict[str, Any],
    *,
    css_class: str,
    eyebrow: str,
    title: str,
    lead: str,
    tipo_intro: str,
    tipo_empty: str,
    pareto_empty: str,
    timeline_title: str,
    timeline_subtitle: str,
    timeline_count_label: str,
) -> str:
    total = int(source.get("total") or 0)
    top = source.get("top_motivo") or {}
    top_line = ""
    if top:
        top_line = (
            f"Principal cenário: <b>{_esc(top.get('motivo'))}</b> "
            f"({_int(top.get('quantidade'))} · {_pct(top.get('pct', 0))}%)"
        )
    inferred_note = ""
    if source.get("tipo_inferred"):
        inferred_note = (
            '<div class="diag-note"><b>Tipo inferido:</b> parte das linhas não tinha coluna explícita '
            "de procedência — classificação derivada de cenário/motivo.</div>"
        )
    dup_note = ""
    dup_n = int(source.get("duplicados_contestacao") or 0)
    if dup_n:
        bruto = int(source.get("achados_bruto") or total)
        dup_note = (
            f'<div class="diag-note"><b>{_int(dup_n)}</b> achado(s) de contestação externa '
            f"já constam na aba Contestação (mesmo protocolo) e foram excluídos desta visão "
            f"para evitar duplicidade. Análise considera <b>{_int(total)}</b> de <b>{_int(bruto)}</b> registros.</div>"
        )
    return f"""
    <section class="diag-source {css_class}">
      <div class="diag-source-head">
        <span class="eyebrow">{_esc(eyebrow)}</span>
        <b>{_esc(title)}</b>
        <p>{lead}</p>
      </div>
      <div class="diag-grid">
        <div class="box">
          <h3>Tipo do achado</h3>
          {_tipo_cards_html(
              source.get("tipos") or [],
              total,
              intro=tipo_intro,
              empty_note=tipo_empty,
          )}
          {inferred_note}
          {dup_note}
        </div>
        <div class="box">
          <h3>Pareto de cenários</h3>
          {f'<p>{top_line}</p>' if top_line else ''}
          {_pareto_motivos_html(source.get("pareto_motivos") or [], empty_note=pareto_empty)}
        </div>
      </div>
      {_motivo_timeline_html(
          source.get("motivos_por_mes") or [],
          title=timeline_title,
          subtitle=timeline_subtitle,
          count_label=timeline_count_label,
      )}
    </section>
    """


def _diagnostics_tab_html(
    diagnostics: dict[str, Any],
    events: list[dict[str, str]],
    *,
    periodo: str,
) -> str:
    contestacao = diagnostics.get("contestacao") or {}
    auditoria = diagnostics.get("auditoria") or {}
    return f"""
    <div class="section-head">
      <div><span class="eyebrow">Diagnóstico</span><h2>Causas e padrões</h2></div>
      <p>Leitura factual do recorte {_esc(periodo)} — achados da auditoria e falhas confirmadas pelo cliente, com origem (automático/manual/mapeamento), cenários recorrentes e marcos temporais. Sem simulação ou meta de taxa.</p>
    </div>
    {_diag_source_section_html(
        auditoria,
        css_class="auditoria",
        eyebrow="Fonte 1",
        title="Achados da auditoria",
        lead="Problemas encontrados nas etapas antes da contestação do cliente. O tipo orienta onde tratar; o cenário descreve o que foi observado.",
        tipo_intro="{total} achados classificados por tipo de falha.",
        tipo_empty="Sem achados da auditoria no recorte para classificar por tipo de falha.",
        pareto_empty="Sem cenários dominantes — nenhum achado da auditoria no período.",
        timeline_title="Recorrência mensal do principal cenário (auditoria)",
        timeline_subtitle="Cenário #1 de achados da auditoria em cada mês · data de análise",
        timeline_count_label="achados",
    )}
    {_diag_source_section_html(
        contestacao,
        css_class="contestacao",
        eyebrow="Fonte 2",
        title="Falhas confirmadas (contestação)",
        lead="Procedências avaliadas pelo cliente como falha. Complementa a visão interna da auditoria com o que foi efetivamente contestado.",
        tipo_intro="{total} falhas confirmadas classificadas por origem operacional.",
        tipo_empty="Sem falhas confirmadas no recorte para classificar por tipo de procedência.",
        pareto_empty="Sem motivos dominantes — nenhuma falha confirmada no período.",
        timeline_title="Recorrência mensal do principal motivo (contestação)",
        timeline_subtitle="Motivo #1 de falhas confirmadas em cada mês · leitura factual, sem projeção",
        timeline_count_label="confirmadas",
    )}
    """


def _portfolio_embed_json(
    payload: dict[str, Any],
    *,
    hero_defaults: dict[str, Any] | None = None,
    standalone_client_slug: str | None = None,
    export_api_base: str | None = None,
    supplement_key: str | None = None,
) -> str:
    monthly = payload.get("monthly") or {}
    data = {
        "diagnostics": payload.get("diagnostics") or {},
        "monthlyScopes": payload.get("monthly_scopes") or {},
        "monthAbbr": {str(i): abbr for i, abbr in enumerate(_MONTH_ABBR) if abbr},
        "totals": payload.get("totals") or {},
        "clients": _slim_clients_for_embed(payload.get("rows") or []),
        "contestacaoTiming": payload.get("contestacao_timing") or {},
        "partialMonth": monthly.get("partial_month"),
        "hasMonthlyData": bool(monthly.get("has_data")),
        "clientCount": int((payload.get("totals") or {}).get("clientes_ok") or 0),
        "generatedAt": payload.get("generated_at"),
        "periodo": payload.get("periodo") or "",
        "inicio": payload.get("inicio"),
        "fim": payload.get("fim"),
        "exportApiBase": export_api_base or "",
        "supplementKey": supplement_key or "",
        "standaloneClientSlug": standalone_client_slug or "",
        "heroDefaults": hero_defaults or {},
    }
    return json.dumps(data, ensure_ascii=False)


def render_executive_portfolio_html(
    payload: dict[str, Any],
    *,
    standalone_client_slug: str | None = None,
    export_api_base: str | None = None,
    supplement_key: str | None = None,
) -> str:
    totals = payload.get("totals") or {}
    top10 = payload.get("top10") or []
    rows = payload.get("rows") or []
    story = payload.get("story") or {}
    monthly = payload.get("monthly") or {}
    events = payload.get("timeline_events") or []
    risk_rows = payload.get("risk_rows") or []
    diagnostics = payload.get("diagnostics") or {}
    periodo = payload.get("periodo") or ""
    top_slugs = {r.get("slug") for r in top10 if r.get("slug")}

    taxa = float(totals.get("taxa_confirmada_pct") or 0)
    confirmadas = int(totals.get("falhas_confirmadas") or 0)
    clientes = int(totals.get("clientes_ok") or 0)
    leader = top10[0] if top10 else {}
    top_share = round(sum(float(r.get("share_confirmadas_pct") or 0) for r in top10), 1)
    top3_share = float(payload.get("top3_share_pct") or 0)
    silent = int(payload.get("silent_clients_count") or 0)
    with_confirmed = len([r for r in rows if r.get("ok") and int(r.get("falhas_confirmadas") or 0) > 0])
    with_contest = len([r for r in rows if r.get("ok") and int(r.get("contestacoes") or 0) > 0])
    top3_confirmadas = sum(int(r.get("falhas_confirmadas") or 0) for r in top10[:3])
    acertos = int(totals.get("sem_falha") or 0)
    decididas = int(totals.get("contestacoes_decididas") or 0)
    contestacoes_total = int(totals.get("contestacoes") or 0)
    auditados_casos = int(totals.get("auditados") or 0)
    auditados_registros = int(totals.get("auditados_registros") or 0)
    timing = payload.get("contestacao_timing") or {}
    mediana_dias = int(timing.get("mediana_dias") or 0)
    mediana_label = _mediana_label(mediana_dias) if mediana_dias else "—"
    faixas_html = _contestacao_faixas_html(timing.get("faixas") or [])
    anos_html = _contestacao_anos_html(timing.get("anos") or [], periodo=periodo)
    timing_note = ""
    if timing.get("total"):
        timing_note = (
            f"{_pct(float(timing.get('pct_mais_90') or 0))}% das contestações com análise "
            f"anterior há mais de 90 dias · base {_int(timing.get('total'))} com datas válidas"
        )

    spark_vals = [int(r.get("falhas_confirmadas") or 0) for r in top10[:7]]
    spark_html = "".join(f'<i style="height:{h}%"></i>' for h in _spark_heights(spark_vals))

    hero_h1 = story.get("headline") or f"Panorama de {clientes} clientes ativos"
    hero_p = story.get("decision_lead") or story.get("subtitle") or f"Consolidado de qualidade no recorte {periodo}."

    logo_uri = _serasa_logo_data_uri()
    logo_img = (
        f'<img class="brand-logo" src="{logo_uri}" alt="Serasa Experian" width="116" height="52"/>'
        if logo_uri
        else ""
    )
    logo_sep = '<span class="brand-sep" aria-hidden="true"></span>' if logo_uri else ""

    hero_defaults = {
        "title": hero_h1,
        "lead": hero_p,
        "chipsHtml": (
            f'<span class="chip">Top 3 · {_pct(top3_share)}% das confirmações</span>'
            f'<span class="chip">Líder · {_esc(leader.get("nome_curto") or "—")}</span>'
            f'<span class="chip">{with_contest} clientes contestaram</span>'
            f'<span class="chip">{_int(silent)} silenciosos</span>'
        ),
        "rate": taxa,
        "decisionsHtml": f"{_int(confirmadas)} de {_int(totals.get('contestacoes_decididas'))} decisões",
        "sparkHtml": spark_html,
        "top10SharePct": top_share,
        "storyHtml": _compact_story_html(story),
        "storyTitle": "A história em quatro pontos",
        "sectionLead": "O painel separa fato e interpretação — com achados da auditoria e confirmações de contestação lado a lado.",
    }
    embed_json = _portfolio_embed_json(
        payload,
        hero_defaults=hero_defaults,
        standalone_client_slug=standalone_client_slug,
        export_api_base=export_api_base,
        supplement_key=supplement_key,
    )
    standalone_slug = (standalone_client_slug or "").strip().lower()
    standalone_row = next(
        (row for row in rows if str(row.get("slug") or "").lower() == standalone_slug),
        rows[0] if standalone_slug and len(rows) == 1 else None,
    )
    page_title = (
        f"Quality Overview · {standalone_row.get('nome_curto') or standalone_slug}"
        if standalone_row
        else "Quality Overview · Portfólio Executivo"
    )
    header_subtitle = (
        f"Visão individual · {standalone_row.get('nome_curto') or standalone_slug}"
        if standalone_row
        else "Portfólio · visão executiva multi-cliente"
    )
    body_class = ' class="standalone-client"' if standalone_row else ""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{_esc(page_title)}</title>
  <style>{_pulse_css()}{_executive_v2_css()}</style>
</head>
<body{body_class}>
  <header>
    <div class="brand">
      {logo_img}
      {logo_sep}
      <div><b>Quality Overview</b><small>{_esc(header_subtitle)}</small></div>
    </div>
    <div class="meta">
      <span class="client-badge" id="scopeBadge">Escopo <b>{clientes} clientes</b></span>
      <span class="tag">{_esc(periodo)}</span>
    </div>
  </header>
  <nav class="exec-nav" aria-label="Navegação principal">
    <button class="active" data-page="resumo">Resumo executivo</button>
    <button data-page="evolucao">Evolução mensal</button>
    <button data-page="riscos">Riscos e falhas</button>
    <button data-page="causas">Causas e padrões</button>
    <button data-page="metodologia">Metodologia</button>
  </nav>
  <main>
    <section id="resumo" class="exec-page active">
      <div class="exec-scope-bar">
        <label for="clientSearch">Recorte</label>
        <div class="exec-client-search" id="clientSearchWrap">
          <input type="search" id="clientSearch" placeholder="Digite o cliente ou escolha portfólio completo…" autocomplete="off" aria-autocomplete="list" aria-controls="clientDropdown" aria-expanded="false">
          <div class="exec-client-dropdown" id="clientDropdown" hidden></div>
        </div>
        <p class="exec-scope-hint">Busque o cliente na lista completa (role se necessário) · workflows opcionais ficam recolhidos abaixo.</p>
        <details class="exec-workflow-details" id="workflowWrap" hidden>
          <summary id="workflowSummary"><span>Workflows</span><span class="exec-workflow-summary-meta" id="workflowSummaryMeta"></span></summary>
          <div class="exec-workflow-body">
            <input type="search" id="workflowFilter" placeholder="Filtrar workflows por nome ou ID…" autocomplete="off" aria-label="Filtrar workflows">
            <div class="exec-workflow-actions">
              <button type="button" id="workflowSelectAll">Selecionar todos</button>
              <button type="button" id="workflowClearAll">Limpar seleção</button>
            </div>
            <div class="exec-workflow-list" id="workflowList" role="group" aria-label="Workflows do cliente"></div>
            <p class="exec-workflow-note" id="workflowNote">Nenhum selecionado = visão consolidada do cliente.</p>
          </div>
        </details>
        <div class="exec-export-bar" id="exportBar" hidden>
          <span class="exec-export-label">Exportar recorte</span>
          <a class="exec-export-btn" id="exportClientHtml" href="#" target="_blank" rel="noopener">Visão HTML do cliente</a>
          <a class="exec-export-btn primary" id="exportClientRca" href="#" target="_blank" rel="noopener">RCA (PDF)</a>
        </div>
      </div>
      <div class="hero">
        <div class="hero-copy">
          <span class="eyebrow">Leitura para decisão</span>
          <h1 id="heroTitle">{_esc(hero_h1)}</h1>
          <p id="heroLead">{_esc(hero_p)}</p>
          <div class="chips" id="heroChips">
            <span class="chip">Top 3 · {_pct(top3_share)}% das confirmações</span>
            <span class="chip">Líder · {_esc(leader.get('nome_curto') or '—')}</span>
            <span class="chip">{with_contest} clientes contestaram</span>
            <span class="chip">{_int(silent)} silenciosos</span>
          </div>
        </div>
        <div class="signal">
          <span>Taxa confirmada ponderada</span>
          <strong id="signalRate">{_pct(taxa)}%</strong>
          <span class="down" id="signalDecisions">{_int(confirmadas)} de {_int(totals.get('contestacoes_decididas'))} decisões</span>
          <div class="spark" id="signalSpark">{spark_html}</div>
        </div>
      </div>

      <div class="exec-kpis">
        <article class="card accent-green"><b>Capacitação</b><strong id="kpiTreinamentos">{_hours(totals.get('treinamentos_horas'))}</strong><small id="kpiTreinamentosDetail">horas realizadas no período</small></article>
        <article class="card accent-red"><b>Falhas confirmadas</b><strong id="kpiConfirmadas">{_int(confirmadas)}</strong><small>contestação · procedentes</small></article>
        <article class="card accent-green"><b>Acertos na contestação</b><strong id="kpiAcertos">{_int(acertos)}</strong><small>improcedentes · operação mantida</small></article>
        <article class="card accent-purple"><b>Achados da auditoria</b><strong id="kpiAchados">{_int(totals.get('achados_fg'))}</strong><small>problemas encontrados nas etapas</small></article>
        <article class="card"><b>Contestações recebidas</b><strong id="kpiContestacoes">{_int(contestacoes_total)}</strong><small id="kpiContestDetail">{_int(decididas)} decididas · {_int(confirmadas)} confirmadas · {_int(acertos)} acertos</small></article>
      </div>
      <div class="exec-kpis-row2">
        <article class="card accent-blue"><b>Protocolos distintos</b><strong id="kpiProtocolos">{_int(auditados_casos)}</strong><small id="kpiRegistros">{_int(auditados_registros)} registros auditados (etapas)</small></article>
        <article class="card accent-amber" id="kpiConcentracaoCard"><b>Concentração top 10</b><strong id="kpiConcentracao">{_pct(top_share)}%</strong><small>das falhas confirmadas</small></article>
        <article class="card"><b>Mediana até contestação</b><strong id="kpiMediana">{_esc(mediana_label)}</strong><small>entre análise contestada e recebimento</small></article>
      </div>
      <div class="exec-timing-box" id="timingBox" style="{'display:block' if faixas_html or anos_html else 'display:none'}">
        <h3>Dispersão do tempo até a contestação</h3>
        <p id="timingNote">{_esc(timing_note)}</p>
        <div id="timingFaixas">{faixas_html}</div>
        <div id="timingAnos">{anos_html}</div>
      </div>

      <div class="section-head" style="margin-top:26px">
        <div><span class="eyebrow">O que importa agora</span><h2>Resumo para executivos</h2></div>
        <p id="execSectionLead">O painel separa fato e interpretação — com achados da auditoria e confirmações de contestação lado a lado.</p>
      </div>
      <div class="exec-grid2">
        <div class="box">
          <h3 id="storyBoxTitle">A história em quatro pontos</h3>
          <div id="storyContent">{_compact_story_html(story)}</div>
        </div>
      </div>

      <div id="timelineWrap">{_timeline_events_html(events)}</div>
    </section>

    <section id="evolucao" class="exec-page">
      <div class="section-head">
        <div><span class="eyebrow">Tendência</span><h2>Evolução mensal</h2></div>
        <p>Série mensal consolidada com leitura comparativa entre achados da auditoria e contestação — apenas dados observados, sem projeção.</p>
      </div>
      <div class="exec-filters">
        <select id="monthMetric" aria-label="Indicador do gráfico">
          <option value="comparativo">Comparativo · auditoria × contestação</option>
          <option value="confirmadas">Falhas confirmadas (contestação)</option>
          <option value="achados">Achados da auditoria</option>
          <option value="contestacoes">Contestações recebidas</option>
        </select>
        <span class="scope-wrap" id="monthScopeWrap">
          <select id="monthScope" aria-label="Agrupamento do portfólio">
            <option value="portfolio">Portfólio completo</option>
            <option value="top10">Top 10 clientes</option>
            <option value="top3">Top 3 clientes</option>
          </select>
        </span>
        <span class="exec-scope-pill" id="monthScopeClientPill" hidden>Cliente selecionado</span>
      </div>
      <div class="monthly-kpis" id="monthlyKpis"></div>
      <div class="monthly-action-plan" id="monthlyActionPlan">
        <div><span class="eyebrow">Contexto de risco · capacitação</span><b id="monthlyActionTitle">Capacitação no período</b><p id="monthlyActionText">As marcações verdes mostram os meses com capacitação e ajudam a contextualizar a evolução dos achados.</p></div>
        <div class="monthly-action-stats"><span><b id="monthlyTrainingHours">0,0 h</b> horas</span><span><b id="monthlyTrainingSessions">0</b> sessões</span><span><b id="monthlyTrainingAverage">0,0 h</b> média/sessão</span></div>
      </div>
      <div class="monthly-panel">
        <div id="monthlyNote"></div>
        <div id="monthlyBars" class="monthly-bars"></div>
        <div class="monthly-legend" id="monthlyLegend"></div>
        <div class="monthly-insight" id="monthlyInsight"></div>
      </div>
      <div class="monthly-table-wrap">
        <div class="monthly-table-head">
          <b>Detalhamento mês a mês</b>
          <small>Todas as métricas no escopo selecionado · taxa = confirmadas ÷ contestações do mês</small>
        </div>
        <table class="monthly-table" id="monthlyTable">
          <thead>
            <tr>
              <th>Mês</th>
              <th>Achados</th>
              <th>Contestações</th>
              <th>Confirmadas</th>
              <th>Taxa</th>
            </tr>
          </thead>
          <tbody id="monthlyTableBody"></tbody>
        </table>
      </div>
    </section>

    <section id="riscos" class="exec-page">
      <div class="section-head">
        <div><span class="eyebrow">Prioridade</span><h2>Riscos e falhas</h2></div>
        <p>Volume e taxa precisam ser lidos juntos. Taxas altas com amostra pequena exigem validação antes de escalar.</p>
      </div>
      <div class="risk-summary">
        <article><strong>{_int(totals.get('achados_fg'))}</strong><span>achados identificados na auditoria</span></article>
        <article><strong>{_pct(top3_share)}%</strong><span>das falhas confirmadas estão no top 3</span></article>
        <article><strong>{_pct(taxa)}%</strong><span>taxa ponderada de confirmação</span></article>
      </div>
      {_risk_table_html(risk_rows)}
      <div class="monthly-note" style="margin-top:14px"><b>Interpretação:</b> maior taxa de confirmação indica maior pressão de qualidade na amostra; não significa, sozinha, pior desempenho ou maior impacto de negócio.</div>

      <div id="portfolioUniverseSection">
      <div class="section-head" style="margin-top:28px">
        <div><span class="eyebrow">Universo completo</span><h2>Todos os clientes ({len(rows)})</h2></div>
        <p>Resumo tabular de todo o portfólio · linhas destacadas compõem o top 10.</p>
      </div>
      <div class="portfolio-table-wrap">{_portfolio_table(rows, highlight_slugs=top_slugs)}</div>
      </div>
    </section>

    <section id="causas" class="exec-page">
      <div id="diagnosticsRoot">
      {_diagnostics_tab_html(diagnostics, events, periodo=periodo)}
      </div>
      <div id="diagnosticsEvents">
      {_timeline_events_html(events)}
      </div>
    </section>

    <section id="metodologia" class="exec-page">
      <div class="section-head">
        <div><span class="eyebrow">Transparência</span><h2>Metodologia e definições</h2></div>
        <p>Esta seção ajuda produto, operação e executivos a lerem o mesmo número da mesma forma.</p>
      </div>
      <div class="glossary">
        <article><b>Taxa confirmada</b><span>Falhas confirmadas ÷ decisões de contestação concluídas.</span></article>
        <article><b>Protocolos distintos</b><span>Casos únicos (protocolo) auditados — soma por cliente, sem deduplicar entre clientes.</span></article>
        <article><b>Registros auditados</b><span>Linhas/etapas auditadas; um protocolo pode gerar vários registros.</span></article>
        <article><b>Mediana até contestação</b><span>Dias entre a data da análise contestada e o recebimento da contestação no recorte.</span></article>
        <article><b>Concentração</b><span>Participação do grupo selecionado no total de falhas confirmadas.</span></article>
        <article><b>Cliente silencioso</b><span>Cliente sem contestação no recorte; separar de ausência de carga.</span></article>
        <article><b>Achados da auditoria</b><span>Problemas encontrados nas etapas analisadas — base distinta da contestação.</span></article>
        <article><b>Falha confirmada</b><span>Quando o cliente contesta e a falha é mantida na análise.</span></article>
        <article><b>Evolução mensal</b><span>Série por mês com comparativo auditoria × contestação, KPIs de pico/média/tendência e tabela detalhada com taxa mensal.</span></article>
        <article><b>Tipo de procedência</b><span>Automático, Manual, Mapeamento ou Processual — indica onde tratar a recorrência.</span></article>
        <article><b>Pareto de motivos</b><span>Ranking dos motivos/cenários das falhas confirmadas no portfólio.</span></article>
      </div>
    </section>
  </main>
  <footer>Quality Overview · Portfólio Executivo · gerado automaticamente a partir dos dados consolidados de qualidade</footer>
  <script id="portfolioData" type="application/json">{embed_json}</script>
  <script>
    const PORTFOLIO = JSON.parse(document.getElementById('portfolioData').textContent || '{{}}');
    const MONTHLY = PORTFOLIO.monthlyScopes || {{}};
    const MONTH_ABBR = PORTFOLIO.monthAbbr || {{}};
    const CLIENTS = PORTFOLIO.clients || [];
    const TOTALS = PORTFOLIO.totals || {{}};
    const HERO = PORTFOLIO.heroDefaults || {{}};

    const state = {{
      scope: 'portfolio',
      metric: 'comparativo',
      clientSlug: '',
      workflowKeys: [],
      workflowFilter: '',
    }};

    const METRIC_LABELS = {{
      comparativo: 'comparativo auditoria × contestação',
      confirmadas: 'falhas confirmadas',
      achados: 'achados da auditoria',
      contestacoes: 'contestações recebidas',
    }};

    const MONTHLY_FIELDS = {{
      confirmadas: 'monthly_confirmadas',
      achados: 'monthly_achados',
      contestacoes: 'monthly_contestacoes',
    }};

    function clientBySlug(slug) {{
      return CLIENTS.find(c => c.slug === slug) || null;
    }}

    function medianaLabel(dias) {{
      const n = Number(dias || 0);
      if (!n) return '—';
      if (n >= 365) {{
        const anos = Math.floor(n / 365);
        const resto = n % 365;
        let label = anos + ' ano' + (anos !== 1 ? 's' : '');
        if (resto) label += ' e ' + resto + ' dias';
        return label;
      }}
      return n + ' dias';
    }}

    function activeClient() {{
      return state.clientSlug ? clientBySlug(state.clientSlug) : null;
    }}

    function activeWorkflows() {{
      const client = activeClient();
      if (!client || !state.workflowKeys.length) return [];
      const keys = new Set(state.workflowKeys);
      return (client.workflows || []).filter(wf => keys.has(wf.key));
    }}

    function activeWorkflow() {{
      const workflows = activeWorkflows();
      return workflows.length === 1 ? workflows[0] : null;
    }}

    function mergeMonthlyMaps(workflows, field) {{
      const out = {{}};
      workflows.forEach(wf => {{
        const map = wf[field] || {{}};
        Object.keys(map).forEach(ym => {{
          out[ym] = (out[ym] || 0) + Number(map[ym] || 0);
        }});
      }});
      return out;
    }}

    function sumWorkflowField(workflows, field) {{
      return workflows.reduce((acc, wf) => acc + Number(wf[field] || 0), 0);
    }}

    function mergeTimingMaps(items) {{
      items = (items || []).filter(Boolean);
      if (!items.length) return {{}};
      if (items.length === 1) return items[0];
      let total = 0;
      let mais90 = 0;
      const faixaMap = {{}};
      const anosMap = {{}};
      items.forEach(timing => {{
        total += Number(timing.total || 0);
        mais90 += Number(timing.mais_90 || 0);
        (timing.faixas || []).forEach(item => {{
          const label = item.label || '—';
          if (!faixaMap[label]) faixaMap[label] = {{ label, qtd: 0 }};
          faixaMap[label].qtd += Number(item.qtd || 0);
        }});
        (timing.anos || []).forEach(item => {{
          const ano = item.ano || '—';
          if (!anosMap[ano]) anosMap[ano] = {{ ano, analises: 0, procedentes: 0, improcedentes: 0 }};
          anosMap[ano].analises += Number(item.analises || 0);
          anosMap[ano].procedentes += Number(item.procedentes != null ? item.procedentes : (item.falhas || 0));
          anosMap[ano].improcedentes += Number(item.improcedentes != null ? item.improcedentes : 0);
        }});
      }});
      const faixas = Object.values(faixaMap).map(item => ({{
        label: item.label,
        qtd: item.qtd,
        pct: total ? Math.round(1000 * item.qtd / total) / 10 : 0,
        clientes: [],
        outros_clientes: 0,
      }}));
      const anos = Object.values(anosMap).map(item => {{
        const analises = item.analises;
        const proc = item.procedentes;
        const improc = item.improcedentes || Math.max(0, analises - proc);
        return {{
          ano: item.ano,
          analises,
          procedentes: proc,
          improcedentes: improc,
          participacao: total ? Math.round(1000 * analises / total) / 10 : 0,
          taxa: analises ? Math.round(1000 * proc / analises) / 10 : 0,
          clientes: [],
          outros_clientes: 0,
        }};
      }});
      const medianas = items.map(t => Number(t.mediana_dias || 0)).filter(n => n > 0).sort((a, b) => a - b);
      const mediana = medianas.length ? medianas[Math.floor(medianas.length / 2)] : 0;
      return {{
        total,
        mediana_dias: mediana,
        pct_mais_90: total ? Math.round(1000 * mais90 / total) / 10 : 0,
        mais_90: mais90,
        faixas,
        anos,
      }};
    }}

    function mergeSourceDiagnostics(items) {{
      items = (items || []).filter(item => item && Number(item.total || 0) >= 0);
      if (!items.length) return {{}};
      if (items.length === 1) return items[0];
      let total = 0;
      const tipoMap = {{}};
      const paretoMap = {{}};
      items.forEach(src => {{
        total += Number(src.total || 0);
        (src.tipos || []).forEach(row => {{
          const label = row.label || '—';
          if (!tipoMap[label]) {{
            tipoMap[label] = {{ label, casos: 0, interpretacao: row.interpretacao || '' }};
          }}
          tipoMap[label].casos += Number(row.casos || 0);
        }});
        (src.pareto_motivos || []).forEach(row => {{
          const motivo = row.motivo || '—';
          if (!paretoMap[motivo]) {{
            paretoMap[motivo] = {{ motivo, tipo: row.tipo || '—', quantidade: 0 }};
          }}
          paretoMap[motivo].quantidade += Number(row.quantidade || 0);
        }});
      }});
      const tipos = Object.values(tipoMap).map(row => ({{
        ...row,
        pct: total ? Math.round(1000 * row.casos / total) / 10 : 0,
      }})).sort((a, b) => b.casos - a.casos);
      const pareto_motivos = Object.values(paretoMap).map(row => ({{
        ...row,
        pct: total ? Math.round(1000 * row.quantidade / total) / 10 : 0,
      }})).sort((a, b) => b.quantidade - a.quantidade).slice(0, 12);
      return {{
        total,
        tipos,
        pareto_motivos,
        top_motivo: pareto_motivos[0] || null,
        motivos_por_mes: [],
        tipo_inferred: items.some(src => src.tipo_inferred),
      }};
    }}

    function mergeWorkflowDiagnostics(workflows) {{
      const contestacao = mergeSourceDiagnostics(workflows.map(wf => (wf.diagnostics || {{}}).contestacao));
      const auditoria = mergeSourceDiagnostics(workflows.map(wf => (wf.diagnostics || {{}}).auditoria));
      return {{ contestacao, auditoria }};
    }}

    function activeMetrics() {{
      const client = activeClient();
      const workflows = activeWorkflows();
      if (!client) {{
        return {{
          nome_curto: 'Portfólio',
          falhas_confirmadas: Number(TOTALS.falhas_confirmadas || 0),
          sem_falha: Number(TOTALS.sem_falha || 0),
          achados_fg: Number(TOTALS.achados_fg || 0),
          contestacoes: Number(TOTALS.contestacoes || 0),
          contestacoes_decididas: Number(TOTALS.contestacoes_decididas || 0),
          auditados: Number(TOTALS.auditados || 0),
          auditados_registros: Number(TOTALS.auditados_registros || 0),
          taxa_confirmada_pct: Number(TOTALS.taxa_confirmada_pct || 0),
          treinamentos: Number(TOTALS.treinamentos || 0),
          treinamentos_sessoes: Number(TOTALS.treinamentos_sessoes || 0),
          treinamentos_agentes: Number(TOTALS.treinamentos_agentes || 0),
          treinamentos_horas: Number(TOTALS.treinamentos_horas || 0),
          timing: PORTFOLIO.contestacaoTiming || {{}},
        }};
      }}
      const source = workflows.length === 1 ? workflows[0] : client;
      let nome = client.nome_curto || client.slug;
      let timing = client.contestacao_timing || {{}};
      let confirmadas = Number(client.falhas_confirmadas || 0);
      let sem_falha = Number(client.sem_falha || 0);
      let achados = Number(client.achados_fg || 0);
      let contestacoes = Number(client.contestacoes || 0);
      let decididas = Number(client.contestacoes_decididas || 0);
      let auditados = Number(client.auditados || 0);
      let taxa = Number(client.taxa_confirmada_pct || 0);
      let treinamentos = Number(client.treinamentos || 0);
      let treinamentosSessoes = Number(client.treinamentos_sessoes || 0);
      let treinamentosAgentes = Number(client.treinamentos_agentes || 0);
      let treinamentosHoras = Number(client.treinamentos_horas || 0);

      if (workflows.length === 1) {{
        const wf = workflows[0];
        nome = (client.nome_curto || client.slug || 'Cliente') + ' · ' + (wf.nome || 'Workflow');
        timing = wf.contestacao_timing || {{}};
        confirmadas = Number(wf.falhas_confirmadas || 0);
        sem_falha = Number(wf.sem_falha || 0);
        achados = Number(wf.achados_fg || 0);
        contestacoes = Number(wf.contestacoes || 0);
        decididas = Number(wf.contestacoes_decididas || 0);
        auditados = Number(wf.auditados || 0);
        taxa = Number(wf.taxa_confirmada_pct || 0);
      }} else if (workflows.length > 1) {{
        confirmadas = sumWorkflowField(workflows, 'falhas_confirmadas');
        sem_falha = sumWorkflowField(workflows, 'sem_falha');
        achados = sumWorkflowField(workflows, 'achados_fg');
        contestacoes = sumWorkflowField(workflows, 'contestacoes');
        decididas = sumWorkflowField(workflows, 'contestacoes_decididas');
        auditados = sumWorkflowField(workflows, 'auditados');
        taxa = decididas ? Math.round(1000 * confirmadas / decididas) / 10 : 0;
        timing = mergeTimingMaps(workflows.map(wf => wf.contestacao_timing));
        nome = (client.nome_curto || client.slug || 'Cliente') + ' · ' + workflows.length + ' workflows';
      }}

      const single = workflows.length === 1 ? workflows[0] : null;
      return {{
        nome_curto: nome,
        falhas_confirmadas: confirmadas,
        sem_falha: sem_falha,
        achados_fg: achados,
        contestacoes: contestacoes,
        contestacoes_decididas: decididas,
        auditados: auditados,
        auditados_registros: Number(client.auditados_registros || 0),
        taxa_confirmada_pct: taxa,
        treinamentos: treinamentos,
        treinamentos_sessoes: treinamentosSessoes,
        treinamentos_agentes: treinamentosAgentes,
        treinamentos_horas: treinamentosHoras,
        timing: timing,
        workflow_nome: single ? (single.nome || '') : (workflows.length > 1 ? (workflows.length + ' selecionados') : ''),
        id_workflow: single ? (single.id_workflow || null) : null,
        workflow_count: workflows.length,
      }};
    }}

    function renderFaixaTip(item) {{
      const clientes = item.clientes || [];
      if (!clientes.length) return '';
      const rows = clientes.map(c => (
        '<li><span>' + escHtml(c.nome || c.slug || '') + '</span><b>' + fmtInt(c.qtd) + '</b></li>'
      )).join('');
      const outros = Number(item.outros_clientes || 0);
      const mais = outros
        ? ('<div class="exec-age-tip-more">+ ' + fmtInt(outros) + ' outros clientes</div>')
        : '';
      return '<div class="exec-age-tip" role="tooltip"><div class="exec-age-tip-head">Principais clientes</div><ul>'
        + rows + '</ul>' + mais + '</div>';
    }}

    function anosNoteText() {{
      const recorte = String(PORTFOLIO.periodo || '').trim() || 'deste recorte';
      return 'Contestações recebidas entre ' + recorte + '. '
        + 'A tabela agrupa pelo ano da análise contestada — quando a auditoria ocorreu, '
        + 'não quando a contestação chegou.';
    }}

    function renderAnoTip(item) {{
      const clientes = item.clientes || [];
      if (!clientes.length) return '';
      const rows = clientes.map(c => (
        '<li><span>' + escHtml(c.nome || c.slug || '') + '</span>'
        + '<b>' + fmtInt(c.qtd) + '</b>'
        + '<em class="recv">' + escHtml(c.recebimento || '—') + '</em></li>'
      )).join('');
      const outros = Number(item.outros_clientes || 0);
      const mais = outros
        ? ('<div class="exec-age-tip-more">+ ' + fmtInt(outros) + ' outros clientes</div>')
        : '';
      return '<div class="exec-age-tip exec-years-tip" role="tooltip">'
        + '<div class="exec-age-tip-head">Cliente · qtd · recebidas no recorte</div>'
        + '<ul>' + rows + '</ul>' + mais + '</div>';
    }}

    function renderAnos(anos) {{
      if (!anos || !anos.length) return '';
      const rows = anos.map(item => {{
        const analises = Number(item.analises || 0);
        const proc = Number(item.procedentes != null ? item.procedentes : (item.falhas || 0));
        const improc = Number(item.improcedentes != null ? item.improcedentes : Math.max(0, analises - proc));
        const tip = renderAnoTip(item);
        const rowClass = tip ? 'exec-years-row has-tip' : 'exec-years-row';
        const rowAttrs = tip ? ' tabindex="0"' : '';
        return '<tr class="' + rowClass + '"' + rowAttrs + '><td><b>' + escHtml(item.ano) + '</b>' + tip + '</td>'
          + '<td class="num">' + fmtInt(analises) + '</td>'
          + '<td class="num">' + fmtPct(Number(item.participacao || 0)) + '</td>'
          + '<td class="num proc">' + fmtInt(proc) + '</td>'
          + '<td class="num improc">' + fmtInt(improc) + '</td>'
          + '<td class="num">' + fmtPct(Number(item.taxa || 0)) + '</td></tr>';
      }}).join('');
      return '<div class="exec-timing-years"><h4>Análises contestadas por ano de origem</h4>'
        + '<p class="exec-timing-years-note">' + escHtml(anosNoteText()) + '</p>'
        + '<table class="exec-years-table"><thead><tr>'
        + '<th>Ano da análise</th><th class="num">Contestações</th><th class="num">Participação</th>'
        + '<th class="num">Procedentes</th><th class="num">Improcedentes</th><th class="num">Taxa proc.</th>'
        + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
    }}

    function renderFaixas(faixas) {{
      if (!faixas || !faixas.length) return '';
      return faixas.map(item => {{
        const tip = renderFaixaTip(item);
        const hasTip = tip ? ' has-tip' : '';
        return '<div class="exec-age-item' + hasTip + '" tabindex="0"><span>' + escHtml(item.label || '') + '</span>'
          + '<div class="exec-age-track"><i style="width:' + Math.min(100, Number(item.pct || 0)) + '%"></i></div>'
          + '<b>' + fmtInt(item.qtd) + ' · ' + fmtPct(Number(item.pct || 0)) + '</b>' + tip + '</div>';
      }}).join('');
    }}

    function activeDiagnostics() {{
      if (!state.clientSlug) return PORTFOLIO.diagnostics || {{}};
      const client = activeClient();
      if (!client) return PORTFOLIO.diagnostics || {{}};
      const workflows = activeWorkflows();
      if (workflows.length === 1 && workflows[0].diagnostics) return workflows[0].diagnostics;
      if (workflows.length > 1) return mergeWorkflowDiagnostics(workflows);
      return client.diagnostics || {{ contestacao: {{}}, auditoria: {{}} }};
    }}

    function diagnosticsScopeLabel() {{
      const workflows = activeWorkflows();
      const client = activeClient();
      if (workflows.length === 1) {{
        const workflow = workflows[0];
        return 'Workflow <b>' + escHtml(workflow.nome || '—') + '</b>'
          + (workflow.id_workflow ? (' · ID ' + escHtml(String(workflow.id_workflow))) : '');
      }}
      if (workflows.length > 1) {{
        return '<b>' + workflows.length + ' workflows</b> selecionados';
      }}
      if (client) return 'Cliente <b>' + escHtml(client.nome_curto || client.slug || '—') + '</b> · todos os workflows';
      return 'Portfólio consolidado';
    }}

    function tipoCardClass(label) {{
      const key = String(label || '').trim().toLowerCase();
      if (key.startsWith('manual')) return 'manual';
      if (key.startsWith('mapeamento')) return 'mapeamento';
      if (key.startsWith('processual')) return 'processual';
      if (key.includes('classificado') || key.startsWith('não informado')) return 'nao-classificado';
      return 'automatico';
    }}

    function renderTipoCards(tipos, total, intro, emptyNote, detailKey) {{
      if (!tipos || !tipos.length) {{
        return '<p class="diag-note">' + escHtml(emptyNote) + '</p>';
      }}
      const allCard = '<article class="tipo-card tipo-all" role="button" tabindex="0" data-tipo="" data-detail-key="' + escHtml(detailKey || '') + '"><b>Todos os tipos</b><strong>' + fmtInt(total) + '</strong><span>Visão consolidada de todos os cenários.</span></article>';
      const cards = tipos.map(row => {{
        const label = String(row.label || '—');
        return '<article class="tipo-card ' + tipoCardClass(label) + '" role="button" tabindex="0" data-tipo="' + escHtml(label) + '" data-detail-key="' + escHtml(detailKey || '') + '"><b>' + escHtml(label) + '</b>'
          + '<strong>' + fmtInt(row.casos) + '</strong>'
          + '<span>' + fmtPct(Number(row.pct || 0)) + ' · ' + escHtml(row.interpretacao || '') + '</span></article>';
      }}).join('');
      return '<p>' + escHtml(intro.replace('{{total}}', fmtInt(total))) + '</p>'
        + '<div class="tipo-cards">' + allCard + cards + '</div>'
        + '<div class="tipo-detail" id="tipo-detail-' + escHtml(detailKey || '') + '" hidden></div>';
    }}

    const processualChecks = [
      'Descrever o fluxo esperado e comparar com a etapa em que o achado ocorreu.',
      'Validar no produto status, permissões, fila, SLA e transições disponíveis para o caso.',
      'Confirmar se a regra de negócio e a configuração do workflow produzem a decisão esperada.',
      'Verificar se o comportamento observado é reproduzível e se ocorre em outros clientes ou workflows.',
      'Registrar impacto para o cliente, evidência, responsável e critério de aceite da correção.'
    ];
    function bindTipoCards(root) {{
      root.querySelectorAll('.tipo-card[data-tipo]').forEach(card => {{
        const activate = () => {{
          const detail = document.getElementById('tipo-detail-' + (card.dataset.detailKey || ''));
          const section = card.closest('.diag-source');
          const source = (window.__diagSources || {{}})[card.dataset.detailKey || ''] || {{}};
          const clickedTipo = card.dataset.tipo || '';
          const tipo = section && section.dataset.activeTipo === clickedTipo ? '' : clickedTipo;
          if (section) section.dataset.activeTipo = tipo;
          const tipoKey = tipo.replace(/\\*$/, '').trim();
          const filtered = (source.por_tipo || {{}})[tipoKey] || {{}};
          if (detail && tipo) {{
            detail.hidden = false;
            const checks = tipo.toLowerCase().startsWith('processual')
              ? '<h4>O que o CS precisa validar no produto</h4><ul>' + processualChecks.map(item => '<li>' + escHtml(item) + '</li>').join('') + '</ul>'
              : '<h4>Tipo selecionado</h4><p>O Pareto e a recorrência mensal foram filtrados para este tipo.</p>';
            detail.innerHTML = checks + '<button type="button" class="tipo-export">Extrair Excel deste tipo</button>';
            detail.querySelector('.tipo-export').addEventListener('click', event => {{
              event.stopPropagation();
              downloadTipoRecords(filtered.records || [], tipoKey);
            }});
          }} else if (detail) {{ detail.hidden = true; detail.innerHTML = ''; }}
          if (!section) return;
          const paretoBox = section.querySelector('.diag-grid .box:nth-child(2)');
          if (paretoBox) {{
            const items = tipoKey ? filtered.pareto_motivos : source.pareto_motivos;
            paretoBox.innerHTML = '<h3>Pareto de cenários</h3>' + renderParetoMotivos(items || [], 'Sem cenários para este tipo.');
          }}
          const timeline = section.querySelector('.motivo-timeline');
          if (timeline) {{
            const items = tipoKey ? filtered.motivos_por_mes : source.motivos_por_mes;
            const track = timeline.querySelector('.motivo-track');
            if (track) track.innerHTML = renderMotivoTimelineCards(items || []);
          }}
        }};
        card.addEventListener('click', activate);
        card.addEventListener('keydown', event => {{ if (event.key === 'Enter' || event.key === ' ') {{ event.preventDefault(); activate(); }} }});
      }});
    }}

    function renderMotivoTimelineCards(items) {{
      return (items || []).map(row => '<article class="motivo-month"><span>' + escHtml(row.label || row.ym || '') + '</span>'
        + '<b>' + escHtml(row.top_motivo || '') + '</b><strong>' + fmtInt(row.top_quantidade) + '</strong>'
        + '<small>' + fmtPct(Number(row.top_pct || 0)) + ' do mês · ' + fmtInt(row.total) + ' registros</small></article>').join('');
    }}
    function downloadTipoRecords(records, tipo) {{
      const columns = ['Cliente', 'Protocolo', 'Cenário', 'Tipo do achado', 'Etapa', 'Workflow'];
      const csvValue = value => '"' + String(value == null ? '' : value).replace(/"/g, '""') + '"';
      const lines = [columns.map(csvValue).join(';')].concat((records || []).map(row => [
        row.cliente, row.protocolo, row.cenario, row.tipo, row.etapa, row.workflow
      ].map(csvValue).join(';')));
      const blob = new Blob(['\\uFEFF' + lines.join('\\r\\n')], {{ type: 'text/csv;charset=utf-8' }});
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'quality_overview_' + String(tipo || 'todos').toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.csv';
      link.click();
      URL.revokeObjectURL(link.href);
    }}

    function renderParetoMotivos(items, emptyNote) {{
      if (!items || !items.length) {{
        return '<p class="diag-note">' + escHtml(emptyNote || 'Sem motivos dominantes no recorte.') + '</p>';
      }}
      return '<div class="pareto-rows">' + items.map((item, index) => {{
        const bar = Math.min(100, Math.max(8, Math.round(Number(item.pct || 0))));
        return '<div class="pareto-row"><span class="pareto-rank">' + String(index + 1).padStart(2, '0') + '</span>'
          + '<div><b>' + escHtml(item.motivo || '') + '</b>'
          + '<div class="pareto-track"><i style="width:' + bar + '%"></i></div>'
          + '<small>' + escHtml(item.tipo || '—') + '</small></div>'
          + '<div class="pareto-value"><b>' + fmtInt(item.quantidade) + '</b>'
          + '<small>' + fmtPct(Number(item.pct || 0)) + '</small></div></div>';
      }}).join('') + '</div>';
    }}

    function renderMotivoTimeline(motivosPorMes, title, subtitle, countLabel) {{
      if (!motivosPorMes || !motivosPorMes.length) return '';
      const cards = motivosPorMes.map(row => (
        '<article class="motivo-month"><span>' + escHtml(row.label || row.ym || '') + '</span>'
        + '<b>' + escHtml(row.top_motivo || '') + '</b>'
        + '<strong>' + fmtInt(row.top_quantidade) + '</strong>'
        + '<small>' + fmtPct(Number(row.top_pct || 0)) + ' do mês · '
        + fmtInt(row.total) + ' ' + escHtml(countLabel) + '</small></article>'
      )).join('');
      return '<div class="motivo-timeline"><div class="motivo-timeline-head"><b>' + escHtml(title) + '</b>'
        + '<small>' + escHtml(subtitle) + '</small></div>'
        + '<div class="motivo-track">' + cards + '</div></div>';
    }}

    function renderDiagSourceSection(source, config) {{
      source = source || {{}};
      const total = Number(source.total || 0);
      const top = source.top_motivo || null;
      let topLine = '';
      if (top && top.motivo) {{
        topLine = '<p>Principal cenário: <b>' + escHtml(top.motivo) + '</b> ('
          + fmtInt(top.quantidade) + ' · ' + fmtPct(Number(top.pct || 0)) + ')</p>';
      }}
      let inferredNote = '';
      if (source.tipo_inferred) {{
        inferredNote = '<div class="diag-note"><b>Tipo inferido:</b> parte das linhas não tinha coluna explícita '
          + 'de procedência — classificação derivada de cenário/motivo.</div>';
      }}
      let dupNote = '';
      const dupN = Number(source.duplicados_contestacao || 0);
      if (dupN) {{
        const bruto = Number(source.achados_bruto || total);
        dupNote = '<div class="diag-note"><b>' + fmtInt(dupN) + '</b> achado(s) de contestação externa '
          + 'já constam na aba Contestação (mesmo protocolo) e foram excluídos desta visão '
          + 'para evitar duplicidade. Análise considera <b>' + fmtInt(total) + '</b> de <b>'
          + fmtInt(bruto) + '</b> registros.</div>';
      }}
      return '<section class="diag-source ' + escHtml(config.cssClass) + '">'
        + '<div class="diag-source-head"><span class="eyebrow">' + escHtml(config.eyebrow) + '</span>'
        + '<b>' + escHtml(config.title) + '</b><p>' + config.lead + '</p></div>'
        + '<div class="diag-grid"><div class="box"><h3>Tipo do achado</h3>'
        + renderTipoCards(source.tipos || [], total, config.tipoIntro, config.tipoEmpty, config.cssClass)
        + inferredNote + dupNote + '</div>'
        + '<div class="box"><h3>Pareto de cenários</h3>' + topLine
        + renderParetoMotivos(source.pareto_motivos || [], config.paretoEmpty) + '</div></div>'
        + renderMotivoTimeline(
          source.motivos_por_mes || [],
          config.timelineTitle,
          config.timelineSubtitle,
          config.timelineCountLabel,
        ) + '</section>';
    }}

    function renderDiagnostics() {{
      const root = document.getElementById('diagnosticsRoot');
      if (!root) return;
      const diagnostics = activeDiagnostics();
      const contestacao = diagnostics.contestacao || {{}};
      const auditoria = diagnostics.auditoria || {{}};
      window.__diagSources = {{ auditoria, contestacao }};
      const periodo = String(PORTFOLIO.periodo || '').trim() || 'do recorte';
      const scoped = Boolean(state.clientSlug);
      const scopeLine = scoped
        ? ('<p class="diag-scope-note">Escopo: ' + diagnosticsScopeLabel() + '</p>')
        : '';
      root.innerHTML = '<div class="section-head"><div><span class="eyebrow">Diagnóstico</span><h2>Causas e padrões</h2></div>'
        + '<p>Leitura factual do recorte ' + escHtml(periodo)
        + ' — achados da auditoria e falhas confirmadas pelo cliente, com origem (automático/manual/mapeamento), '
        + 'cenários recorrentes e marcos temporais. Sem simulação ou meta de taxa.</p>'
        + scopeLine + '</div>'
        + renderDiagSourceSection(auditoria, {{
          cssClass: 'auditoria',
          eyebrow: 'Fonte 1',
          title: 'Achados da auditoria',
          lead: 'Problemas encontrados nas etapas antes da contestação do cliente. O tipo orienta onde tratar; o cenário descreve o que foi observado.',
          tipoIntro: '{{total}} achados classificados por tipo de falha.',
          tipoEmpty: 'Sem achados da auditoria no recorte para classificar por tipo de falha.',
          paretoEmpty: 'Sem cenários dominantes — nenhum achado da auditoria no período.',
          timelineTitle: 'Recorrência mensal do principal cenário (auditoria)',
          timelineSubtitle: 'Cenário #1 de achados da auditoria em cada mês · data de análise',
          timelineCountLabel: 'achados',
        }})
        + renderDiagSourceSection(contestacao, {{
          cssClass: 'contestacao',
          eyebrow: 'Fonte 2',
          title: 'Falhas confirmadas (contestação)',
          lead: 'Procedências avaliadas pelo cliente como falha. Complementa a visão interna da auditoria com o que foi efetivamente contestado.',
          tipoIntro: '{{total}} falhas confirmadas classificadas por origem operacional.',
          tipoEmpty: 'Sem falhas confirmadas no recorte para classificar por tipo de procedência.',
          paretoEmpty: 'Sem motivos dominantes — nenhuma falha confirmada no período.',
          timelineTitle: 'Recorrência mensal do principal motivo (contestação)',
          timelineSubtitle: 'Motivo #1 de falhas confirmadas em cada mês · leitura factual, sem projeção',
          timelineCountLabel: 'confirmadas',
        }});
      bindTipoCards(root);
    }}

    function applyClientScope() {{
      const view = activeMetrics();
      const isPortfolio = !state.clientSlug;
      const confirmadas = view.falhas_confirmadas;
      const acertos = view.sem_falha;
      const decididas = view.contestacoes_decididas;

      const badge = document.getElementById('scopeBadge');
      if (badge) {{
        if (isPortfolio) {{
          badge.innerHTML = 'Escopo <b>' + fmtInt(PORTFOLIO.clientCount || 0) + ' clientes</b>';
        }} else if (activeWorkflows().length) {{
          const wfs = activeWorkflows();
          badge.innerHTML = 'Cliente <b>' + escHtml(activeClient()?.nome_curto || '—') + '</b> · '
            + (wfs.length === 1
              ? ('Workflow <b>' + escHtml(wfs[0]?.nome || '—') + '</b>')
              : ('<b>' + wfs.length + ' workflows</b> selecionados'));
        }} else {{
          badge.innerHTML = 'Cliente <b>' + escHtml(view.nome_curto || '—') + '</b>';
        }}
      }}

      const setText = (id, value) => {{ const el = document.getElementById(id); if (el) el.textContent = value; }};
      setText('kpiConfirmadas', fmtInt(confirmadas));
      setText('kpiAcertos', fmtInt(acertos));
      setText('kpiAchados', fmtInt(view.achados_fg));
      setText('kpiContestacoes', fmtInt(view.contestacoes));
      setText('kpiTreinamentos', fmtHours(view.treinamentos_horas));
      setText('kpiTreinamentosDetail', fmtInt(view.treinamentos_sessoes) + ' sessões realizadas no período');
      setText('kpiContestDetail', fmtInt(decididas) + ' decididas · ' + fmtInt(confirmadas) + ' confirmadas · ' + fmtInt(acertos) + ' acertos');
      setText('kpiProtocolos', fmtInt(view.auditados));
      setText('kpiRegistros', fmtInt(view.auditados_registros) + ' registros auditados (etapas)');
      setText('kpiMediana', medianaLabel(view.timing?.mediana_dias));

      const concCard = document.getElementById('kpiConcentracaoCard');
      if (concCard) concCard.classList.toggle('exec-kpi-hidden', !isPortfolio);
      if (isPortfolio) setText('kpiConcentracao', fmtPct(Number(HERO.top10SharePct || 0)));

      const timingBox = document.getElementById('timingBox');
      const faixas = view.timing?.faixas || [];
      const anos = view.timing?.anos || [];
      if (timingBox) {{
        timingBox.style.display = (faixas.length || anos.length) ? 'block' : 'none';
        const note = document.getElementById('timingNote');
        if (note && faixas.length) {{
          note.textContent = fmtPct(Number(view.timing?.pct_mais_90 || 0)) + ' das contestações com análise anterior há mais de 90 dias · base '
            + fmtInt(view.timing?.total || 0) + ' com datas válidas';
        }} else if (note) {{
          note.textContent = '';
        }}
        const faixasEl = document.getElementById('timingFaixas');
        if (faixasEl) faixasEl.innerHTML = faixas.length ? ('<div class="exec-age-grid">' + renderFaixas(faixas) + '</div>') : '';
        const anosEl = document.getElementById('timingAnos');
        if (anosEl) anosEl.innerHTML = renderAnos(anos);
      }}

      const heroTitle = document.getElementById('heroTitle');
      const heroLead = document.getElementById('heroLead');
      const heroChips = document.getElementById('heroChips');
      if (isPortfolio) {{
        if (heroTitle) heroTitle.textContent = HERO.title || '';
        if (heroLead) heroLead.textContent = HERO.lead || '';
        if (heroChips) heroChips.innerHTML = HERO.chipsHtml || '';
      }} else {{
        const wf = activeWorkflow();
        const wfs = activeWorkflows();
        if (heroTitle) heroTitle.textContent = view.nome_curto + ' · visão individual';
        if (heroLead) {{
          heroLead.textContent = wfs.length > 1
            ? ('Recorte de ' + wfs.length + ' workflows — KPIs, evolução mensal e diagnóstico agregados.')
            : (wf
              ? ('Recorte do workflow selecionado — KPIs e evolução mensal recalculados para este fluxo.')
              : ('Recorte do cliente no portfólio — selecione um ou mais workflows abaixo para refinar a leitura.'));
        }}
        if (heroChips) heroChips.innerHTML =
          '<span class="chip">Taxa ' + fmtPct(view.taxa_confirmada_pct) + '</span>'
          + '<span class="chip">' + fmtInt(confirmadas) + ' confirmadas</span>'
          + '<span class="chip">' + fmtInt(view.achados_fg) + ' achados</span>'
          + '<span class="chip">' + fmtInt(view.contestacoes) + ' contestações</span>'
          + (wf && wf.id_workflow ? ('<span class="chip">WF ' + escHtml(String(wf.id_workflow)) + '</span>') : '')
          + (wfs.length > 1 ? ('<span class="chip">' + wfs.length + ' WFs</span>') : '');
      }}

      setText('signalRate', fmtPct(view.taxa_confirmada_pct));
      setText('signalDecisions', fmtInt(confirmadas) + ' de ' + fmtInt(decididas) + ' decisões');
      const spark = document.getElementById('signalSpark');
      if (spark) {{
        spark.innerHTML = isPortfolio
          ? (HERO.sparkHtml || '')
          : ('<i style="height:' + (confirmadas ? 100 : 12) + '%"></i>');
      }}

      syncMonthScopeUi();
      syncWorkflowPicker();
      renderStoryActions(isPortfolio, activeClient(), view);
      renderMonthly();
      renderDiagnostics();
      const eventsEl = document.getElementById('diagnosticsEvents');
      if (eventsEl) eventsEl.hidden = !isPortfolio;
      updateExportBar();
    }}

    function exportParams(slug) {{
      const params = new URLSearchParams();
      params.set('client', slug);
      if (PORTFOLIO.inicio) params.set('date_from', PORTFOLIO.inicio);
      if (PORTFOLIO.fim) params.set('date_to', PORTFOLIO.fim);
      if (PORTFOLIO.supplementKey) params.set('supplement_key', PORTFOLIO.supplementKey);
      const wf = activeWorkflow();
      if (wf && wf.id_workflow) params.set('id_workflow', String(wf.id_workflow));
      return params;
    }}

    function workflowVolumeLabel(wf) {{
      return fmtInt(
        Number(wf.falhas_confirmadas || 0)
        + Number(wf.achados_fg || 0)
        + Number(wf.contestacoes || 0)
        + Number(wf.auditados || 0)
      );
    }}

    function syncWorkflowPicker() {{
      const wrap = document.getElementById('workflowWrap');
      const list = document.getElementById('workflowList');
      const note = document.getElementById('workflowNote');
      const filterInput = document.getElementById('workflowFilter');
      const client = activeClient();
      if (!wrap || !list) return;
      if (!client || PORTFOLIO.standaloneClientSlug) {{
        wrap.hidden = true;
        state.workflowKeys = [];
        return;
      }}
      const workflows = client.workflows || [];
      if (!workflows.length) {{
        wrap.hidden = true;
        state.workflowKeys = [];
        return;
      }}
      wrap.hidden = false;
      const summaryMeta = document.getElementById('workflowSummaryMeta');
      const selectedCount = state.workflowKeys.length;
      const totalWf = workflows.length;
      if (summaryMeta) {{
        summaryMeta.textContent = selectedCount
          ? (selectedCount + ' selecionado(s) · ' + totalWf + ' disponíveis')
          : (totalWf + ' disponíveis · visão consolidada');
      }}
      const q = String(state.workflowFilter || filterInput?.value || '').trim().toLowerCase();
      const selected = new Set(state.workflowKeys);
      const visible = workflows.filter(wf => {{
        if (!q) return true;
        const hay = ((wf.nome || '') + ' ' + (wf.id_workflow || '') + ' ' + (wf.key || '')).toLowerCase();
        return hay.includes(q);
      }});
      list.innerHTML = visible.map(wf => {{
        const checked = selected.has(wf.key) ? ' checked' : '';
        const muted = wf.sem_carga_periodo ? ' muted' : '';
        const idLabel = wf.id_workflow ? ('ID ' + wf.id_workflow + ' · ') : '';
        const emptyLabel = wf.sem_carga_periodo ? ' · sem carga no período' : '';
        return '<label class="exec-workflow-option' + muted + '">'
          + '<input type="checkbox" value="' + escHtml(wf.key) + '"' + checked + '>'
          + '<span><b>' + escHtml(wf.nome || 'Workflow') + '</b>'
          + '<span class="meta">' + escHtml(idLabel) + 'volume ' + workflowVolumeLabel(wf) + emptyLabel + '</span></span>'
          + '</label>';
      }}).join('') || '<p class="exec-workflow-note">Nenhum workflow corresponde ao filtro.</p>';
      list.querySelectorAll('input[type=checkbox]').forEach(input => {{
        input.addEventListener('change', () => {{
          const key = input.value;
          if (input.checked) {{
            if (!state.workflowKeys.includes(key)) state.workflowKeys.push(key);
          }} else {{
            state.workflowKeys = state.workflowKeys.filter(item => item !== key);
          }}
          if (note) {{
            note.textContent = state.workflowKeys.length
              ? (state.workflowKeys.length + ' workflow(s) selecionado(s) · KPIs e gráficos agregados.')
              : 'Nenhum selecionado = visão consolidada do cliente.';
          }}
          applyClientScope();
        }});
      }});
      if (note) {{
        note.textContent = state.workflowKeys.length
          ? (state.workflowKeys.length + ' workflow(s) selecionado(s) · KPIs e gráficos agregados.')
          : 'Nenhum selecionado = visão consolidada do cliente.';
      }}
    }}

    function updateExportBar() {{
      const bar = document.getElementById('exportBar');
      const htmlLink = document.getElementById('exportClientHtml');
      const rcaLink = document.getElementById('exportClientRca');
      if (!bar || PORTFOLIO.standaloneClientSlug) return;
      const client = activeClient();
      const base = String(PORTFOLIO.exportApiBase || '').replace(/\\/$/, '');
      if (!client || !base) {{
        bar.hidden = true;
        return;
      }}
      const params = exportParams(client.slug);
      bar.hidden = false;
      if (htmlLink) htmlLink.href = base + '/executive-portfolio/client/?' + params.toString();
      if (rcaLink) rcaLink.href = base + '/executive-portfolio/client/rca/?' + params.toString();
    }}

    function selectClient(slug, label) {{
      state.clientSlug = slug || '';
      state.workflowKeys = [];
      state.workflowFilter = '';
      const filterInput = document.getElementById('workflowFilter');
      if (filterInput) filterInput.value = '';
      const input = document.getElementById('clientSearch');
      if (input) input.value = label || '';
      closeClientDropdown();
      syncWorkflowPicker();
      applyClientScope();
    }}

    function closeClientDropdown() {{
      const dropdown = document.getElementById('clientDropdown');
      const input = document.getElementById('clientSearch');
      if (dropdown) dropdown.hidden = true;
      if (input) input.setAttribute('aria-expanded', 'false');
    }}

    function openClientDropdown() {{
      const dropdown = document.getElementById('clientDropdown');
      const input = document.getElementById('clientSearch');
      if (dropdown) dropdown.hidden = false;
      if (input) input.setAttribute('aria-expanded', 'true');
    }}

    function portfolioDefaultLabel() {{
      return 'Portfólio completo (' + fmtInt(PORTFOLIO.clientCount || 0) + ' clientes)';
    }}

    function isPortfolioSelection(text) {{
      const value = (text || '').trim().toLowerCase();
      return !value || value.startsWith('portfólio completo') || value.startsWith('portfolio completo');
    }}

    function clientFilterQuery(text) {{
      if (isPortfolioSelection(text)) return '';
      return (text || '').trim().toLowerCase();
    }}

    function renderClientDropdown(query) {{
      const dropdown = document.getElementById('clientDropdown');
      if (!dropdown) return;
      const q = clientFilterQuery(query);
      const portfolioLabel = portfolioDefaultLabel();
      const items = [];
      if (!q) {{
        items.push({{ slug: '', label: portfolioLabel, meta: 'Visão consolidada multi-cliente' }});
      }}
      CLIENTS.forEach(client => {{
        const name = String(client.nome_curto || client.slug || '');
        const hay = (name + ' ' + (client.slug || '')).toLowerCase();
        if (!q || hay.includes(q)) {{
          items.push({{
            slug: client.slug,
            label: name,
            meta: fmtInt(client.auditados) + ' auditados · '
              + fmtInt(client.falhas_confirmadas) + ' confirmadas · '
              + fmtInt(client.achados_fg) + ' achados',
          }});
        }}
      }});
      if (!items.length) {{
        dropdown.innerHTML = '<button type="button" class="exec-client-option" disabled>Nenhum cliente encontrado</button>';
        openClientDropdown();
        return;
      }}
      const clientItems = items.filter(item => item.slug);
      const totalClients = CLIENTS.length;
      let footer = '';
      if (q) {{
        footer = clientItems.length + ' de ' + totalClients + ' clientes correspondem à busca';
      }} else {{
        footer = totalClients + ' clientes · role a lista para ver todos · digite para filtrar';
      }}
      dropdown.innerHTML = items.map((item, index) => (
        '<button type="button" class="exec-client-option' + (item.slug ? '' : ' portfolio') + (index === 0 ? ' active' : '') + '" data-slug="'
        + escHtml(item.slug) + '"><span>' + escHtml(item.label) + '</span><small>' + escHtml(item.meta) + '</small></button>'
      )).join('') + '<div class="exec-client-dropdown-footer">' + escHtml(footer) + '</div>';
      dropdown.querySelectorAll('.exec-client-option[data-slug]').forEach(btn => {{
        btn.addEventListener('mousedown', e => {{
          e.preventDefault();
          selectClient(btn.getAttribute('data-slug') || '', btn.querySelector('span')?.textContent || '');
        }});
      }});
      openClientDropdown();
    }}

    function setupClientSearch() {{
      const input = document.getElementById('clientSearch');
      const wrap = document.getElementById('clientSearchWrap');
      if (!input) return;
      input.value = portfolioDefaultLabel();
      input.addEventListener('focus', () => {{
        if (isPortfolioSelection(input.value)) input.select();
        renderClientDropdown('');
      }});
      input.addEventListener('input', () => renderClientDropdown(input.value));
      input.addEventListener('keydown', e => {{
        const dropdown = document.getElementById('clientDropdown');
        const options = dropdown ? [...dropdown.querySelectorAll('.exec-client-option[data-slug]:not([disabled])')] : [];
        let activeIndex = options.findIndex(opt => opt.classList.contains('active'));
        if (e.key === 'ArrowDown') {{
          e.preventDefault();
          if (activeIndex < options.length - 1) {{
            options[activeIndex]?.classList.remove('active');
            options[activeIndex + 1]?.classList.add('active');
          }}
          openClientDropdown();
        }} else if (e.key === 'ArrowUp') {{
          e.preventDefault();
          if (activeIndex > 0) {{
            options[activeIndex]?.classList.remove('active');
            options[activeIndex - 1]?.classList.add('active');
          }}
        }} else if (e.key === 'Enter') {{
          e.preventDefault();
          const active = options[activeIndex >= 0 ? activeIndex : 0];
          if (active) selectClient(active.getAttribute('data-slug') || '', active.querySelector('span')?.textContent || '');
        }} else if (e.key === 'Escape') {{
          closeClientDropdown();
        }}
      }});
      document.addEventListener('click', e => {{
        if (wrap && !wrap.contains(e.target)) closeClientDropdown();
      }});
    }}

    function syncMonthScopeUi() {{
      const sel = document.getElementById('monthScope');
      const wrap = document.getElementById('monthScopeWrap');
      const pill = document.getElementById('monthScopeClientPill');
      const client = activeClient();
      if (client) {{
        if (wrap) wrap.hidden = true;
        if (pill) {{
          pill.hidden = false;
          pill.textContent = client.nome_curto || client.slug || 'Cliente selecionado';
        }}
      }} else {{
        if (wrap) wrap.hidden = false;
        if (pill) pill.hidden = true;
        if (sel) {{
          sel.querySelectorAll('option[value="top10"], option[value="top3"]').forEach(opt => {{
            opt.hidden = false;
            opt.disabled = false;
          }});
        }}
      }}
      if (sel && !client) {{
        if (state.scope !== 'portfolio' && state.scope !== 'top10' && state.scope !== 'top3') {{
          state.scope = 'portfolio';
        }}
        sel.value = state.scope;
      }}
    }}

    function scopedMetricMap(metricKey) {{
      const workflows = activeWorkflows();
      const client = activeClient();
      const field = MONTHLY_FIELDS[metricKey] || MONTHLY_FIELDS.confirmadas;
      if (workflows.length) {{
        return mergeMonthlyMaps(workflows, field);
      }}
      if (client) {{
        return client[field] || {{}};
      }}
      const metrics = MONTHLY.metrics || {{}};
      const scoped = metrics[metricKey] || {{}};
      const scope = state.scope in scoped ? state.scope : 'portfolio';
      return scoped[scope] || MONTHLY[scope] || {{}};
    }}

    function metricMap() {{
      if (state.metric === 'comparativo') {{
        return {{}};
      }}
      const key = state.metric in (MONTHLY.metrics || {{}}) ? state.metric : 'confirmadas';
      return scopedMetricMap(key);
    }}

    function buildMonthlyRows() {{
      const months = MONTHLY.months || [];
      const achados = scopedMetricMap('achados');
      const contestacoes = scopedMetricMap('contestacoes');
      const confirmadas = scopedMetricMap('confirmadas');
      return months.map(ym => {{
        const ach = Number(achados[ym] || 0);
        const cont = Number(contestacoes[ym] || 0);
        const conf = Number(confirmadas[ym] || 0);
        const taxa = cont > 0 ? (conf / cont) * 100 : null;
        return {{ ym, achados: ach, contestacoes: cont, confirmadas: conf, taxa }};
      }});
    }}

    function scopeSeries() {{
      const months = MONTHLY.months || [];
      if (state.metric === 'comparativo') {{
        const achados = scopedMetricMap('achados');
        const confirmadas = scopedMetricMap('confirmadas');
        const contestacoes = scopedMetricMap('contestacoes');
        return months.map(ym => ({{
          ym,
          achados: Number(achados[ym] || 0),
          confirmadas: Number(confirmadas[ym] || 0),
          contestacoes: Number(contestacoes[ym] || 0),
          value: Math.max(Number(achados[ym] || 0), Number(confirmadas[ym] || 0), Number(contestacoes[ym] || 0)),
        }}));
      }}
      const base = metricMap();
      return months.map(ym => ({{ ym, value: Number(base[ym] || 0) }}));
    }}

    function monthDelta(current, previous) {{
      if (!current && !previous) return {{ cls: 'flat', label: '—' }};
      if (!previous) return {{ cls: 'up', label: current ? 'novo' : '—' }};
      const diff = current - previous;
      if (diff === 0) return {{ cls: 'flat', label: '0%' }};
      const pct = Math.abs((diff / previous) * 100);
      return {{
        cls: diff > 0 ? 'up' : 'down',
        label: (diff > 0 ? '+' : '−') + pct.toFixed(0) + '%',
      }};
    }}

    function renderMonthlyKpis(rows) {{
      const wrap = document.getElementById('monthlyKpis');
      if (!wrap) return;
      if (!rows.length) {{
        wrap.innerHTML = '';
        return;
      }}
      const partial = PORTFOLIO.partialMonth;
      const activeRows = partial ? rows.filter(r => r.ym !== partial) : rows;
      const sourceRows = activeRows.length ? activeRows : rows;
      let total = 0;
      let peakRow = rows[0];
      let peakValue = 0;
      if (state.metric === 'comparativo') {{
        total = sourceRows.reduce((acc, row) => acc + row.achados + row.confirmadas, 0);
        rows.forEach(row => {{
          const mix = row.achados + row.confirmadas;
          if (mix >= peakValue) {{
            peakValue = mix;
            peakRow = row;
          }}
        }});
      }} else {{
        const key = state.metric === 'achados' ? 'achados'
          : state.metric === 'contestacoes' ? 'contestacoes' : 'confirmadas';
        total = sourceRows.reduce((acc, row) => acc + Number(row[key] || 0), 0);
        rows.forEach(row => {{
          const value = Number(row[key] || 0);
          if (value >= peakValue) {{
            peakValue = value;
            peakRow = row;
          }}
        }});
      }}
      const monthsWithData = sourceRows.filter(row => row.achados || row.contestacoes || row.confirmadas).length || rows.length;
      const avg = monthsWithData ? Math.round(total / monthsWithData) : 0;
      const last = rows[rows.length - 1];
      const prev = rows.length > 1 ? rows[rows.length - 2] : null;
      let trendValue = 0;
      let trendPrev = 0;
      if (state.metric === 'comparativo') {{
        trendValue = last.achados + last.confirmadas;
        trendPrev = prev ? prev.achados + prev.confirmadas : 0;
      }} else {{
        const key = state.metric === 'achados' ? 'achados'
          : state.metric === 'contestacoes' ? 'contestacoes' : 'confirmadas';
        trendValue = Number(last[key] || 0);
        trendPrev = prev ? Number(prev[key] || 0) : 0;
      }}
      const trend = monthDelta(trendValue, trendPrev);
      wrap.innerHTML =
        '<article class="monthly-kpi"><b>Total no período</b><strong>' + fmtInt(total) + '</strong>'
        + '<small>' + (METRIC_LABELS[state.metric] || 'Indicador') + '</small></article>'
        + '<article class="monthly-kpi"><b>Pico mensal</b><strong>' + fmtInt(peakValue) + '</strong>'
        + '<small>' + monthLabel(peakRow.ym) + (peakRow.ym === partial ? ' · parcial' : '') + '</small></article>'
        + '<article class="monthly-kpi"><b>Média mensal</b><strong>' + fmtInt(avg) + '</strong>'
        + '<small>com base em ' + fmtInt(monthsWithData) + ' mês(es) com movimento</small></article>'
        + '<article class="monthly-kpi trend-' + trend.cls + '"><b>Último vs anterior</b><strong>' + escHtml(trend.label) + '</strong>'
        + '<small>' + monthLabel(last.ym) + ' frente a ' + (prev ? monthLabel(prev.ym) : '—') + '</small></article>';
    }}

    function renderMonthlyActionPlan() {{
      const box = document.getElementById('monthlyActionPlan');
      if (!box) return;
      const client = activeClient();
      const view = activeMetrics();
      const hoursMap = client ? (client.monthly_treinamentos_horas || {{}}) : ((MONTHLY.metrics || {{}}).treinamentos_horas?.[state.scope] || {{}});
      const sessionsMap = client ? (client.monthly_treinamentos_sessoes || {{}}) : ((MONTHLY.metrics || {{}}).treinamentos_sessoes?.[state.scope] || {{}});
      // No portfólio, use o total consolidado (corporativo deduplicado). Somar
      // os mapas mensais dos clientes repete o mesmo treinamento corporativo.
      const hours = client ? Number(view.treinamentos_horas || 0) : Number(TOTALS.treinamentos_horas || 0);
      const sessions = client ? Number(view.treinamentos_sessoes || 0) : Number(TOTALS.treinamentos_sessoes || 0);
      const months = Object.keys(hoursMap).filter(ym => Number(hoursMap[ym] || 0) > 0);
      const title = document.getElementById('monthlyActionTitle');
      const text = document.getElementById('monthlyActionText');
      if (title) title.textContent = hours || sessions ? 'Capacitação no período' : 'Sem capacitação registrada no período';
      if (text) text.textContent = hours || sessions
        ? ('Ao longo de ' + fmtInt(months.length || 1) + ' mês(es), foram realizadas ' + fmtInt(sessions) + ' sessões, totalizando ' + fmtHours(hours) + '. Esse volume de capacitação compõe a leitura de risco; acompanhe os meses seguintes para verificar como evoluem os principais achados identificados.')
        : 'Não há sessões com horas no recorte. A evolução dos achados deve ser interpretada sem esse contexto de capacitação.';
      const set = (id, value) => {{ const el = document.getElementById(id); if (el) el.textContent = value; }};
      set('monthlyTrainingHours', fmtHours(hours));
      set('monthlyTrainingSessions', fmtInt(sessions));
      set('monthlyTrainingAverage', fmtHours(sessions ? hours / sessions : 0));
    }}

    function renderMonthlyLegend() {{
      const legend = document.getElementById('monthlyLegend');
      if (!legend) return;
      if (state.metric === 'comparativo') {{
        legend.innerHTML =
          '<span class="audit"><i></i>Achados da auditoria</span>'
          + '<span><i></i>Falhas confirmadas</span>'
          + '<span class="training"><i></i>Horas de capacitação (contexto de risco)</span>'
          + '<span class="empty"><i></i>Sem movimento no mês</span>';
        return;
      }}
      if (state.metric === 'achados') {{
        legend.innerHTML =
          '<span class="audit"><i></i>Achados da auditoria por mês</span>'
          + '<span class="empty"><i></i>Sem achado no mês</span>';
        return;
      }}
      if (state.metric === 'contestacoes') {{
        legend.innerHTML =
          '<span class="contest"><i></i>Contestações recebidas por mês</span>'
          + '<span class="empty"><i></i>Sem contestação no mês</span>';
        return;
      }}
      legend.innerHTML =
        '<span><i></i>Falhas confirmadas por mês</span>'
        + '<span class="empty"><i></i>Sem confirmação no mês</span>';
    }}

    function renderMonthlyInsight(rows) {{
      const insight = document.getElementById('monthlyInsight');
      if (!insight) return;
      const partial = PORTFOLIO.partialMonth;
      const activeRows = rows.filter(row => row.ym !== partial || rows.length === 1);
      if (!activeRows.length) {{
        insight.textContent = 'Sem movimento datado suficiente para leitura comparativa no recorte.';
        return;
      }}
      const auditWins = activeRows.filter(row => row.achados > row.confirmadas).length;
      const confirmWins = activeRows.filter(row => row.confirmadas > row.achados).length;
      const silent = activeRows.filter(row => !row.contestacoes && !row.confirmadas).length;
      if (state.metric === 'comparativo') {{
        insight.innerHTML = '<b>Leitura:</b> em <b>' + fmtInt(auditWins) + '</b> de '
          + fmtInt(activeRows.length) + ' mês(es) os achados da auditoria superaram as confirmações do cliente'
          + (confirmWins ? ' · em <b>' + fmtInt(confirmWins) + '</b> mês(es) houve mais confirmações que achados' : '')
          + (silent ? ' · <b>' + fmtInt(silent) + '</b> mês(es) sem contestação datada' : '')
          + '. Isso ajuda a separar pressão interna (auditoria) de pressão percebida pelo cliente (contestação).';
        return;
      }}
      const key = state.metric === 'achados' ? 'achados'
        : state.metric === 'contestacoes' ? 'contestacoes' : 'confirmadas';
      const values = activeRows.map(row => Number(row[key] || 0));
      const peak = Math.max(...values, 0);
      const peakMonth = activeRows.find(row => Number(row[key] || 0) === peak);
      insight.innerHTML = '<b>Leitura:</b> pico de <b>' + fmtInt(peak) + '</b> '
        + (METRIC_LABELS[state.metric] || 'registros') + ' em <b>' + monthLabel(peakMonth?.ym || '') + '</b>'
        + (auditWins ? ' · achados superaram confirmações em ' + fmtInt(auditWins) + ' mês(es)' : '')
        + '.';
    }}

    function renderMonthlyTable(rows) {{
      const body = document.getElementById('monthlyTableBody');
      if (!body) return;
      const partial = PORTFOLIO.partialMonth;
      const peakConfirm = Math.max(...rows.map(row => row.confirmadas), 0);
      body.innerHTML = rows.map(row => {{
        const classes = [];
        if (row.ym === partial) classes.push('partial');
        if (row.confirmadas === peakConfirm && peakConfirm > 0) classes.push('peak');
        const taxa = row.taxa == null ? '—' : fmtPct(row.taxa);
        return '<tr class="' + classes.join(' ') + '"><td>' + monthLabel(row.ym)
          + (row.ym === partial ? ' *' : '') + '</td>'
          + '<td>' + (row.achados ? fmtInt(row.achados) : '<span class="num-muted">—</span>') + '</td>'
          + '<td>' + (row.contestacoes ? fmtInt(row.contestacoes) : '<span class="num-muted">—</span>') + '</td>'
          + '<td>' + (row.confirmadas ? fmtInt(row.confirmadas) : '<span class="num-muted">—</span>') + '</td>'
          + '<td>' + taxa + '</td></tr>';
      }}).join('');
    }}

    function renderMonthly() {{
      const note = document.getElementById('monthlyNote');
      const bars = document.getElementById('monthlyBars');
      if (!bars || !note) return;

      const months = MONTHLY.months || [];
      const rows = buildMonthlyRows();
      const series = scopeSeries();
      const partial = PORTFOLIO.partialMonth;
      const client = activeClient();
      const scopeLabel = client ? ('cliente ' + (client.nome_curto || '')) : 'portfólio consolidado';
      const isComparativo = state.metric === 'comparativo';
      const isAudit = state.metric === 'achados';
      const isContest = state.metric === 'contestacoes';
      const trainingHoursMap = activeClient()
        ? (activeClient().monthly_treinamentos_horas || {{}})
        : ((MONTHLY.metrics || {{}}).treinamentos_horas?.[state.scope] || {{}});
      const peak = Math.max(1, ...series.map(r => Number(r.value || 0)));

      renderMonthlyKpis(rows);
      renderMonthlyActionPlan();
      renderMonthlyLegend();
      renderMonthlyInsight(rows);
      renderMonthlyTable(rows);

      if (partial) {{
        note.innerHTML = '<div class="monthly-note"><b>Mês parcial:</b> '
          + monthLabel(partial) + ' reflete apenas os dias incluídos no recorte — use a tabela abaixo com cautela.</div>';
      }} else if (!PORTFOLIO.hasMonthlyData && !client) {{
        note.innerHTML = '<div class="monthly-note"><b>Sem movimento datado</b> no recorte para este indicador.</div>';
      }} else {{
        note.innerHTML = '<div class="monthly-note">'
          + (METRIC_LABELS[state.metric] || 'Indicador') + ' do ' + scopeLabel + ' · dados observados.</div>';
      }}

      bars.style.setProperty('--n-months', months.length || 1);
      bars.innerHTML = series.map((row, index) => {{
        const previous = index > 0 ? series[index - 1] : null;
        if (isComparativo) {{
          const ach = row.achados;
          const conf = row.confirmadas;
          const empty = !ach && !conf;
          const achH = ach ? Math.max(8, Math.round(180 * ach / peak)) : 8;
          const confH = conf ? Math.max(8, Math.round(180 * conf / peak)) : 8;
          const delta = monthDelta(ach + conf, previous ? previous.achados + previous.confirmadas : 0);
          const classes = ['month-col'];
          if (empty) classes.push('empty');
          else if (row.ym === partial) classes.push('partial');
          const display = empty ? '—' : (fmtInt(ach) + ' / ' + fmtInt(conf));
          const title = 'Achados: ' + fmtInt(ach) + ' · Confirmadas: ' + fmtInt(conf)
            + (row.contestacoes ? ' · Contestações: ' + fmtInt(row.contestacoes) : '')
            + (trainingHoursMap[row.ym] ? ' · Capacitação: ' + fmtHours(trainingHoursMap[row.ym]) : '');
          return '<div class="' + classes.join(' ') + '" title="' + escHtml(title) + '">'
            + '<div class="month-col-group">'
            + '<i class="audit" style="height:' + achH + 'px"></i>'
            + '<i class="confirm" style="height:' + confH + 'px"></i></div>'
            + '<strong>' + display + '</strong>'
            + '<span class="month-delta ' + delta.cls + '">' + escHtml(delta.label) + '</span>'
            + '<small>' + monthLabel(row.ym) + '</small>'
            + (trainingHoursMap[row.ym] ? '<em class="training-mark">' + fmtHours(trainingHoursMap[row.ym]) + '</em>' : '')
            + '</div>';
        }}

        const value = Number(row.value || 0);
        const height = value ? Math.max(8, Math.round(180 * value / peak)) : 8;
        const classes = ['month-col'];
        if (value === 0) classes.push('empty');
        else if (isAudit) classes.push('audit');
        else if (isContest) classes.push('contest');
        else if (row.ym === partial) classes.push('partial');
        const prevValue = previous ? Number(previous.value || 0) : 0;
        const delta = monthDelta(value, prevValue);
        const display = value ? fmtInt(value) : '—';
        return '<div class="' + classes.join(' ') + '"><i style="height:' + height + 'px"></i>'
          + '<strong>' + display + '</strong>'
          + '<span class="month-delta ' + delta.cls + '">' + escHtml(delta.label) + '</span>'
          + '<small>' + monthLabel(row.ym) + '</small>'
          + (trainingHoursMap[row.ym] ? '<em class="training-mark">' + fmtHours(trainingHoursMap[row.ym]) + '</em>' : '')
          + '</div>';
      }}).join('');
    }}

    function monthLabel(ym) {{
      const part = (ym || '').split('-')[1];
      return MONTH_ABBR[String(Number(part))] || ym;
    }}

    function fmtInt(n) {{
      return Number(n || 0).toLocaleString('pt-BR');
    }}

    function fmtPct(n) {{
      return Number(n || 0).toFixed(1).replace('.', ',') + '%';
    }}

    function fmtHours(n) {{
      return Number(n || 0).toFixed(1).replace('.', ',') + ' h';
    }}

    function escHtml(value) {{
      return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
    }}

    function renderStoryCompact(beats) {{
      return '<div class="exec-story-compact">' + beats.map((beat, index) => (
        '<article><div class="step">' + (index + 1) + '</div><div><b>'
        + escHtml(beat.title) + '</b><p>' + escHtml(beat.body) + '</p></div></article>'
      )).join('') + '</div>';
    }}

    function buildClientStory(client, view) {{
      const nome = view.nome_curto || client.slug || 'Cliente';
      const achados = view.achados_fg;
      const confirmadas = view.falhas_confirmadas;
      const contest = view.contestacoes;
      const taxa = view.taxa_confirmada_pct;
      const auditados = view.auditados;
      const mediana = Number(view.timing?.mediana_dias || 0);
      const share = Number(client.share_confirmadas_pct || 0);
      let pressao = 'taxa controlada no recorte';
      if (confirmadas === 0 && contest === 0) pressao = 'sem contestação no período — validar se é silêncio ou ausência de carga';
      else if (taxa >= 10) pressao = 'pressão elevada nas confirmações';
      else if (taxa >= 5) pressao = 'pressão moderada nas confirmações';
      else if (confirmadas > 0) pressao = 'confirmações presentes, taxa relativamente controlada';

      let proximo = 'Manter monitoramento de achados e contestações no próximo fechamento.';
      if (achados > confirmadas * 3 && achados > 20) {{
        proximo = 'Priorizar redução de achados na auditoria antes de esperar contestação do cliente.';
      }} else if (confirmadas > 0) {{
        proximo = 'Investigar motivos das confirmações com Operação/Qualidade e validar recorrência.';
      }} else if (contest === 0) {{
        proximo = 'Confirmar se a ausência de contestação reflete qualidade ou falta de movimento no recorte.';
      }}

      return [
        {{
          title: 'Volume analisado',
          body: fmtInt(auditados) + ' protocolos distintos · ' + fmtInt(view.auditados_registros) + ' registros · ' + fmtInt(achados) + ' achados na auditoria.',
        }},
        {{
          title: 'Resultado das contestações',
          body: fmtInt(achados) + ' achados internos versus ' + fmtInt(confirmadas) + ' falhas confirmadas pelo cliente (' + fmtInt(contest) + ' contestações recebidas).',
        }},
        {{
          title: 'Pressão percebida',
          body: 'Taxa confirmada ' + fmtPct(taxa) + ' · ' + pressao
            + (share && confirmadas ? ' · ' + fmtPct(share) + ' do total de confirmações do portfólio.' : '.'),
        }},
        {{
          title: 'Tempo e próximo passo',
          body: (mediana ? ('Mediana de ' + medianaLabel(mediana) + ' até a contestação. ') : '')
            + proximo,
        }},
      ];
    }}

    function buildClientStoryModern(client, view) {{
      const beats = buildClientStory(client, view);
      const titles = ['Volume analisado', 'Resultado das contesta\\u00e7\\u00f5es', 'Leitura do per\\u00edodo', 'Pr\\u00f3ximo acompanhamento'];
      return beats.map((beat, index) => ({{ ...beat, title: titles[index] || beat.title }}));
    }}

    function renderStoryActions(isPortfolio, client, view) {{
      const storyEl = document.getElementById('storyContent');
      const storyTitle = document.getElementById('storyBoxTitle');
      const sectionLead = document.getElementById('execSectionLead');
      const timeline = document.getElementById('timelineWrap');
      if (isPortfolio) {{
        if (storyTitle) storyTitle.textContent = HERO.storyTitle || 'A história em quatro pontos';
        if (sectionLead) sectionLead.textContent = HERO.sectionLead || '';
        if (storyEl) storyEl.innerHTML = HERO.storyHtml || '';
        if (timeline) timeline.style.display = '';
      }} else if (client) {{
        if (storyTitle) storyTitle.textContent = 'Leitura · ' + (view.nome_curto || 'cliente');
        if (sectionLead) sectionLead.textContent = 'Resumo recalculado para o cliente selecionado — alinhado aos KPIs acima.';
        if (storyEl) storyEl.innerHTML = renderStoryCompact(buildClientStoryModern(client, view));
        if (timeline) timeline.style.display = 'none';
      }}
    }}

    const buttons = [...document.querySelectorAll('.exec-nav button')];
    const pages = [...document.querySelectorAll('.exec-page')];
    buttons.forEach(btn => btn.addEventListener('click', () => {{
      buttons.forEach(x => x.classList.remove('active'));
      btn.classList.add('active');
      pages.forEach(p => p.classList.toggle('active', p.id === btn.dataset.page));
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }}));

    document.getElementById('monthScope')?.addEventListener('change', e => {{
      state.scope = e.target.value;
      renderMonthly();
    }});

    document.getElementById('monthMetric')?.addEventListener('change', e => {{
      state.metric = e.target.value;
      renderMonthly();
    }});

    document.getElementById('workflowFilter')?.addEventListener('input', e => {{
      state.workflowFilter = e.target.value || '';
      syncWorkflowPicker();
    }});

    document.getElementById('workflowSelectAll')?.addEventListener('click', () => {{
      const client = activeClient();
      if (!client) return;
      const q = String(state.workflowFilter || '').trim().toLowerCase();
      state.workflowKeys = (client.workflows || [])
        .filter(wf => {{
          if (!q) return true;
          const hay = ((wf.nome || '') + ' ' + (wf.id_workflow || '') + ' ' + (wf.key || '')).toLowerCase();
          return hay.includes(q);
        }})
        .map(wf => wf.key);
      syncWorkflowPicker();
      applyClientScope();
    }});

    document.getElementById('workflowClearAll')?.addEventListener('click', () => {{
      state.workflowKeys = [];
      syncWorkflowPicker();
      applyClientScope();
    }});

    setupClientSearch();
    if (PORTFOLIO.standaloneClientSlug) {{
      const standalone = clientBySlug(PORTFOLIO.standaloneClientSlug);
      if (standalone) {{
        selectClient(standalone.slug, standalone.nome_curto || standalone.slug);
      }}
    }} else {{
      applyClientScope();
    }}
  </script>
</body>
</html>"""


def write_executive_portfolio_html(payload: dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_executive_portfolio_html(payload), encoding="utf-8")
    return path
