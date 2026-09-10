# -*- coding: utf-8 -*-
"""Abas HTML avançadas: Painel Executivo, Rastreabilidade, Reincidência, Causas."""
from __future__ import annotations

import html as html_lib

import pandas as pd

from report_brb.brb_analytics import BRBAnalytics, TRACE_COLS
from report_brb.brb_charts import chart_barh, chart_bars, chart_donut, img_tag
from report_brb.brb_classify import CATEGORIA_RECOMENDACOES, categoria_label
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics
from report_brb.config_brb import CLIENT

C = CLIENT["cores"]

TRACE_DISPLAY = [
    ("_protocolo_norm", "Protocolo"),
    ("_matricula_norm", "Matrícula"),
    ("Nome Agente", "Agente"),
    ("chave_demanda", "Demanda"),
    ("Data de Análise", "Data"),
    ("categoria_macro", "Categoria"),
    ("severidade", "Severidade"),
    ("descricao_padrao", "Descrição"),
]


def _esc(x) -> str:
    return html_lib.escape("" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))


def _alert_html(nivel: str, texto: str) -> str:
    return f'<div class="alert alert-{nivel}">{_esc(texto)}</div>'


def _kpi_card(label: str, value, sub: str = "") -> str:
    sub_html = f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else ""
    return (
        f'<div class="kpi accent-primary"><div class="kpi-lbl">{_esc(label)}</div>'
        f'<div class="kpi-val">{_esc(value)}</div>{sub_html}</div>'
    )


def build_analytics_tabs(
    analytics: BRBAnalytics,
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    df_table_fn,
    section_fn,
    grid2_fn,
    chart_frame_fn,
    lead_fn,
) -> dict[str, str]:
    """Constrói HTML das abas analíticas. df_table_fn = _df_table do render principal."""
    return {
        "painel": _tab_painel(analytics, metrics, df_table_fn, lead_fn),
        "rastreabilidade": _tab_rastreabilidade(analytics, df_table_fn, section_fn, lead_fn),
        "reincidencia": _tab_reincidencia(analytics, df_table_fn, section_fn, grid2_fn, chart_frame_fn),
        "causas": _tab_causas(analytics, df_table_fn, section_fn, grid2_fn, chart_frame_fn),
        "qualidade": _tab_qualidade(analytics, df_table_fn, section_fn, lead_fn, chart_frame_fn),
    }


def _tab_painel(analytics, metrics, df_table, lead,) -> str:
    c = analytics.cruzamentos
    kpis = (
        f'<div class="kpi-strip">'
        f'{_kpi_card("Casos FG", c.get("casos_fg", 0), f"{c.get("protocolos_fg", 0)} protocolos")}'
        f'{_kpi_card("Falhas NA", c.get("falhas_na", 0), f"{c.get("protocolos_na", 0)} protocolos")}'
        f'{_kpi_card("Taxa conforme", f"{c.get("taxa_conformidade", 0)}%", f"{c.get("conforme_sim", 0)} Sim")}'
        f'{_kpi_card("Treinamentos", c.get("treinamentos", 0), f"{c.get("agentes_treinados", 0)} agentes")}'
        f"</div>"
    )
    alertas = "".join(_alert_html(a["nivel"], a["texto"]) for a in analytics.alertas_qualidade[:6])
    if not alertas:
        alertas = _alert_html("verde", "Nenhum alerta crítico de qualidade de dados no momento.")

    prior = "".join(f"<li>{_esc(p)}</li>" for p in analytics.prioridades) or "<li>Sem prioridades críticas identificadas.</li>"
    oport = "".join(f"<li>{_esc(o)}</li>" for o in analytics.oportunidades) or "<li>Manter monitoramento regular.</li>"
    acao = "".join(f"<li><b>{_esc(a)}</b></li>" for a in analytics.acao_agora)

    cruz_cards = (
        f'<div class="cruz-grid">'
        f'<div class="cruz-item"><span class="cruz-val">{c.get("pct_fg_sem_na", 0)}%</span>'
        f'<span class="cruz-lbl">FG sem match NA</span><span class="cruz-num">{c.get("fg_sem_match_na", 0)} casos</span></div>'
        f'<div class="cruz-item"><span class="cruz-val">{c.get("pct_fg_sem_av", 0)}%</span>'
        f'<span class="cruz-lbl">FG sem CONFORME</span><span class="cruz-num">{c.get("fg_sem_contestacao", 0)} casos</span></div>'
        f'<div class="cruz-item"><span class="cruz-val">{c.get("pct_fg_com_na", 0)}%</span>'
        f'<span class="cruz-lbl">FG com match NA</span><span class="cruz-num">{c.get("fg_com_match_na", 0)} casos</span></div>'
        f"</div>"
    )

    recs = "".join(
        f'<div class="rec-item rec-{r.prioridade}"><span class="rec-tag">{_esc(r.area)}</span> {_esc(r.texto)}</div>'
        for r in analytics.recomendacoes[:8]
    )

    return (
        lead("painel")
        + f'<div class="callout">{analytics.resumo_executivo}</div>'
        + kpis
        + f'<h3 class="block-title">Alertas prioritários</h3>{alertas}'
        + cruz_cards
        + f'<div class="grid-2"><div><h3 class="block-title">Top 5 — ação necessária</h3><ul class="action-list">{prior}</ul></div>'
        + f'<div><h3 class="block-title">Oportunidades</h3><ul class="action-list">{oport}</ul></div></div>'
        + f'<h3 class="block-title">O que fazer agora</h3><ul class="action-list">{acao}</ul>'
        + f'<h3 class="block-title">Recomendações automáticas</h3><div class="rec-list">{recs}</div>'
    )


