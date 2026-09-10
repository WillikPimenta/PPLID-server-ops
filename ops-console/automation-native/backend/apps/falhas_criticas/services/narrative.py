# -*- coding: utf-8 -*-
"""Storytelling e pré-diagnóstico executivo para o portal."""
import re

from report_falhas.insights import compute_trend, trend_text

from apps.falhas_criticas.utils_metrics import pct

_TAG_RE = re.compile(r'<[^>]+>')


def _strip_html(text):
    """Remove tags HTML do texto legado (trend_text vem com <span>)."""
    if not text:
        return ''
    return _TAG_RE.sub('', str(text)).strip()


def pre_diagnostico_block(veredito='', bullets=None, tendencia=None, titulo='Leitura rápida'):
    return {
        'titulo': titulo,
        'veredito': veredito or '',
        'bullets': bullets or [],
        'tendencia': tendencia or '',
    }


def trend_from_series(values, unidade='por período'):
    vals = [v for v in (values or []) if v is not None]
    if len(vals) < 2:
        return ''
    slope, _ = compute_trend(vals)
    return _strip_html(trend_text(slope, unidade=unidade)) if slope is not None else ''


def build_support_narrative(
    kpis,
    top_agentes=None,
    top_workflows=None,
    duvidas_piora=None,
    periodo_mtd_label=None,
    top_agentes_volume=None,
):
    bullets = []
    total = kpis.get('total') or 0
    if total:
        bullets.append(
            f"Volume de {total} solicitações no período; NC oficial em {kpis.get('nc_oficial_pct', 0)}%."
        )
        if kpis.get('regra3_count'):
            bullets.append(
                f"{kpis['regra3_count']} casos críticos (Regra 3) — {kpis.get('regra3_pct', 0)}% do total."
            )
        delta = kpis.get('volume_delta_pct')
        if delta and delta != '—':
            bullets.append(f"Variação de volume vs MTD anterior: {delta}.")
        nc_delta = kpis.get('nc_delta_pct')
        if nc_delta and nc_delta != '—':
            bullets.append(f"NC oficial vs MTD anterior: {nc_delta}.")
        r3_delta = kpis.get('r3_delta_pct')
        if r3_delta and r3_delta != '—':
            bullets.append(f"Regra 3 vs MTD anterior: {r3_delta}.")
    piora = [d for d in (duvidas_piora or []) if d.get('delta', 0) > 0]
    if piora:
        ref = f" (vs {periodo_mtd_label})" if periodo_mtd_label else " (MTD anterior)"
        tops = ', '.join(
            f"«{d.get('texto', '')}» (+{d.get('delta', 0)})" for d in piora[:3]
        )
        bullets.append(
            f"{len(piora)} cenário(s) em alta{ref}: {tops}."
        )
    if top_agentes_volume:
        v = top_agentes_volume[0]
        label = v.get('nome') or v.get('matricula') or '—'
        bullets.append(
            f"Maior volume de acionamentos: {label} "
            f"({v.get('atual', 0)} solicitações"
            f"{', Δ +' + str(v.get('delta', 0)) + ' vs MTD anterior' if v.get('delta', 0) > 0 else ''})."
        )
    if top_agentes:
        a = top_agentes[0]
        bullets.append(
            f"Agente com maior exposição R3: {a.get('nome') or a.get('matricula')} "
            f"({a.get('r3', 0)} casos, {a.get('r3_pct', 0)}%)."
        )
    if top_workflows:
        w = top_workflows[0]
        bullets.append(f"Workflow mais frequente em R3: {w.get('workflow')} ({w.get('qtd', 0)} casos).")

    veredito = bullets[0] if bullets else 'Sem solicitações de suporte no período filtrado.'
    return pre_diagnostico_block(veredito=veredito, bullets=bullets[:5])


