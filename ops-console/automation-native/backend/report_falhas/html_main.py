# -*- coding: utf-8 -*-
"""HTML principal do e-mail diario (format_html + kpi_tile)."""

import re
from datetime import datetime, date

import pandas as pd

from report_falhas.io.data_loader import safe_str, norm_matricula
import report_falhas.config_report as cfg_report
from report_falhas.display_config import (
    APENAS_TOTAL,
    EXIBIR_REINC_GERAL,
    INCLUIR_CONSOLIDADO,
    INCLUIR_GRAFICO_DIARIO,
    INCLUIR_GRAFICO_EVOLUCAO,
    MOSTRAR_KPI_NOVOS_NO_MES,
    SEPARAR_REINC_POR_TURNO,
)
from report_falhas.html_blocks import table_row_cols
from report_falhas.kpis import make_kpis
from report_falhas.periods import label_comparativo_mes_anterior
from report_falhas.matricula_utils import clean_matricula_unified, resolve_agent_name
from report_falhas.outlook_render import (
    render_como_ler_delta_outlook,
    render_projecoes_insuficientes_outlook,
    render_rank_delta_outlook,
)
from report_falhas.analytics import (
    render_dimension_single_outlook,
    render_dimension_triplet_outlook,
    render_matrix_single_outlook,
    render_matrix_triplet_outlook,
    render_scenarios_single_outlook,
    render_scenarios_triplet_outlook,
)

COL_DATA_ANALISE = cfg_report.COL_DATA_ANALISE
COL_DATA_AUDITORIA = cfg_report.COL_DATA_AUDITORIA
COL_PROTOCOLO = cfg_report.COL_PROTOCOLO

def kpi_tile(title: str, value: str, sub: str = None) -> str:  # layout legado do e-mail
    sub_html = f"<div style='font-size:12px;color:#6c757d;margin-top:2px;'>{sub}</div>" if sub else ""
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#fff;border:1px solid #e9ecef;border-radius:10px;'>"
        "<tr><td style='padding:12px;'>"
        f"<div style='font-size:12px;color:#6c757d;margin-bottom:4px;'>{title}</div>"
        f"<div style='font-size:22px;font-weight:700;color:#212529;'>{value}</div>"
        f"{sub_html}"
        "</td></tr>"
        "</table>"
    )