def _tab_rastreabilidade(analytics, df_table, section, lead) -> str:
    cols = TRACE_DISPLAY
    seq = getattr(analytics, "sequencia_resumo", None) or {}
    seq_cards = (
        '<div class="cruz-grid cruz-grid-4">'
        f'<div class="cruz-item accent-green"><span class="cruz-val">{seq.get("n_completo_esperado", 0)}</span>'
        f'<span class="cruz-lbl">Sequência esperada</span></div>'
        f'<div class="cruz-item accent-warn"><span class="cruz-val">{seq.get("n_sem_contestacao", 0)}</span>'
        f'<span class="cruz-lbl">Sem contestação</span></div>'
        f'<div class="cruz-item"><span class="cruz-val">{seq.get("n_ordem_invertida", 0)}</span>'
        f'<span class="cruz-lbl">Ordem invertida</span></div>'
        f'<div class="cruz-item"><span class="cruz-val">{seq.get("n_na_sem_fg", 0)}</span>'
        f'<span class="cruz-lbl">NA sem FG</span></div>'
        "</div>"
    )
    seq_df = getattr(analytics, "sequencia_protocolos", None)
    seq_html = ""
    if seq_df is not None and not seq_df.empty:
        pref = seq_df[seq_df["classe"].isin(["ordem_invertida", "sem_contestacao", "completo_esperado"])]
        sample = pref.head(20) if not pref.empty else seq_df.head(20)
        show = sample.copy()
        for col in ("dt_fg", "dt_na", "dt_cont"):
            if col in show.columns:
                show[col] = show[col].apply(
                    lambda x: x.strftime("%d/%m/%Y") if pd.notna(x) else "—"
                )
        seq_cols = [
            ("Protocolo", "Protocolo"),
            ("classe", "Classe"),
            ("dt_fg", "Data auditoria"),
            ("dt_na", "Data notificação"),
            ("dt_cont", "Data contestação"),
        ]
        seq_html = section(
            "Amostra de protocolos por classe de sequência",
            df_table(show, seq_cols, table_id="trac-seq"),
            f"{len(sample)} registro(s)",
        )
    return (
        lead("rastreabilidade")
        + '<div class="callout callout-warn">Tabelas de exceção — casos que exigem revisão de integração ou dados.</div>'
        + '<p class="lead">Sequência temporal: auditoria → notificação Qualidade→CS → contestação.</p>'
        + seq_cards
        + seq_html
        + section(
            "FG sem match em Notificação Ativa",
            df_table(analytics.fg_sem_match_na, cols, table_id="trac-na"),
            f"{len(analytics.fg_sem_match_na)} registro(s)",
        )
        + section(
            "FG sem avaliação CONFORME",
            df_table(analytics.fg_sem_contestacao, cols, table_id="trac-cont"),
            f"{len(analytics.fg_sem_contestacao)} registro(s)",
        )
        + section(
            "Protocolos com múltiplas matrículas",
            df_table(analytics.multi_matricula, [("_protocolo_norm", "Protocolo"), ("n_matriculas", "Nº matrículas")], table_id="trac-multi"),
        )
        + section(
            "Agentes reincidentes (amostra)",
            df_table(analytics.reincidencia_agentes, [("Agente", "Agente"), ("Falhas", "Falhas")], table_id="trac-rein"),
        )
        + section(
            "Registros com dados incompletos",
            df_table(analytics.dados_incompletos, cols, table_id="trac-inc"),
        )
        + section(
            "Sem categoria ou severidade definida",
            df_table(analytics.sem_categoria_severidade, cols, table_id="trac-sem"),
        )
    )