def build_comparativo_narrative(bsb: dict, sc: dict, drivers: list):
    bullets = []
    bf = bsb.get('falhas', {})
    sf = sc.get('falhas', {})
    b_total = bf.get('total') or 0
    s_total = sf.get('total') or 0

    if s_total == 0 and b_total > 0:
        veredito = (
            f"Brasília registra {b_total} falhas no período; São Carlos sem registros "
            f"(verifique importação ou grafia da localidade na planilha)."
        )
    elif b_total == 0 and s_total > 0:
        veredito = (
            f"São Carlos registra {s_total} falhas no período; Brasília sem registros "
            f"(verifique importação ou grafia da localidade na planilha)."
        )
    elif b_total > s_total and s_total:
        diff_pct = pct(b_total - s_total, s_total)
        veredito = (
            f"Brasília registra {b_total} falhas vs {s_total} em São Carlos "
            f"(+{diff_pct}% relativo a SC)."
        )
    elif s_total > b_total and b_total:
        diff_pct = pct(s_total - b_total, b_total)
        veredito = (
            f"São Carlos registra {s_total} falhas vs {b_total} em Brasília "
            f"(+{diff_pct}% relativo a BSB)."
        )
    elif b_total == s_total:
        veredito = f"Volumes iguais: Brasília {b_total} | São Carlos {s_total}."
    else:
        veredito = f"Brasília {b_total} | São Carlos {s_total}."

    if drivers:
        d = drivers[0]
        bq = int(d.get('bsb_qtd') or 0)
        sq = int(d.get('sc_qtd') or 0)
        delta = int(d.get('delta') or 0)
        label = d.get('label') or '—'
        if delta < 0:
            bullets.append(
                f'Maior diferença: {label} — BSB {bq} vs SC {sq}; '
                f'São Carlos registra {abs(delta)} falha(s) a mais.'
            )
        elif delta > 0:
            bullets.append(
                f'Maior diferença: {label} — BSB {bq} vs SC {sq}; '
                f'Brasília registra {delta} falha(s) a mais.'
            )
        else:
            bullets.append(f'Maior diferença: {label} — BSB {bq} vs SC {sq} (empate).')

        # Se o total pior é BSB mas a maior diferença favorece BSB, explicar o total.
        if b_total > s_total and delta < 0:
            elevam = [
                x for x in drivers
                if int(x.get('delta') or 0) > 0
            ][:3]
            if elevam:
                parts = [
                    f"{x.get('label')} (+{int(x.get('delta') or 0)})"
                    for x in elevam
                ]
                bullets.append(f'Itens que elevam Brasília no total: {", ".join(parts)}.')
        elif s_total > b_total and delta > 0:
            elevam = [
                x for x in drivers
                if int(x.get('delta') or 0) < 0
            ][:3]
            if elevam:
                parts = [
                    f"{x.get('label')} ({int(x.get('delta') or 0)})"
                    for x in elevam
                ]
                bullets.append(f'Itens que elevam São Carlos no total: {", ".join(parts)}.')

    b_r3 = bsb.get('suporte', {}).get('regra3_pct')
    s_r3 = sc.get('suporte', {}).get('regra3_pct')
    b_sup_total = int(bsb.get('suporte', {}).get('total') or 0)
    s_sup_total = int(sc.get('suporte', {}).get('total') or 0)
    if b_sup_total == 0 and s_sup_total == 0:
        bullets.append('NC suporte TEAMS: BSB sem dados | SC sem dados.')
    elif b_r3 is not None or s_r3 is not None or b_sup_total or s_sup_total:
        b_nc = bsb['suporte'].get('nc_oficial_pct')
        s_nc = sc['suporte'].get('nc_oficial_pct')
        b_nc_txt = 'sem dados' if b_sup_total == 0 or b_nc is None else f'{b_nc}%'
        s_nc_txt = 'sem dados' if s_sup_total == 0 or s_nc is None else f'{s_nc}%'
        bullets.append(f'NC suporte TEAMS: BSB {b_nc_txt} | SC {s_nc_txt}.')
        if b_r3 is not None and s_r3 is not None and b_sup_total and s_sup_total:
            bullets.append(f'Regra 3: BSB {b_r3}% | SC {s_r3}%.')

    b_reinc = bf.get('reinc_pct')
    s_reinc = sf.get('reinc_pct')
    if b_reinc is not None:
        bullets.append(f"Reincidência: BSB {b_reinc}% vs SC {s_reinc or 0}% dos agentes no período.")

    acoes = []
    if drivers:
        d0 = drivers[0]
        delta = int(d0.get('delta') or 0)
        label = d0.get('label') or 'cenário'
        if delta > 0:
            acoes.append(f"Priorizar capacitação em «{label}» na operação Brasília.")
        elif delta < 0:
            acoes.append(f"Priorizar capacitação em «{label}» na operação São Carlos.")
    if (bsb.get('suporte', {}).get('regra3_count') or 0) > (sc.get('suporte', {}).get('regra3_count') or 0):
        acoes.append("Revisar dúvidas críticas (Regra 3) com os top agentes de Brasília no TEAMS.")
    elif (sc.get('suporte', {}).get('regra3_count') or 0) > (bsb.get('suporte', {}).get('regra3_count') or 0):
        acoes.append("Revisar dúvidas críticas (Regra 3) com os top agentes de São Carlos no TEAMS.")
    if not acoes:
        acoes.append("Manter ritmo de acompanhamento semanal por turno e cenário dominante.")

    bullets.extend([f"Ação sugerida: {a}" for a in acoes[:2]])
    return pre_diagnostico_block(
        veredito=veredito,
        bullets=bullets[:5],
        titulo='Leitura para a gerência',
    )