def format_html(
    insights,
    cur_start, cur_end_mtd, n_dias_falha, dias_corridos, dias_sem_falha,
    prev_equal_start, prev_equal_end, total_atual, total_prev_equal,
    df_reinc_full, tempo_map_etapa, tempo_map_casa, nome_map, atividade_atual_hc_map, dificuldade_counts_map,
    top3_cenarios, novos_mes_list, turno_map=None,
    team_categoria_map=None,
    b64_meses=None, txt_meses="",
    b64_diario=None, txt_diario="",
    b64_meses_facil=None, txt_meses_facil="",
    b64_quarters_facil=None, txt_quarters_facil="",
 b64_tipo_doc=None, b64_uf_doc=None,
 docs_total=None, docs_fn=None, docs_fp=None,
 ufs_total=None, ufs_fn=None, ufs_fp=None,
 mat_tipo_uf_total=None, mat_tipo_uf_fn=None, mat_tipo_uf_fp=None,
 scen_tipo_total=None, scen_tipo_fn=None, scen_tipo_fp=None,
 scen_uf_total=None, scen_uf_fn=None, scen_uf_fp=None,
 etapa_map=None, dificuldade_map=None,
    total_atual_aud=0, aud_mesmo_mes=0, aud_outro_mes=0, spill_count_prev=0, spill_count_outros=0,
    spill_df=None, spill_breakdown=None,
    slope_meses=0, slope_facil=0, hist_meses=None,
 official_block=None,
 proto_diff=None,
 scen_geral=None,
 uf_ff=None,
 scen_ff=None,
 comp_txt_treinamento='',
    b64_turno=None,
    txt_turno='',
    b64_turno_fy=None,
    txt_turno_fy='',
    b64_falhas_por_agente_turno=None,
    txt_falhas_por_agente_turno='',
    tab_falhas_por_agente_turno='',
    is_fechamento_mes: bool = False,
    audit_grace_end: date | None = None,
):
    def _sanitize_matricula_nome_concat_html(html_text: str) -> str:
        """Failsafe: remove concatenação 'matricula + Nome do agente ...' no texto visível do HTML."""
        s = safe_str(html_text)
        if not s:
            return s
        # Ex.: >c92935aNome do agente Fulano< -> >c92935a<
        s = re.sub(
            r'>(\s*[A-Za-z]?\d{3,}[A-Za-z]?\s*)Nome\s*(?:do\s*agente|Agente)?\s*[:\-]?\s*[^<]*<',
            r'>\1<',
            s,
            flags=re.IGNORECASE,
        )
        return s

    def _fy_label_for_date(dt: date | datetime | None) -> str:
        if dt is None:
            return 'FY'
        try:
            fy_year = (dt.year + 1) if dt.month >= 4 else dt.year
            return f'FY{fy_year}'
        except Exception:
            return 'FY'

    fy_label = _fy_label_for_date(cur_end_mtd or cur_start)

    etapa_map = etapa_map or {}
    dificuldade_map = dificuldade_map or {}
    team_categoria_map = team_categoria_map or {}
    from report_falhas.team_category import CATEGORIA_NAO_CLASSIFICADO
    k = make_kpis(
        cur_start, cur_end_mtd, total_atual, total_prev_equal,
        prev_equal_start, prev_equal_end,
        df_reinc_full, top3_cenarios, novos_mes_list
    )
    # Tabela reincidência

    # Tabela reincidência (V8) — padrão final: Matrícula visível + Nome via title
    from html import escape as _esc

    REINC_HEADERS = [
        'Matrícula',
        'Team/Category',
        'Reincidente',
        'Ocorr. Mês Atual',
        'Cenário (Mês Atual)',
        'Nível de Dificuldade (contagem)',
        'Protocolos (Mês Atual)',
        'Etapa',
        'Atividade atual (HC)',
        'Tempo desde última Alteração de Atividade (HC)',
        'Tempo de Casa',
    ]

    def rows_reinc(df_r):
        if df_r is None or df_r.empty:
            return (
                f"<tr><td colspan='{len(REINC_HEADERS)}' style='padding:10px;border:1px solid #e9ecef;"
                "text-align:center;color:#6c757d;'>Sem dados no mês atual.</td></tr>"
            )

        out = []
        for _, r in df_r.iterrows():
            mat_raw = safe_str(r.get('Matrícula Agente', ''))
            
            # Usa a função centralizada de limpeza
            mat, nome_from_raw = clean_matricula_unified(mat_raw)
            mat_n = norm_matricula(mat)
            
            # Fallback: se limpeza não resultou em matrícula, tenta regex direto
            if not mat and mat_raw:
                mm = re.search(r'([A-Za-z]?\d{3,}[A-Za-z]?)', mat_raw)
                if mm:
                    mat = safe_str(mm.group(1))
                    mat_n = norm_matricula(mat)

            # Nome apenas para tooltip/fallback acessível
            nome = resolve_agent_name(mat_n, mat_raw, nome_map, r)

            # Exibe preferencialmente a matrícula limpa. Se não houver,
            # usa o valor normalizado como fallback. NUNCA exibe o valor bruto.
            display_code = mat or mat_n or '—'
            if nome:
                mat_html = f"<span title=\"Nome do agente: {_esc(nome, quote=True)}\" style='cursor:help;'>{_esc(display_code)}</span>"
            else:
                mat_html = _esc(display_code)

            tempo_etapa = tempo_map_etapa.get(mat_n, '—')
            tempo_casa = tempo_map_casa.get(mat_n, '—')
            atividade_hc = atividade_atual_hc_map.get(mat_n, '')
            dificuldade_mes = dificuldade_counts_map.get(mat_n, '')
            etapa_mes = (etapa_map or {}).get(mat, '') or (etapa_map or {}).get(mat_raw, '')

            cols = []
            cols.append(
                "<td style='padding:6px 8px;border:1px solid #e9ecef;white-space:nowrap;'>"
                f"{mat_html}</td>"
            )

            team_cat = safe_str(team_categoria_map.get(mat_n, CATEGORIA_NAO_CLASSIFICADO)) or CATEGORIA_NAO_CLASSIFICADO
            cols.append(
                f"<td style='padding:6px 8px;border:1px solid #e9ecef;white-space:nowrap;'>"
                f"{_esc(team_cat)}</td>"
            )

            reinc = safe_str(r.get('Reincidente', ''))
            cor = 'color:#DC3545;font-weight:bold;' if reinc.strip().lower() == 'sim' else ''
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;{cor}'>{_esc(reinc)}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;white-space:nowrap;'>{int(r.get('Ocorrências Mês Atual', 0) or 0)}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{_esc(safe_str(r.get('Cenário (Mês Atual)', '')))}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{_esc(safe_str(dificuldade_mes))}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{_esc(safe_str(r.get('Protocolos (Mês Atual)', '')))}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{_esc(safe_str(etapa_mes))}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{_esc(safe_str(atividade_hc))}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;white-space:nowrap;'>{_esc(safe_str(tempo_etapa))}</td>")
            cols.append(f"<td style='padding:6px 8px;border:1px solid #e9ecef;white-space:nowrap;'>{_esc(safe_str(tempo_casa))}</td>")

            out.append('<tr>' + ''.join(cols) + '</tr>')
        return '\n'.join(out)


    # Cabeçalho
    ths = REINC_HEADERS[:]
    thead_html = ''.join([
        f"<th style='padding:8px;border:1px solid #e9ecef;text-align:left;'>{{h}}</th>".format(h=h)
        for h in ths
    ])


    # --- Reincidência por Turno (HC) ---
    reinc_turno_block = "<div style='font-size:12px;color:#6c757d;'>Sem dados por turno no mês atual.</div>"
    reinc_turno_summary_html = ""
    try:
        if SEPARAR_REINC_POR_TURNO and df_reinc_full is not None and not df_reinc_full.empty and (turno_map is not None):
            df_tmp_turno = df_reinc_full.copy()
            df_tmp_turno['Turno'] = df_tmp_turno['Matrícula Agente'].map(lambda mm: (turno_map or {}).get(norm_matricula(mm), ''))
            df_tmp_turno['Turno'] = df_tmp_turno['Turno'].apply(safe_str)
            df_tmp_turno.loc[df_tmp_turno['Turno'] == '', 'Turno'] = 'Sem turno'
            top_turno = ''
            top_turno_occ = 0
            top_turno_cenario = ''
            top_turno_dif = ''
            if 'Ocorrências Mês Atual' in df_tmp_turno.columns:
                df_tmp_turno['_occ'] = pd.to_numeric(df_tmp_turno['Ocorrências Mês Atual'], errors='coerce').fillna(0)
                agg = df_tmp_turno.groupby('Turno')['_occ'].sum().sort_values(ascending=False)
                if not agg.empty:
                    top_turno = str(agg.index[0])
                    top_turno_occ = int(agg.iloc[0])
                    sub_top = df_tmp_turno[df_tmp_turno['Turno'] == top_turno].copy()
                    if 'Cenário (Mês Atual)' in sub_top.columns:
                        vc = sub_top['Cenário (Mês Atual)'].apply(safe_str)
                        vc = vc[vc != ''].value_counts()
                        if not vc.empty:
                            top_turno_cenario = str(vc.index[0])
                    if 'Nível de Dificuldade (contagem)' in sub_top.columns:
                        vd = sub_top['Nível de Dificuldade (contagem)'].apply(safe_str)
                        vd = vd[vd != ''].value_counts()
                        if not vd.empty:
                            top_turno_dif = str(vd.index[0])

            if top_turno:
                parts = []
                parts.append(f"<li>Turno com mais reincidência: <b>{safe_str(top_turno)}</b> ({top_turno_occ} ocorrências)</li>")
                if top_turno_cenario:
                    parts.append(f"<li>Cenário dominante no turno: <b>{_esc(top_turno_cenario)}</b></li>")
                if top_turno_dif:
                    parts.append(f"<li>Dificuldade dominante no turno: <b>{_esc(top_turno_dif)}</b></li>")
                reinc_turno_summary_html = (
                    "<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:10px 12px;margin:6px 0 10px;'>"
                    "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>⚡ Leitura rápida por turno</div>"
                    "<ul style='margin:6px 0 0;padding-left:18px;font-size:12px;color:#334155;'>"
                    + "".join(parts) +
                    "</ul></div>"
                )
            blocks = []
            for tval in sorted(df_tmp_turno['Turno'].unique().tolist()):
                sub = df_tmp_turno[df_tmp_turno['Turno'] == tval].copy()
                if sub.empty:
                    continue
                blocks.append(f"<h4 style='margin:14px 0 8px;'>⏱️ Turno: {safe_str(tval)}</h4>")
                blocks.append("<table role='presentation' cellpadding='0' cellspacing='0' style='border-collapse:collapse;width:100%;font-size:14px;margin:6px 0 14px;'>")
                blocks.append(f"<thead><tr style='background:#F8F9FA;'>{thead_html}</tr></thead>")
                blocks.append('<tbody>' + rows_reinc(sub) + '</tbody></table>')
            if blocks:
                reinc_turno_block = ''.join(blocks)
    except Exception:
        pass

    # Reincidência geral (tabela única) - opcional
    reinc_geral_html = ''
    try:
        if EXIBIR_REINC_GERAL:
            reinc_geral_html = (
                '<h3 style="margin:16px 0 8px;">🔁 Reincidência (geral)</h3>'
                '<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:14px;margin:6px 0 14px;">'
                f'<thead><tr style="background:#F8F9FA;">{thead_html}</tr></thead>'
                f'<tbody>{rows_reinc(df_reinc_full)}</tbody></table>'
            )
    except Exception:
        reinc_geral_html = ''


    # Ajuste: falhas do período sem casos auditados fora do período / fora da janela de fechamento
    fail_total = int(k.get('fail_mtd_atual', 0))
    spill_count = int(spill_count_outros or 0)
    fail_ajustado = max(fail_total - spill_count, 0)
    grace_fmt = audit_grace_end.strftime('%d/%m/%Y') if audit_grace_end else ''
    if is_fechamento_mes and audit_grace_end and cur_start:
        fora_fase_tip = (
            f"O mês do protocolo é definido pela Data de Análise. No fechamento de "
            f"{cur_start.strftime('%m/%Y')}, entram falhas com análise entre "
            f"{cur_start.strftime('%d/%m/%Y')} e {cur_end_mtd.strftime('%d/%m/%Y')}. "
            f"A Data Auditoria pode ir até {grace_fmt} (5º dia útil do mês seguinte) "
            f"sem sair do oficial. Análise em meses posteriores pertence ao report daquele mês."
        )
    else:
        fora_fase_tip = (
            "Fora de fase = caso cuja Data da Auditoria caiu em outro período, "
            "apesar de a Data de Análise ser do período avaliado."
        )
    fora_fase_html = (
        f" <span title=\"{_esc(fora_fase_tip, quote=True)}\" style='cursor:help;'>fora de fase</span>"
    )
    if is_fechamento_mes:
        if spill_count > 0:
            linha_ajuste = (
                f"Dessas, <b>{spill_count}</b> foram auditadas após o prazo de fechamento "
                f"e não entram no oficial{fora_fase_html}."
            )
        else:
            linha_ajuste = (
                "Nenhuma foi auditada fora da janela de fechamento; todas entram no oficial"
                f"{fora_fase_html}."
            )
    elif spill_count > 0:
        linha_ajuste = (
            f"Dessas, <b>{spill_count}</b> foram auditadas em outro período "
            f"e não entram no oficial{fora_fase_html}."
        )
    else:
        linha_ajuste = (
            f"Nenhuma foi auditada em outro período; todas entram no oficial{fora_fase_html}."
        )
    fail_sub = (
        f"<div>Falhas com Data de Análise no período ({k['periodo_mtd']}): <b>{fail_total}</b></div>"
        f"<div>{linha_ajuste}</div>"
        f"<div style='margin-top:4px;font-weight:800;color:#0f3d78;'>Oficial do período: {fail_ajustado}</div>"
    )
    if is_fechamento_mes and audit_grace_end and cur_start:
        _meses_nome = {
            1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril', 5: 'maio', 6: 'junho',
            7: 'julho', 8: 'agosto', 9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro',
        }
        mes_nome = _meses_nome.get(cur_start.month, cur_start.strftime('%m/%Y'))
        fail_sub += (
            f"<div style='margin-top:6px;font-size:12px;color:#475569;'>"
            f"Fechamento de {mes_nome}/{cur_start.year}: auditorias registradas até "
            f"{grace_fmt} contam no oficial, desde que a Data de Análise seja de "
            f"{mes_nome}.</div>"
        )