def _tab_reincidencia(analytics, df_table, section, grid2, chart_frame) -> str:
    chart_ag = chart_frame(
        img_tag(
            chart_barh(
                list(zip(analytics.reincidencia_agentes["Agente"], analytics.reincidencia_agentes["Falhas"]))
                if not analytics.reincidencia_agentes.empty
                else [],
                "Agentes reincidentes",
                color=C["red"],
            ),
            "Reincidência agentes",
        ),
        "Top agentes com mais de uma falha",
    )
    chart_cat = chart_frame(
        img_tag(
            chart_barh(
                list(zip(analytics.reincidencia_categorias["Categoria"], analytics.reincidencia_categorias["Quantidade"]))
                if not analytics.reincidencia_categorias.empty
                else [],
                "Categorias recorrentes",
                color=C["primary"],
            ),
            "Reincidência categorias",
        ),
        "Categorias com maior recorrência",
    )
    trein = ""
    if not analytics.treinamento_pos_falha.empty:
        trein = section(
            "Treinados com falha posterior",
            df_table(
                analytics.treinamento_pos_falha,
                [("Agente", "Agente"), ("Treinamentos", "Treinamentos"), ("Falhas pós-treino", "Falhas pós-treino")],
                table_id="rein-trein",
            ),
            "Agentes que tiveram capacitação e nova falha depois",
        )
    mes_chart = ""
    if not analytics.reincidencia_mes.empty:
        mes_chart = chart_frame(
            img_tag(
                chart_bars(
                    list(analytics.reincidencia_mes["Mês"]),
                    list(analytics.reincidencia_mes["Casos"]),
                    "Volume por mês",
                    rotate=25,
                ),
                "Reincidência mensal",
            ),
            "Evolução mensal de casos FG",
        )
    return (
        '<p class="lead">Reincidência identifica repetição por agente, protocolo, demanda e categoria — sinal de necessidade de ação direcionada.</p>'
        + grid2(chart_ag, chart_cat)
        + section("Ranking de protocolos recorrentes", df_table(analytics.reincidencia_protocolos, [("Protocolo", "Protocolo"), ("Ocorrências", "Ocorrências")], table_id="rein-prot"))
        + section("Ranking de demandas QI", df_table(analytics.reincidencia_demandas, [("Demanda QI", "Demanda"), ("Quantidade", "Quantidade")], table_id="rein-dem"))
        + mes_chart
        + trein
    )


def _tab_causas(analytics, df_table, section, grid2, chart_frame) -> str:
    cat_chart = chart_frame(
        img_tag(
            chart_donut(
                list(analytics.top_categorias["Categoria"]) if not analytics.top_categorias.empty else [],
                list(analytics.top_categorias["Quantidade"]) if not analytics.top_categorias.empty else [],
                "Categorias macro",
            ),
            "Categorias",
        ),
        "Distribuição por categoria macro",
    )
    sev_chart = chart_frame(
        img_tag(
            chart_barh(
                list(zip(analytics.por_severidade["Severidade"], analytics.por_severidade["Quantidade"]))
                if not analytics.por_severidade.empty
                else [],
                "Severidade",
                color="#ea580c",
            ),
            "Severidade",
        ),
        "Distribuição por severidade",
    )
    rec_blocks = []
    for code, texto in CATEGORIA_RECOMENDACOES.items():
        n = 0
        if not analytics.top_categorias.empty:
            match = analytics.top_categorias[analytics.top_categorias["Categoria"] == categoria_label(code)]
            if not match.empty:
                n = int(match.iloc[0]["Quantidade"])
        if n > 0 or code in ("ADULTERACAO_VISUAL", "FRAUDE_NAO_IDENTIFICADA", "SOBREPOSICAO_FOTO"):
            rec_blocks.append(
                f'<div class="cat-rec"><h4>{_esc(categoria_label(code))} ({n} casos)</h4>'
                f'<p>{_esc(texto)}</p></div>'
            )
    return (
        '<p class="lead">Causas agrupadas em categorias macro padronizadas — facilita priorização e plano de ação.</p>'
        + grid2(cat_chart, sev_chart)
        + section("Top descrições originais", df_table(analytics.top_descricoes, [("Descrição", "Descrição"), ("Quantidade", "Quantidade")], table_id="caus-desc"))
        + section(
            "Categoria × Severidade",
            df_table(
                analytics.categoria_severidade,
                [("categoria_macro", "Categoria"), ("severidade", "Severidade"), ("Quantidade", "Quantidade")],
                table_id="caus-cruz",
            ),
        )
        + '<h3 class="block-title">Recomendações por categoria</h3>'
        + f'<div class="cat-rec-list">{"".join(rec_blocks[:8])}</div>'
    )


