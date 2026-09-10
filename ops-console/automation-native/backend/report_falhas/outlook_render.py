# -*- coding: utf-8 -*-
"""Blocos HTML compatíveis com Outlook (rank delta, projeções, explicações)."""

from __future__ import annotations

import html as html_module

from report_falhas.io.data_loader import safe_str


def _esc(x, quote: bool = False) -> str:
    try:
        return html_module.escape(safe_str(x), quote=bool(quote))
    except Exception:
        return html_module.escape("" if x is None else str(x), quote=bool(quote))


def render_rank_delta_outlook(title: str, col_label: str, data: dict, comp_txt: str = "") -> str:
    """Tabela curta e simples (Outlook-safe) com comparação vs período equivalente."""
    if not data or (not data.get("impacto") and not data.get("crescimento")):
        return "<div style='font-size:12px;color:#6c757d;'>Sem dados para este recorte.</div>"

    missing = float(data.get("missing_pct", 0.0) or 0.0)

    def _pct(v):
        return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"

    def _delta_pct(v):
        if v is None:
            return "—"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.0f}%"

    def _tbl_impact(rows):
        if not rows:
            return "<div style='font-size:12px;color:#6c757d;'>Mais frequentes: sem dados.</div>"
        h = (
            "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Mais frequentes</div>"
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
            "<colgroup><col style='width:70%'><col style='width:15%'><col style='width:15%'></colgroup>"
            "<thead><tr style='background:#F3F4F6;'>"
            f"<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:left;'>{col_label}</th>"
            "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>Qtd</th>"
            "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>%</th>"
            "</tr></thead><tbody>"
        )
        for i, r in enumerate(rows):
            bg = "background:#FAFAFA;" if i % 2 == 1 else ""
            h += (
                "<tr>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}overflow-wrap:break-word;'>{safe_str(r.get('label'))}</td>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{int(r.get('cur',0))}</td>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{_pct(r.get('share',0.0))}</td>"
                "</tr>"
            )
        h += "</tbody></table>"
        return h

    def _tbl_growth(rows):
        if not rows:
            return "<div style='font-size:12px;color:#6c757d;'>Maior aumento: sem dados.</div>"
        h = (
            "<div style='font-weight:800;margin:10px 0 6px;font-size:12px;'>Maior aumento (comparado ao período equivalente)</div>"
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
            "<colgroup><col style='width:45%'><col style='width:14%'><col style='width:14%'><col style='width:13%'><col style='width:14%'></colgroup>"
            "<thead><tr style='background:#F3F4F6;'>"
            f"<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:left;'>{col_label}</th>"
            "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>Anterior</th>"
            "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>Atual</th>"
            "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>Δ</th>"
            "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>Δ%</th>"
            "</tr></thead><tbody>"
        )
        for i, r in enumerate(rows):
            bg = "background:#FAFAFA;" if i % 2 == 1 else ""
            prev = int(r.get("prev", 0))
            cur = int(r.get("cur", 0))
            delta = int(r.get("delta", 0))
            sign = "+" if delta >= 0 else ""
            h += (
                "<tr>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}overflow-wrap:break-word;'>{safe_str(r.get('label'))}</td>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{prev}</td>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{cur}</td>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{sign}{delta}</td>"
                f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{_delta_pct(r.get('delta_pct'))}</td>"
                "</tr>"
            )
        h += "</tbody></table>"
        return h

    comp_line = safe_str(comp_txt) if safe_str(comp_txt) else "Sem comparação disponível"

    return (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        f"<h3 style='margin:0 0 6px;'>{title}</h3>"
        f"<div style='font-size:12px;color:#6c757d;margin:0 0 10px;line-height:1.35;'>"
        f"Leitura rápida: <b>Mais frequentes</b> = onde mais ocorre. <b>Maior aumento</b> = o que piorou no comparativo.<br>"
        f"<b>Comparação:</b> {comp_line}<br>"
        f"Valores vazios em <b>{col_label}</b>: {missing:.1f}%."
        "</div>"
        + _tbl_impact(data.get("impacto") or [])
        + _tbl_growth(data.get("crescimento") or [])
        + "</div>"
    )