# Tiles KPIs
    periodo_prev_suffix = label_comparativo_mes_anterior(
        cur_start,
        cur_end_mtd,
        mes_atual_zero_falhas=(
            k.get("fail_mtd_atual", 0) == 0 and k.get("periodo_equal_prev") != "Sem dados"
        ),
    )
    periodo_prev_label = f"Comparativo: {k['periodo_equal_prev']} ({periodo_prev_suffix})"

    tiles_row1 = table_row_cols([
        kpi_tile("Falhas (por Data de Análise)", f"{fail_ajustado}", fail_sub),
        kpi_tile("Falhas mês anterior (comparativo)", f"{k['fail_prev_equal']}",
                 periodo_prev_label),
        kpi_tile("Diferença entre períodos", f"{k['variacao_perc']}", f"{k['variacao_delta']} falhas")
    ], gray_container=True)

    tiles_row2 = table_row_cols([
        kpi_tile("Agentes Reincidentes", f"{k['reinc_total']}") if True else " ",
        (kpi_tile("Agentes novos no mês", f"{k['novos_mes_count']}") if MOSTRAR_KPI_NOVOS_NO_MES else " "),
        kpi_tile("Top 1 - Cenário de falhas", f"{k['top1_cenario_qtd']}", k['top1_cenario_nome'])
    ], gray_container=True)

    # Auditoria + Spillover (ajustado: sem 'auditado no mês anterior')