def build_training_narrative(kpis):
    bullets = []
    total = kpis.get('total') or 0
    if not total:
        return pre_diagnostico_block(veredito='Nenhum treinamento registrado para o recorte.')
    assinados = kpis.get('assinados', 0)
    pendentes = kpis.get('pendentes')
    if pendentes is None:
        pendentes = max(0, total - int(assinados or 0) - int(kpis.get('vencidos') or 0))
    vencidos = kpis.get('vencidos', 0)
    bullets.append(
        f"{total} treinamentos no recorte; {assinados} assinados ({kpis.get('assinados_pct', 0)}%), "
        f"{pendentes} pendentes ({kpis.get('pendentes_pct', 0)}%), "
        f"{vencidos} vencidos ({kpis.get('vencidos_pct', 0)}%)."
    )
    if vencidos:
        bullets.append(
            f"{vencidos} com prazo ultrapassado ({kpis.get('vencidos_pct', 0)}%) — ação imediata recomendada."
        )
    if kpis.get('a_vencer_7d'):
        bullets.append(f"{kpis['a_vencer_7d']} a vencer nos próximos 7 dias.")
    if pendentes and not vencidos:
        bullets.append(
            f"{pendentes} treinamentos ainda pendentes de conclusão ou assinatura "
            f"({kpis.get('pendentes_pct', 0)}% do recorte)."
        )
    return pre_diagnostico_block(veredito=bullets[0], bullets=bullets[:4], titulo='Capacitação — resumo')


def build_support_falhas_bridge_narrative(summary, agentes_risco=None, clientes_workflows=None):
    agentes_risco = agentes_risco or []
    clientes_workflows = clientes_workflows or []
    r3 = int(summary.get('agentes_com_regra3_e_falha') or 0)
    nc = int(summary.get('agentes_com_nc_e_falha') or 0)
    wf = int(summary.get('workflows_com_suporte_e_falha') or 0)

    if r3 == 0 and nc == 0 and wf == 0:
        return pre_diagnostico_block(
            veredito='Não houve sobreposição relevante entre suporte e falhas no período filtrado.',
            bullets=[
                'Solicitações e falhas podem existir, mas não coincidem por agente ou cliente/workflow.',
                'Amplie o período ou mude a localidade para revisar outros recortes.',
            ],
            titulo='Conexão Suporte × Falhas',
        )

    bullets = []
    if r3:
        bullets.append(f"{r3} agente(s) com Regra 3 no suporte e falha no período.")
    if nc:
        bullets.append(f"{nc} agente(s) com NC no suporte e falha no período.")
    if wf:
        bullets.append(f"{wf} combinação(ões) cliente/workflow com suporte e falhas simultâneos.")

    reinc = sum(1 for a in agentes_risco if a.get('reincidente'))
    if reinc:
        bullets.append(f"{reinc} desses agentes também são reincidentes na métrica oficial.")

    if agentes_risco:
        top = agentes_risco[0]
        bullets.append(
            f"Maior sinal individual: {top.get('nome_agente') or top.get('matricula')} "
            f"({top.get('regra3_count', 0)} Regra 3, {top.get('falhas_total', 0)} falha(s))."
        )
    elif clientes_workflows:
        top = clientes_workflows[0]
        bullets.append(
            f"Maior concentração operacional: {top.get('cliente')} / {top.get('workflow')}."
        )

    veredito = summary.get('maior_risco') or bullets[0]
    return pre_diagnostico_block(
        veredito=veredito,
        bullets=bullets[:5],
        titulo='Conexão Suporte × Falhas',
    )


def build_training_reincidence_bridge_narrative(summary, agentes_criticos=None, lideres=None):
    agentes_criticos = agentes_criticos or []
    lideres = lideres or []
    total = int(summary.get('agentes_criticos_com_pendencia') or 0)
    reinc_v = int(summary.get('reincidentes_com_vencidos') or 0)
    alta_p = int(summary.get('alta_frequencia_com_pendencia') or 0)
    lideres_n = int(summary.get('lideres_com_risco') or 0)

    if total == 0:
        return pre_diagnostico_block(
            veredito='Não houve agentes críticos com pendência de capacitação no período filtrado.',
            bullets=[
                'Falhas usam data de análise; treinamentos usam data limite e situação calculada.',
                'Agentes críticos são reincidentes, alta frequência ou recorrentes em 4 meses.',
            ],
            titulo='Capacitação × Reincidência',
        )

    bullets = [
        f"{total} agente(s) crítico(s) com pendência de capacitação (vencido, a vencer ou previsto sem assinatura).",
    ]
    if reinc_v:
        bullets.append(f"{reinc_v} reincidente(s) oficial(is) com treinamento vencido.")
    if alta_p:
        bullets.append(f"{alta_p} agente(s) de alta frequência com pendência no recorte.")
    if lideres_n:
        bullets.append(f"{lideres_n} líder(es) concentram agentes críticos com pendências.")
    if agentes_criticos:
        top = agentes_criticos[0]
        bullets.append(
            f"Maior prioridade: {top.get('nome_agente') or top.get('matricula')} "
            f"({top.get('treinamentos_vencidos', 0)} vencido(s), risco {top.get('risco_capacitacao', '—')})."
        )

    veredito = summary.get('maior_risco') or bullets[0]
    return pre_diagnostico_block(
        veredito=veredito,
        bullets=bullets[:5],
        titulo='Capacitação × Reincidência',
    )