def render_projecoes_insuficientes_outlook(num_meses: int | None = None) -> str:
    """Tooltip compacto sobre dados insuficientes para projeções."""
    count_txt = (
        f"Histórico: {int(num_meses)}/3 meses"
        if (num_meses is not None)
        else "Histórico: menos de 3/3 meses"
    )
    body = (
        f"<div style='font-size:12px;color:#334155;line-height:1.45;'>"
        f"<div style='font-weight:600;margin-bottom:6px;'>{_esc(count_txt)}</div>"
        "As projeções não são exibidas neste momento porque ainda não há histórico mensal suficiente.<br>"
        "São necessários pelo menos <b>3 meses completos de dados</b> para que a tendência seja estatisticamente confiável e evitar interpretações incorretas."
        "</div>"
    )
    return (
        "<!--[if mso]>"
        "<div style='margin:6px 0 0;padding:6px 8px;background:#FFF7ED;border:1px solid #FDBA74;border-radius:8px;'>"
        "<div style='font-weight:400;'>ⓘ Projeção indisponível</div>"
        "<div style='margin-top:6px;'>"
        f"{body}"
        "</div>"
        "</div>"
        "<![endif]-->"
        "<!--[if !mso]><!-- -->"
        "<details style='margin:6px 0 0;padding:6px 8px;background:#FFF7ED;border:1px solid #FDBA74;border-radius:8px;'>"
        "<summary style='font-weight:400;margin:0;cursor:pointer;list-style:none;'>ⓘ Projeção indisponível</summary>"
        "<div style='margin-top:6px;'>"
        f"{body}"
        "</div>"
        "</details>"
        "<!--<![endif]-->"
    )


def render_como_ler_delta_outlook(comp_txt: str = "") -> str:
    """Bloco explicativo curto para Treinamento: como ler Anterior/Atual/Δ/Δ%."""
    comp = safe_str(comp_txt)
    if not comp:
        comp = "Sem comparação disponível"
    body = (
        f"<div style='font-size:12px;color:#334155;line-height:1.45;'>"
        "A comparação usa a <b>mesma quantidade de dias de calendário</b> no mês anterior "
        "(ex.: 01/05 a 10/05 no atual vs 01/04 a 10/04 no comparativo).<br>"
        "Se o período atual tem <b>0 falhas</b>, usamos o mês anterior inteiro e comparamos contra zero.<br>"
        "Isso vale para meses em andamento e meses fechados.<br>"
        "<b>Importante:</b> "
        "<b>Período e mês:</b> todas as métricas usam a <b>Data de Análise</b>. "
        "O protocolo entra no mês em que a análise ocorreu.<br>"
        "<b>Fechamento mensal:</b> ao fechar um mês (ex.: junho), a auditoria pode ser registrada até o "
        "<b>5º dia útil do mês seguinte</b> (ex.: 07/07/2026) e o caso continua no oficial daquele fechamento "
        "— desde que a análise seja do mês fechado.<br>"
        "<b>Fora de fase:</b> análise no mês fechado, mas auditoria <b>depois</b> do 5º dia útil seguinte, "
        "não entra no oficial (aparece na aba “Fora de Fase” no Excel).<br>"
        "<b>Exemplo:</b> análise 30/06 + auditoria 02/07 → oficial de junho. "
        "Análise 30/06 + auditoria 10/07 → fora de fase. Análise 07/07 → report de julho.<br>"
        f"<b>Comparação:</b> {comp}<br>"
        "<b>Δ</b> = Atual − Anterior (positivo = piorou; negativo = melhorou).<br>"
        "<b>Δ%</b> = variação percentual vs Anterior (mostra '—' quando Anterior = 0)."
        "</div>"
    )
    return (
        "<!--[if mso]>"
        "<div style='background:#EEF2FF;border:1px solid #D9E0FF;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        "<div style='font-weight:800;margin:0 0 6px;'>ⓘ Como ler este comparativo "
        "<span style='font-weight:400;font-size:12px;color:#6b7280;'>(clique para abrir)</span>"
        "</div>"
        "<div style='margin-top:8px;'>"
        f"{body}"
        "</div>"
        "</div>"
        "<![endif]-->"
        "<!--[if !mso]><!-- -->"
        "<details style='background:#EEF2FF;border:1px solid #D9E0FF;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        "<summary style='font-weight:800;margin:0;cursor:pointer;list-style:none;'>ⓘ Como ler este comparativo "
        "<span style='font-weight:400;font-size:12px;color:#6b7280;'>(clique para abrir)</span>"
        "</summary>"
        "<div style='margin-top:8px;'>"
        f"{body}"
        "</div>"
        "</details>"
        "<!--<![endif]-->"
    )