# Auditoria (visão do mês pela Data Auditoria)
    tiles_row3 = ""  # Auditoria (Falhas por Data Auditoria) REMOVIDA conforme ajuste

    # ===== Consolidado (Métrica Oficial) =====
    tiles_row1_of = tiles_row2_of = tiles_row3_of = ""
    official_title = ""
    if official_block is not None:
        try:
            oc = official_block
            k_of = make_kpis(
                oc.get('cur_start'), oc.get('cur_end_mtd'),
                int(oc.get('total_atual', 0)), int(oc.get('total_prev_equal', 0)),
                oc.get('prev_equal_start'), oc.get('prev_equal_end'),
                oc.get('df_reinc_full'), oc.get('top3_cenarios') or [], oc.get('novos_mes_list') or []
            )

            periodo_prev_suffix_of = label_comparativo_mes_anterior(
                oc.get("cur_start"),
                oc.get("cur_end_mtd"),
                mes_atual_zero_falhas=(
                    k_of.get("fail_mtd_atual", 0) == 0
                    and k_of.get("periodo_equal_prev") != "Sem dados"
                ),
            )
            periodo_prev_label_of = (
                f"Comparativo: {k_of['periodo_equal_prev']} ({periodo_prev_suffix_of})"
            )

            tiles_row1_of = table_row_cols([
                kpi_tile("Falhas (por Data de Análise) - Métrica Oficial", f"{k_of['fail_mtd_atual']}", f"Período: {k_of['periodo_mtd']}"),
                kpi_tile("Falhas mês anterior (comparativo) - Métrica Oficial", f"{k_of['fail_prev_equal']}",
                         periodo_prev_label_of),
                kpi_tile("Diferença entre períodos - Métrica Oficial", f"{k_of['variacao_perc']}", f"{k_of['variacao_delta']} falhas"),
            ], gray_container=True)

            tiles_row2_of = table_row_cols([
                kpi_tile("Agentes Reincidentes - Métrica Oficial", f"{k_of['reinc_total']}"),
                (kpi_tile("Agentes novos no mês - Métrica Oficial", f"{k_of['novos_mes_count']}") if MOSTRAR_KPI_NOVOS_NO_MES else " "),
                kpi_tile("Top 1 - Cenário de falhas - Métrica Oficial", f"{k_of['top1_cenario_qtd']}", k_of['top1_cenario_nome']),
            ], gray_container=True)

            cur_s = oc.get('cur_start')
            cur_e = oc.get('cur_end_mtd')
            periodo_aud = f"Período: {cur_s.strftime('%d/%m/%Y')} → {cur_e.strftime('%d/%m/%Y')}" if (cur_s and cur_e) else "Sem dados"
            tiles_row3_of = ""  # Auditoria (Falhas por Data Auditoria) - Métrica Oficial REMOVIDA conforme ajuste

            official_title = '<h3 style="margin:8px 0 8px;">📌 Consolidado - Métrica Oficial</h3>'
        except Exception:
            official_title = ""

    # Gráficos
    # Gráficos
    def _looks_like_base64_png(b64):
        if not isinstance(b64, str):
            return False
        s = b64.strip()
        # base64 de PNG costuma ser grande; evita quebrar com dict/None/curto
        if len(s) < 200:
            return False
        # valida caracteres base64
        return re.fullmatch(r'[A-Za-z0-9+/=\s]+', s) is not None

    def _graf_block(b64, txt):
        if (not b64) or (not _looks_like_base64_png(b64)):
            return "<em>Sem dados.</em>"
        return (
            f"<img src=\"data:image/png;base64,{b64}\" width=\"702\" style=\"width:100%;height:auto;display:block;border-radius:8px;\">"
            f"<div style='font-size:12px;color:#6c757d;margin-top:6px'>{txt}</div>"
        )

    graf_meses = _graf_block(b64_meses, txt_meses) if INCLUIR_GRAFICO_EVOLUCAO else ""
    graf_meses_facil = _graf_block(b64_meses_facil, txt_meses_facil) if INCLUIR_GRAFICO_EVOLUCAO else ""
    graf_quarters_facil = _graf_block(b64_quarters_facil, txt_quarters_facil) if INCLUIR_GRAFICO_EVOLUCAO else ""
    # --- Gráfico de falhas por turno (HTML) ---
    graf_turno = _graf_block(b64_turno, txt_turno) if (b64_turno and _looks_like_base64_png(b64_turno)) else ""
    graf_turno_fy = _graf_block(b64_turno_fy, txt_turno_fy) if (b64_turno_fy and _looks_like_base64_png(b64_turno_fy)) else ""
    resumo_falhas_por_agente_turno = txt_falhas_por_agente_turno if (b64_falhas_por_agente_turno and _looks_like_base64_png(b64_falhas_por_agente_turno)) else ""
    graf_falhas_por_agente_turno = (
        f"<img src=\"data:image/png;base64,{b64_falhas_por_agente_turno}\" width=\"702\" style=\"width:100%;height:auto;display:block;border-radius:8px;\">"
        if (b64_falhas_por_agente_turno and _looks_like_base64_png(b64_falhas_por_agente_turno))
        else ""
    )
    # === Visões (Top/Matrix/Cenários) - mês atual ===
    docs_total = docs_total or []
    docs_fn = docs_fn or []
    docs_fp = docs_fp or []
    ufs_total = ufs_total or []
    ufs_fn = ufs_fn or []
    ufs_fp = ufs_fp or []

    # === Visoes (Top/Matrix/Cenarios) - mes atual ===
    docs_total = docs_total or []
    docs_fn = docs_fn or []
    docs_fp = docs_fp or []
    ufs_total = ufs_total or []
    ufs_fn = ufs_fn or []
    ufs_fp = ufs_fp or []

    if APENAS_TOTAL:
        bloco_docs = render_dimension_single_outlook('🧾 Tipos de documento (mes atual)', 'Tipo de documento', docs_total)
        bloco_ufs  = render_dimension_single_outlook('📍 UFs do documento (mes atual)', 'UF do documento', ufs_total)
    else:
        bloco_docs = render_dimension_triplet_outlook('🧾 Tipos de documento (mes atual)', 'Tipo de documento', docs_total, docs_fn, docs_fp)
        bloco_ufs  = render_dimension_triplet_outlook('📍 UFs do documento (mes atual)', 'UF do documento', ufs_total, ufs_fn, ufs_fp)

    bloco_matriz = ''
    if mat_tipo_uf_total is not None and not getattr(mat_tipo_uf_total, 'empty', True):
        if APENAS_TOTAL:
            bloco_matriz = render_matrix_single_outlook('🔀 Cruzamento Tipo x UF (mes atual)', mat_tipo_uf_total)
        else:
            bloco_matriz = render_matrix_triplet_outlook('🔀 Cruzamento Tipo x UF (mes atual)', mat_tipo_uf_total, mat_tipo_uf_fn, mat_tipo_uf_fp)

    if APENAS_TOTAL:
        bloco_cenarios_tipo = render_scenarios_single_outlook('🔎 Cenarios por Tipo (Top 5 tipos)', scen_tipo_total or [], dim_label='Tipo')
        bloco_cenarios_uf   = render_scenarios_single_outlook('🔎 Cenarios por UF (Top 10 UFs)', scen_uf_total or [], dim_label='UF')
    else:
        bloco_cenarios_tipo = render_scenarios_triplet_outlook('🔎 Cenarios por Tipo (Top 5 tipos)', scen_tipo_total or [], scen_tipo_fn or [], scen_tipo_fp or [], dim_label='Tipo')
        bloco_cenarios_uf   = render_scenarios_triplet_outlook('🔎 Cenarios por UF (Top 10 UFs)', scen_uf_total or [], scen_uf_fn or [], scen_uf_fp or [], dim_label='UF')




    # === Treinamento & feedback (sem jargão): comparativos de cenários + Formatação/Fonte ===
    bloco_treinamento = ""
    try:
        parts = []
        parts.append(render_rank_delta_outlook('📌 Cenários (geral) - principais e variação', 'Cenário', scen_geral or {}, comp_txt=comp_txt_treinamento))
        parts.append(render_rank_delta_outlook('🅵🅵 Formatação/Fonte - Tipo + UF e variação', 'Tipo | UF', scen_ff or {}, comp_txt=comp_txt_treinamento))
        bloco_treinamento = "".join([p for p in parts if p])
    except Exception:
        bloco_treinamento = ""

    graf_diario = _graf_block(b64_diario, txt_diario) if INCLUIR_GRAFICO_DIARIO else ""
    # --- Divergência de protocolos (Base) ---
    # (removido do HTML: exportado em XLSX separado para acompanhamento interno)
    protocolos_diff_html = ''
    como_ler_comparativo_html = render_como_ler_delta_outlook(comp_txt_treinamento)
    resumo_html = (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:14px;margin-bottom:16px;'>"
        "<h3 style='margin-bottom:8px;'>📊 Resumo das Tendências</h3>"
        "<ul style='margin:0;padding-left:18px;font-size:14px;color:#212529;'>"
        + ''.join([f"<li>{it}</li>" for it in (insights or [])]) + "</ul>"
        + (render_projecoes_insuficientes_outlook(hist_meses) if slope_meses is None else "")
        + "</div>"
    )
    
    # Adicionar explicacao compacta dentro do resumo quando projecoes estao bloqueadas
    bloco_projecoes_explicativo = ""

    piora_melhora = f"{safe_str(k.get('variacao_delta', ''))} falhas ({safe_str(k.get('variacao_perc', ''))})"
    cenário_principal = top3_cenarios[0][0] if top3_cenarios else ''
    foco_imediato = cenário_principal or 'Cenário com maior frequência no período'
    reinc_txt = f"Reincidentes: {safe_str(k.get('reinc_total', '0'))} | Novos no mês: {safe_str(k.get('novos_mes_count', '0'))}"

    exec_html = (
        "<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px;margin:0 0 16px;'>"
        "<div style='font-weight:900;margin:0 0 6px;'>⚡ Leitura Executiva</div>"
        "<ul style='margin:6px 0 0;padding-left:18px;font-size:13px;color:#334155;'>"
        f"<li>Variação do período: <b>{_esc(piora_melhora)}</b></li>"
        f"<li>Principal cenário de atenção: <b>{_esc(safe_str(cenário_principal))}</b></li>"
        f"<li>Reincidência e novos no mês: <b>{_esc(reinc_txt)}</b></li>"
        f"<li>Foco imediato: <b>{_esc(safe_str(foco_imediato))}</b></li>"
        "</ul></div>"
    )

    cenarios_exec_html = ""
    try:
        top_impact = (scen_geral or {}).get('impacto', [])
        top_growth = (scen_geral or {}).get('crescimento', [])
        item_impact = top_impact[0].get('label') if top_impact else ''
        item_growth = top_growth[0].get('label') if top_growth else ''
        if item_impact or item_growth:
            cenarios_exec_html = (
                "<div style='background:#FFF7ED;border:1px solid #FDBA74;border-radius:10px;padding:10px 12px;margin:12px 0 0;'>"
                "<div style='font-weight:800;margin:0 0 6px;'>🎯 Prioridade de Cenários</div>"
                "<ul style='margin:6px 0 0;padding-left:18px;font-size:12px;color:#7C2D12;'>"
                + (f"<li>O que mais acontece: <b>{_esc(safe_str(item_impact))}</b></li>" if item_impact else "")
                + (f"<li>O que mais piorou: <b>{_esc(safe_str(item_growth))}</b></li>" if item_growth else "")
                + "</ul></div>"
            )
    except Exception:
        cenarios_exec_html = ""

    next_focus_html = (
        "<div style='background:#F1F5F9;border:1px solid #E2E8F0;border-radius:10px;padding:12px;margin:14px 0 0;'>"
        "<div style='font-weight:900;margin:0 0 6px;'>🧭 Próximos focos sugeridos</div>"
        "<ul style='margin:6px 0 0;padding-left:18px;font-size:13px;color:#334155;'>"
        f"<li>Cenário principal a atacar: <b>{_esc(safe_str(cenário_principal))}</b></li>"
        f"<li>Verificar crescimento de cenários (bloco de variação) e reforçar ações.</li>"
        f"<li>Priorizar reincidência e novos no mês (indicador de pressão operacional).</li>"
        "</ul></div>"
    )

    spill_html = ""
    if spill_df is not None and not isinstance(spill_df, dict) and not spill_df.empty:
        cols = []
        if COL_PROTOCOLO in spill_df.columns: cols.append(COL_PROTOCOLO)
        if COL_DATA_AUDITORIA in spill_df.columns: cols.append(COL_DATA_AUDITORIA)
        if COL_DATA_ANALISE in spill_df.columns: cols.append(COL_DATA_ANALISE)
        spill_df_print = spill_df[cols].copy()
        if COL_DATA_AUDITORIA in spill_df_print.columns:
            spill_df_print[COL_DATA_AUDITORIA] = spill_df_print[COL_DATA_AUDITORIA].dt.strftime("%d/%m/%Y")
        if COL_DATA_ANALISE in spill_df_print.columns:
            spill_df_print[COL_DATA_ANALISE] = spill_df_print[COL_DATA_ANALISE].dt.strftime("%d/%m/%Y")
        spill_rows = spill_df_print.head(50).to_records(index=False)
        rows_html = "\n".join(
            "<tr>" + "".join([f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{safe_str(v)}</td>" for v in row]) + "</tr>"
            for row in spill_rows
        )
        th_html = "".join([f"<th style='padding:8px;border:1px solid #e9ecef;text-align:left;'>{c}</th>" for c in cols])
        spill_html = "".join([
            "<h3 style='margin:16px 0 8px;'>🧾 Fora de Fase (resumo)</h3>",
            "<table role='presentation' cellpadding='0' cellspacing='0' style='border-collapse:collapse;width:100%;font-size:14px;margin:6px 0 14px;'>",
            f"<thead><tr style='background:#F8F9FA;'>{th_html}</tr></thead>",
            f"<tbody>{rows_html}</tbody>",
            "</table>"
        ])

    html = f"""
<!DOCTYPE html>
<html lang='pt-br'>
<head>
  <meta charset='utf-8'>
  <title>Relatório Diário - Consolidado + Reincidência</title>
</head>
<body style="font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif; color:#212529; background:#fff; line-height:1.4; padding:16px;">
  <!--[if mso]>
<table role='presentation' align='center' width='702' cellpadding='0' cellspacing='0'><tr><td>
<![endif]-->
<table role='presentation' class='container' width='100%' cellpadding='0' cellspacing='0' style='max-width:702px;margin:0 auto;border:1px solid #e9ecef;border-radius:10px;'>
<tr><td style='padding:0;'>
    <table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#174E97;color:#fff;'><tr><td style='padding:14px 18px;'>
      <h2 style="margin:0;font-weight:700;">📣 Report Falhas Críticas</h2>
      <div style="margin-top:4px;font-size:12px;opacity:0.9;">🕒 Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
    </td></tr></table>
    <table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr><td style='padding:16px 18px;'>
 {como_ler_comparativo_html}
 {resumo_html}
 {exec_html}
      {'<h3 style="margin:8px 0 8px;">📌 Consolidado</h3>' if INCLUIR_CONSOLIDADO else ''}
      {tiles_row1 if INCLUIR_CONSOLIDADO else ''}
      {tiles_row2 if INCLUIR_CONSOLIDADO else ''}

 {official_title if (INCLUIR_CONSOLIDADO and official_block is not None) else ''}
 {tiles_row1_of if (INCLUIR_CONSOLIDADO and official_block is not None) else ''}
 {tiles_row2_of if (INCLUIR_CONSOLIDADO and official_block is not None) else ''}

      <div style="font-size:12px;color:#6c757d;margin:0 0 10px;">Obs.: <b>Tempo desde última Alteração de Atividade (HC)</b> é calculado a partir do último evento de <i>Alteração/Mudança de Atividade</i> no HC (regra do próximo ciclo).</div>
<h3 style="margin:16px 0 8px;">🔁 Reincidência por turno</h3>
 {reinc_turno_summary_html}
 {reinc_turno_block}
 {reinc_geral_html}
 {bloco_treinamento}
 {cenarios_exec_html}
 {next_focus_html}
    {f'<h3 style="margin:16px 0 8px;">📈 Evolução - {fy_label}</h3>' if INCLUIR_GRAFICO_EVOLUCAO else ''}
      {('<div style="background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:10px;">'+graf_meses+'</div>') if INCLUIR_GRAFICO_EVOLUCAO else ''}
    {f'<h3 style="margin:16px 0 8px;">🟦 Falhas fáceis - {fy_label}</h3>' if INCLUIR_GRAFICO_EVOLUCAO else ''}
      {('<div style="background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:10px;">'+graf_meses_facil+'</div>') if INCLUIR_GRAFICO_EVOLUCAO else ''}
    {f'<h3 style="margin:16px 0 8px;">🗓️ Falhas fáceis por quarter ({fy_label})</h3>' if INCLUIR_GRAFICO_EVOLUCAO else ''}
      {('<div style="background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:10px;">'+graf_quarters_facil+'</div>') if INCLUIR_GRAFICO_EVOLUCAO else ''}
          {'<h3 style="margin:16px 0 8px;">👥  Carga de falhas por agente (por turno) - Mês Atual</h3>' if graf_falhas_por_agente_turno else ''}
          {('<div style="font-size:13px;color:#4b5563;margin:0 0 8px;">Pressão operacional proporcional indica quantas falhas, em média, cada agente absorve em um turno, considerando o tamanho do time.</div>') if graf_falhas_por_agente_turno else ''}
          {('<details style="background:#FFF7E6;border-left:4px solid #F5A623;border-radius:6px;padding:10px;margin-bottom:12px;font-size:13px;line-height:1.5;">'
              '<summary style="font-weight:800;cursor:pointer;">📌 O que este indicador mostra</summary>'
              '<div style="margin-top:6px;">Este indicador mostra onde a operação está mais pressionada em cada turno, considerando quantas falhas recaem, em média, sobre cada agente ativo.<br>'
              'Na prática, turnos com times menores podem operar sob maior pressão, mesmo quando apresentam menos falhas totais.<br>'
              '<br><strong>🔍 Importante:</strong><br>'
              'Este indicador não avalia desempenho individual e não é ranking de agentes.<br>'
              'Ele apoia decisões de equilíbrio de capacidade, atenção operacional e mitigação de risco.'
              '</div></details>') if graf_falhas_por_agente_turno else ''}
        {(resumo_falhas_por_agente_turno if graf_falhas_por_agente_turno else '')}
        {('<div style="background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:10px;">'+graf_falhas_por_agente_turno+'</div>') if graf_falhas_por_agente_turno else ''}
        {(tab_falhas_por_agente_turno if graf_falhas_por_agente_turno else '')}
            {'<h3 style="margin:16px 0 8px;">⏱️ Falhas por turno - Mês Atual</h3>' if graf_turno else ''}
            {('<div style="background:#E7F3FF;border-left:4px solid #1E4F91;border-radius:6px;padding:10px;margin-bottom:12px;font-size:13px;line-height:1.5;"><strong>Objetivo:</strong> Distribuição de qualidade das falhas por turno <strong>(apenas mês atual)</strong>.<br><br><strong>Interpretação das cores:</strong><br>🔴 <strong>Fácil (Vermelho)</strong> = Falha crítica de baixa complexidade = <strong>(alta evitabilidade)</strong><br>🟡 <strong>Médio (Laranja)</strong> = Falha crítica de complexidade média<br>🟢 <strong>Difícil (Verde)</strong> = Falha crítica com alta complexidade = fator atenuante<br><br><strong>Observação:</strong> Os turnos possuem diferentes tamanhos de equipe e volumes operacionais. Não é indicador de desempenho, mas <strong>distribuição proporcional de qualidade</strong>.</div>') if graf_turno else ''}
            {('<div style="background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:10px;">'+graf_turno+'</div>') if graf_turno else ''}
      {'<h3 style="margin:16px 0 8px;">⏱️ Falhas por turno - FY (Acumulado desde Abril)</h3>' if graf_turno_fy else ''}
      {('<div style="background:#E7F3FF;border-left:4px solid #1E4F91;border-radius:6px;padding:10px;margin-bottom:12px;font-size:13px;line-height:1.5;"><strong>Objetivo:</strong> Distribuição de qualidade das falhas por turno <strong>(acumulado desde abril = FY inteiro)</strong>.<br><br><strong>Interpretação das cores:</strong><br>🔴 <strong>Fácil (Vermelho)</strong> = Falha crítica de baixa complexidade = <strong>(alta evitabilidade)</strong><br>🟡 <strong>Médio (Laranja)</strong> = Falha crítica de complexidade média<br>🟢 <strong>Difícil (Verde)</strong> = Falha crítica com alta complexidade = fator atenuante<br><br><strong>Comparação:</strong> Compare com gráfico de mês atual para identificar tendências ao longo do FY.<br><strong>Observação:</strong> Os turnos possuem diferentes tamanhos de equipe e volumes operacionais. Não é indicador de desempenho, mas <strong>distribuição proporcional de qualidade</strong>.</div>') if graf_turno_fy else ''}
      {('<div style="background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:10px;">'+graf_turno_fy+'</div>') if graf_turno_fy else ''}
      {'<h3 style="margin:16px 0 8px;">📅 Falhas por dia - mês atual</h3>' if INCLUIR_GRAFICO_DIARIO else ''}
      {('<div style="background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:10px;">'+graf_diario+'</div>') if INCLUIR_GRAFICO_DIARIO else ''}
      {spill_html}
    </td></tr></table>
<!--[if mso]>
</td></tr></table>
<![endif]-->
</body>
</html>
"""
    html = _sanitize_matricula_nome_concat_html(html)
    return html