def _tab_qualidade(analytics, df_table, section, lead, chart_frame_fn) -> str:
    rows = "".join(
        f'<tr><td><span class="alert-dot dot-{r["nivel"]}"></span> {_esc(r["metrica"])}</td>'
        f'<td>{r["quantidade"]}</td><td>{r["percentual"]}%</td>'
        f'<td><span class="badge badge-{r["nivel"]}">{_esc(r["nivel"])}</span></td></tr>'
        for _, r in analytics.qualidade.iterrows()
    ) if not analytics.qualidade.empty else ""
    table = (
        f'<div class="table-wrap"><table class="data-table"><thead><tr>'
        f"<th>Métrica</th><th>Qtd</th><th>%</th><th>Nível</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )
    avisos = "".join(f"<li>{_esc(a)}</li>" for a in analytics.temporal_avisos)
    avisos_html = f'<ul class="action-list">{avisos}</ul>' if avisos else ""
    temporal_html = ""
    fg_mes = analytics.temporal.get("fg_por_mes", {})
    if fg_mes:
        temporal_html = chart_frame_fn(
            img_tag(
                chart_bars(list(fg_mes.keys()), list(fg_mes.values()), "FG por mês", rotate=25),
                "Evolução FG",
            ),
            "Volume de Falhas Gerais por mês",
        )
    return (
        lead("qualidade")
        + '<div class="callout">Indicadores de completude e consistência — verde: ok · amarelo: atenção · vermelho: crítico.</div>'
        + table
        + avisos_html
        + temporal_html
    )


def analytics_extra_css() -> str:
    return """
    .alert{padding:12px 16px;border-radius:6px;margin-bottom:10px;font-size:13px;border-left:4px solid;}
    .alert-verde{background:#ecfdf5;border-color:#15803d;color:#065f46;}
    .alert-amarelo{background:#fffbeb;border-color:#d97706;color:#92400e;}
    .alert-vermelho{background:#fef2f2;border-color:#b91c1c;color:#991b1b;}
    .alert-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;}
    .dot-verde{background:#15803d;}.dot-amarelo{background:#d97706;}.dot-vermelho{background:#b91c1c;}
    .badge-verde{background:#ecfdf5;color:#047857;}.badge-amarelo{background:#fffbeb;color:#b45309;}
    .badge-vermelho{background:#fef2f2;color:#b91c1c;}
    .sev-critica{background:#fef2f2;color:#b91c1c;}
    .sev-alta{background:#fff7ed;color:#c2410c;}
    .sev-media{background:#fffbeb;color:#b45309;}
    .sev-baixa{background:#ecfdf5;color:#047857;}
    .sev-indef{background:#f1f5f9;color:#64748b;}
    .cat-badge{background:#eff6ff;color:#1d4ed8;}
    .cruz-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0;}
    @media(max-width:800px){.cruz-grid{grid-template-columns:1fr;}}
    .cruz-item{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:16px;text-align:center;}
    .cruz-val{font-size:28px;font-weight:700;color:#0b2a59;display:block;}
    .cruz-lbl{font-size:11px;text-transform:uppercase;color:#64748b;letter-spacing:.04em;}
    .cruz-num{font-size:12px;color:#64748b;display:block;margin-top:4px;}
    .action-list{margin:0 0 20px;padding-left:20px;color:#475569;line-height:1.6;}
    .rec-list{display:flex;flex-direction:column;gap:8px;}
    .rec-item{padding:12px 14px;border-radius:6px;font-size:13px;border:1px solid #e2e8f0;background:#fafbfc;}
    .rec-alta{border-left:3px solid #b91c1c;}
    .rec-media{border-left:3px solid #d97706;}
    .rec-baixa{border-left:3px solid #15803d;}
    .rec-tag{font-size:10px;text-transform:uppercase;font-weight:700;color:#64748b;margin-right:8px;}
    .cat-rec-list{display:flex;flex-direction:column;gap:12px;}
    .cat-rec{padding:14px 16px;border:1px solid #e2e8f0;border-radius:6px;background:#fff;}
    .cat-rec h4{margin:0 0 8px;font-size:14px;color:#0b2a59;}
    .cat-rec p{margin:0;font-size:13px;color:#475569;line-height:1.5;}
    """
