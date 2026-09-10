# -*- coding: utf-8 -*-

"""Páginas HTML completas (Etapa 2)."""

from datetime import date, datetime, timedelta
from io import BytesIO
import base64
import html as _html
import json
import re
import unicodedata

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from report_falhas.io.data_loader import (
    normalize_text,
    safe_str,
    safe_to_datetime,
    norm_matricula,
    norm_protocolo,
)
from report_falhas.assets import resolve_logo_data_uri
from report_falhas.config_report import (
    COL_CLIENTE,
    COL_DATA,
    COL_DATA_ANALISE,
    COL_DATA_AUDITORIA,
    COL_MATRICULA,
    COL_CENARIO as _COL_CENARIO_DEFAULT,
    COL_PROTOCOLO,
    COL_WORKFLOW_BASE,
    SUP_COL_DATA,
    SUP_COL_PROTO,
    SUP_COL_CLIENTE,
    SUP_COL_WORKFLOW,
    SUP_COL_AGENTE,
    SUP_COL_LIDER_SOL,
    SUP_COL_TIPO_SOL,
    SUP_COL_DUVIDA,
    SUP_COL_CONCL,
    SUP_COL_LOCAL_SOL,
    SUP_COL_OBS,
    SUP_COL_OBS_SUP,
    SUP_COL_CONFORM,
    SUP_COL_DIFIC,
    SUP_COL_CRIT,
    SUP_COL_TIPO_DOC,
    SUP_COL_UF_EMIS,
)
from report_falhas.contestacoes import get_contest_state
from report_falhas.html_blocks import format_html as format_html_blocks
from report_falhas.html_blocks import table_row_cols as table_row_cols_blocks
from report_falhas.kpis import is_formatacao_fonte
from report_falhas.matricula_utils import clean_matricula_unified as _clean_matricula_pair
from report_falhas.periods import (
    add_months_first_day,
    april_start,
    filter_by_date_range_on,
    first_day,
    month_last_day,
    months_from_to,
    prev_month_first_day,
)
from report_falhas import periods as _periods_mod
from report_falhas.utils_report import (
    MESES_ABREV_BR,
    build_metric_filter_buttons,
    fmt_data_br,
    fmt_delta_html,
    fmt_mes_ano_br,
)

# Mantido para compatibilidade: pode ser atualizado por report_falhas_criticas.read_base
COL_CENARIO = _COL_CENARIO_DEFAULT

from report_falhas.render.pages.common import wrap_simple_page


# ===== Função Centralizada de Limpeza de Matrícula =====
def _build_mats_ativos_hc(df_hc: pd.DataFrame | None) -> set[str]:
    """Matrículas ativas no HC: possui ao menos um registro com data_final vazia."""
    if df_hc is None or df_hc.empty:
        return set()
    hc = df_hc.copy()
    if 'mat_norm' not in hc.columns:
        col_mat = ''
        for candidate in ['matricula_agente', 'Matrícula Agente', 'Matrícula', 'matricula']:
            if candidate in hc.columns:
                col_mat = candidate
                break
        if not col_mat:
            return set()
        hc['mat_norm'] = hc[col_mat].apply(norm_matricula)
    hc = hc[hc['mat_norm'].astype(str).str.strip() != ''].copy()
    if hc.empty:
        return set()
    if 'data_final' not in hc.columns:
        return {safe_str(m) for m in hc['mat_norm'].unique() if safe_str(m)}
    open_rows = hc[hc['data_final'].isna()]
    return {safe_str(m) for m in open_rows['mat_norm'].unique() if safe_str(m)}


def build_treinamentos_tracking_page_html(
    df_trein: pd.DataFrame,
    df_hc: pd.DataFrame | None = None,
    scope_name: str = "",
    ref_date: date | None = None,
) -> str:
    ref_date = ref_date or datetime.now().date()
    scope_norm = normalize_text(safe_str(scope_name))
    allowed_local: str | None = None
    if 'brasilia' in scope_norm or 'bsb' in scope_norm:
        allowed_local = 'Brasília'
    elif 'sao carlos' in scope_norm or 'saocarlos' in scope_norm or 'sanca' in scope_norm:
        allowed_local = 'São Carlos'
    df = df_trein.copy() if df_trein is not None else pd.DataFrame()
    for col in [
        'Status', 'Event', 'Event: EventTitle', 'SignatureDate', 'Event:EventDeadline',
        'AssignmentDate', 'Agent: UserLanID',
    ]:
        if col not in df.columns:
            df[col] = ''

    df['mat_norm'] = df['Agent: UserLanID'].apply(norm_matricula)

    nome_map = {}
    if df_hc is not None and not df_hc.empty:
        hc = df_hc.copy()
        col_mat_hc = ''
        for candidate in ['matricula_agente', 'Matrícula Agente', 'Matrícula', 'matricula']:
            if candidate in hc.columns:
                col_mat_hc = candidate
                break
        col_nome_hc = ''
        for candidate in ['nome_agente', 'Nome Agente', 'Nome', 'nome']:
            if candidate in hc.columns:
                col_nome_hc = candidate
                break
        if col_mat_hc and col_nome_hc:
            hc_names = hc[[col_mat_hc, col_nome_hc]].copy()
            hc_names['mat_norm'] = hc_names[col_mat_hc].apply(norm_matricula)
            hc_names['nome_agente'] = hc_names[col_nome_hc].apply(safe_str)
            hc_names = hc_names[hc_names['mat_norm'] != '']
            for mat_norm, grp in hc_names.groupby('mat_norm'):
                nome = next((safe_str(v) for v in grp['nome_agente'].tolist() if safe_str(v)), '')
                if nome:
                    nome_map[mat_norm] = nome

    hc_map = {}
    if df_hc is not None and not df_hc.empty:
        hc = df_hc.copy()
        if 'mat_norm' not in hc.columns and 'matricula_agente' in hc.columns:
            hc['mat_norm'] = hc['matricula_agente'].apply(norm_matricula)
        if 'localidade' in hc.columns:
            hc_loc = hc[['mat_norm', 'localidade']].copy()
            hc_loc['mat_norm'] = hc_loc['mat_norm'].apply(norm_matricula)
            hc_loc['localidade'] = hc_loc['localidade'].apply(safe_str)
            hc_loc = hc_loc[hc_loc['mat_norm'] != '']
            hc_map = {}
            for mat_norm, grp in hc_loc.groupby('mat_norm'):
                locais = {normalize_text(loc) for loc in grp['localidade'].tolist() if safe_str(loc)}
                if len(locais) == 1:
                    loc = safe_str(grp['localidade'].iloc[0])
                    loc_norm = normalize_text(loc)
                    if 'brasilia' in loc_norm or loc_norm == 'bsb':
                        hc_map[mat_norm] = 'Brasília'
                    elif 'sao carlos' in loc_norm:
                        hc_map[mat_norm] = 'São Carlos'
                    else:
                        hc_map[mat_norm] = 'Localidade não identificada'
                else:
                    hc_map[mat_norm] = 'Localidade não identificada'

    def _map_localidade(mat_norm: str) -> str:
        return hc_map.get(mat_norm, 'Localidade não identificada')

    df['Localidade'] = df['mat_norm'].apply(_map_localidade)
    df['nome_agente'] = df['mat_norm'].map(lambda m: nome_map.get(m, ''))

    def _deadline_flag(row) -> str:
        if row.get('SignatureDate') is not pd.NaT and pd.notna(row.get('SignatureDate')):
            return 'Finalizado'
        st = normalize_text(safe_str(row.get('Status', '')))
        if st == 'ministrado':
            return 'Pendente de assinatura do agente'
        if st == 'previsto':
            return 'Previsto'
        d = row.get('Event:EventDeadline')
        if d is None or (hasattr(pd, 'isna') and pd.isna(d)):
            return ''
        try:
            dd = pd.Timestamp(d).date()
        except Exception:
            return ''
        if dd < ref_date:
            return 'Vencido'
        if dd < (ref_date + timedelta(days=7)):
            return 'A vencer'
        return 'Dentro do prazo'

    def _prefix(title: str) -> str:
        s = safe_str(title)
        if not s:
            return 'Outros'
        for sep in [' - ', ' | ', ':', ' — ', ' – ']:
            if sep in s:
                return s.split(sep)[0].strip() or 'Outros'
        return s.split()[0].strip() or 'Outros'

    def _status_display(row) -> str:
        st = normalize_text(safe_str(row.get('Status', '')))
        if st == 'assinado' or (row.get('SignatureDate') is not pd.NaT and pd.notna(row.get('SignatureDate'))):
            return 'Finalizado'
        if st == 'ministrado':
            return 'Ministrado (sem assinatura)'
        if st == 'previsto':
            return 'Previsto'
        return safe_str(row.get('Status', '')) or '—'

    def _fmt_deadline(dval) -> str:
        if dval is None or (hasattr(pd, 'isna') and pd.isna(dval)):
            return '—'
        try:
            return pd.Timestamp(dval).strftime('%d/%m/%Y')
        except Exception:
            return safe_str(dval)

    info_text = (
        "Esta aba apresenta uma visão consolidada dos treinamentos registrados na operação. "
        "Para consultas específicas, utilize o filtro por matrícula para visualizar apenas os treinamentos do agente selecionado, garantindo uma leitura direcionada e segura."
    )

    def _render_consolidado(df_view: pd.DataFrame, label: str) -> str:
        status_norm = df_view['Status'].apply(safe_str).apply(normalize_text)
        is_signed = (status_norm == 'assinado') | df_view['SignatureDate'].notna()
        is_ministrado = (status_norm == 'ministrado') & (~df_view['SignatureDate'].notna())
        is_previsto = status_norm == 'previsto'
        deadline_flags = df_view.apply(_deadline_flag, axis=1)

        total = int(len(df_view))
        assinados = int(is_signed.sum())
        ministrados = int(is_ministrado.sum())
        previstos = int(is_previsto.sum())
        vencidos = int((deadline_flags == 'Vencido').sum())
        a_vencer = int((deadline_flags == 'A vencer').sum())

        prefixes = df_view['Event: EventTitle'].apply(_prefix) if not df_view.empty else pd.Series(dtype=str)
        vc = prefixes.value_counts() if not df_view.empty else pd.Series(dtype=int)
        dist_rows = "".join(
            "<tr>"
            f"<td>{safe_str(lbl)}</td>"
            f"<td style='text-align:right;'>{int(qtd)}</td>"
            f"<td style='text-align:right;'>{(int(qtd) / total * 100.0 if total else 0):.1f}%</td>"
            "</tr>"
            for lbl, qtd in vc.items()
        ) if not df_view.empty else "<tr><td colspan='3' style='color:#6B7280;'>Sem dados de treinamentos.</td></tr>"

        main_rows = []
        if df_view.empty:
            main_rows.append("<tr><td colspan='5' style='color:#6B7280;'>Sem dados de treinamentos.</td></tr>")
        else:
            for _, r in df_view.iterrows():
                signed_row = (normalize_text(safe_str(r.get('Status', ''))) == 'assinado') or (
                    r.get('SignatureDate') is not pd.NaT and pd.notna(r.get('SignatureDate'))
                )
                situation = '' if signed_row else (_deadline_flag(r) or 'Dentro do prazo')
                main_rows.append(
                    "<tr>"
                    f"<td>{safe_str(r.get('Event: EventTitle',''))}</td>"
                    f"<td>{_status_display(r)}</td>"
                    f"<td>{_fmt_deadline(r.get('Event:EventDeadline'))}</td>"
                    f"<td>{situation}</td>"
                    f"<td>{safe_str(r.get('Localidade',''))}</td>"
                    "</tr>"
                )

        return (
            "<div class='card'>"
            f"<div style='font-weight:900;margin:0 0 6px;font-size:13px;'>Visão consolidada - {safe_str(label)}</div>"
            "<div style='display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:6px;'>"
            f"<div class='quick'><div style='font-size:12px;color:#6B7280;'>Total de treinamentos</div><div style='font-size:22px;font-weight:900;margin-top:4px;'>{total}</div></div>"
            f"<div class='quick'><div style='font-size:12px;color:#6B7280;'>Assinados</div><div style='font-size:22px;font-weight:900;margin-top:4px;'>{assinados}</div></div>"
            f"<div class='quick'><div style='font-size:12px;color:#6B7280;'>Ministrados</div><div style='font-size:22px;font-weight:900;margin-top:4px;'>{ministrados}</div></div>"
            "</div>"
            "<div style='display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:10px;'>"
            f"<div class='quick'><div style='font-size:12px;color:#6B7280;'>Previstos</div><div style='font-size:22px;font-weight:900;margin-top:4px;'>{previstos}</div></div>"
            f"<div class='quick'><div style='font-size:12px;color:#6B7280;'>Vencidos</div><div style='font-size:22px;font-weight:900;margin-top:4px;'>{vencidos}</div></div>"
            f"<div class='quick'><div style='font-size:12px;color:#6B7280;'>A vencer (7 dias)</div><div style='font-size:22px;font-weight:900;margin-top:4px;'>{a_vencer}</div></div>"
            "</div>"
            "</div>"
            "<div class='card'>"
            "<div style='font-weight:900;margin:0 0 6px;font-size:13px;'>Distribuição por tipo de ação (prefixo do título)</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0'>"
            "<thead><tr><th>Tipo</th><th style='text-align:right;'>Qtd</th><th style='text-align:right;'>%</th></tr></thead>"
            f"<tbody>{dist_rows}</tbody>"
            "</table>"
            "</div>"
            "<div class='card'>"
            "<div style='font-weight:900;margin:0 0 6px;font-size:13px;'>Treinamentos registrados</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0'>"
            "<thead><tr><th>Event: EventTitle</th><th>Status</th><th>Event:EventDeadline</th><th>Situação</th><th>Localidade</th></tr></thead>"
            f"<tbody>{''.join(main_rows)}</tbody>"
            "</table>"
            "</div>"
        )

    # Consolidated frames per locality
    consolidado_geral = df.copy()
    consolidado_brasilia = df[df['Localidade'] == 'Brasília'].copy()
    consolidado_saocarlos = df[df['Localidade'] == 'São Carlos'].copy()

    # If the report is generated for a specific scope (BU), enforce that only that BU is shown
    if allowed_local == 'Brasília':
        consolidado_geral = consolidado_brasilia.copy()
        consolidado_saocarlos = consolidado_saocarlos.iloc[0:0].copy()
    elif allowed_local == 'São Carlos':
        consolidado_geral = consolidado_saocarlos.copy()
        consolidado_brasilia = consolidado_brasilia.iloc[0:0].copy()

    df_brasilia = consolidado_brasilia.copy()
    df_saocarlos = consolidado_saocarlos.copy()

    mes_ref_start = first_day(ref_date)
    mes_ref_end = month_last_day(ref_date)
    mes_ref_label = mes_ref_start.strftime('%m/%Y')
    hoje = datetime.now().date()
    hoje_label = hoje.strftime('%d/%m/%Y')

    def _matricula_cell(r) -> str:
        mat = safe_str(r.get('Agent: UserLanID', '')) or '—'
        nome = safe_str(r.get('nome_agente', ''))
        mat_esc = _html.escape(mat)
        if not nome:
            return f"<td>{mat_esc}</td>"
        nome_esc = _html.escape(nome)
        return (
            f'<td class="mat-hover-cell" title="Nome do agente: {nome_esc}">'
            f'<span class="mat-val">{mat_esc}</span>'
            f'<div class="mat-tip"><div class="ttl">Nome do agente</div>'
            f'<div class="sub">{nome_esc}</div></div></td>'
        )

    mats_ativos_hc = _build_mats_ativos_hc(df_hc)

    def _somente_agentes_ativos(df_view: pd.DataFrame) -> pd.DataFrame:
        if df_view.empty or not mats_ativos_hc:
            return df_view.iloc[0:0].copy()
        return df_view[df_view['mat_norm'].isin(mats_ativos_hc)].copy()

    def _split_previstos(df_view: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        df_view = _somente_agentes_ativos(df_view)
        if df_view.empty:
            return df_view.iloc[0:0].copy(), df_view.iloc[0:0].copy()
        status_norm = df_view['Status'].apply(safe_str).apply(normalize_text)
        dl = pd.to_datetime(df_view['Event:EventDeadline'], errors='coerce')
        base = status_norm == 'previsto'
        no_mes = (
            base & dl.notna()
            & (dl.dt.date >= mes_ref_start) & (dl.dt.date <= mes_ref_end)
        )
        mask_venc = no_mes & (dl.dt.date < hoje)
        mask_mes = no_mes & (dl.dt.date >= hoje)
        df_venc = df_view.loc[mask_venc].copy()
        df_mes = df_view.loc[mask_mes].copy()
        for part in (df_venc, df_mes):
            if not part.empty:
                part['_dl_sort'] = pd.to_datetime(part['Event:EventDeadline'], errors='coerce')
                part.sort_values(['_dl_sort', 'Event: EventTitle'], inplace=True, na_position='last')
        return df_venc, df_mes

    _TREIN_PREV_PAGE = 20

    def _rows_previsto_table(df_part: pd.DataFrame, empty_msg: str, section_key: str) -> str:
        if df_part.empty:
            return (
                "<tr><td colspan='6' style='color:#6B7280;'>"
                f"{_html.escape(empty_msg)}"
                "</td></tr>"
            )
        out = []
        for idx, (_, r) in enumerate(df_part.iterrows()):
            hidden = ' trein-prev-row-hidden' if idx >= _TREIN_PREV_PAGE else ''
            out.append(
                f'<tr class="trein-prev-row trein-prev-{section_key}{hidden}" data-section="{section_key}">'
                + _matricula_cell(r)
                + f"<td>{_html.escape(safe_str(r.get('Event', '')) or '—')}</td>"
                + f"<td>{_html.escape(safe_str(r.get('Event: EventTitle', '')) or '—')}</td>"
                + f"<td>{_html.escape(_status_display(r))}</td>"
                + f"<td>{_html.escape(_fmt_deadline(r.get('Event:EventDeadline')))}</td>"
                + f"<td>{_html.escape(safe_str(r.get('Localidade', '')) or '—')}</td>"
                + "</tr>"
            )
        return ''.join(out)

    def _kpi_local_breakdown(df_part: pd.DataFrame) -> str:
        if allowed_local or df_part.empty:
            return ''
        vc_loc = df_part['Localidade'].value_counts()
        parts = [f"{_html.escape(safe_str(loc))}: {int(q)}" for loc, q in vc_loc.items()]
        return (
            "<div style='font-size:12px;color:#6B7280;margin-top:6px;'>"
            + " · ".join(parts)
            + "</div>"
        )

    def _render_previsoes_resumo(df_view: pd.DataFrame) -> str:
        df_venc, df_mes = _split_previstos(df_view)
        n_venc = int(len(df_venc))
        n_mes = int(len(df_mes))
        thead = (
            "<thead><tr>"
            "<th>Matrícula</th><th>ID (Event)</th><th>Título</th><th>Status</th>"
            "<th>Prazo</th><th>Localidade</th>"
            "</tr></thead>"
        )
        more_venc = max(0, n_venc - _TREIN_PREV_PAGE)
        more_mes = max(0, n_mes - _TREIN_PREV_PAGE)
        btn_venc = (
            f"<button type='button' class='trein-filter-btn' id='trein-more-vencidos'"
            f" style='margin-top:8px;{'display:none;' if more_venc <= 0 else ''}'"
            f" onclick=\"treinPrevisaoShowMore('vencidos');return false;\">"
            f"Ver mais ({more_venc} restante(s))</button>"
        ) if more_venc else ''
        btn_mes = (
            f"<button type='button' class='trein-filter-btn' id='trein-more-mes'"
            f" style='margin-top:8px;{'display:none;' if more_mes <= 0 else ''}'"
            f" onclick=\"treinPrevisaoShowMore('mes');return false;\">"
            f"Ver mais ({more_mes} restante(s))</button>"
        ) if more_mes else ''
        return (
            "<div class='card' id='trein-previsoes-card'>"
            "<div style='font-weight:900;margin:0 0 6px;font-size:13px;'>"
            "📅 Visão geral — treinamentos previstos"
            "</div>"
            "<div class='muted' style='font-size:12px;margin-bottom:10px;'>"
            f"Somente previstos com prazo em <b>{mes_ref_label}</b> e agentes ativos no HC. "
            f"<b>Previsto do mês</b>: prazo no mês e a partir de hoje ({hoje_label}). "
            f"<b>Vencidos</b>: previstos do mês com prazo anterior a hoje. "
            f"Agentes ativos considerados: <b>{len(mats_ativos_hc)}</b>. "
            "Clique no card para alternar a lista. Exibindo 20 por vez — use Ver mais."
            "</div>"
            "<div style='display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:14px;'>"
            f"<div class='quick res-previsto trein-prev-kpi trein-prev-kpi-active' id='trein-kpi-mes'"
            " role='button' tabindex='0'"
            " onclick=\"treinPrevisaoSetView('mes');return false;\">"
            f"<div style='font-size:12px;color:#6B7280;'>Previsto do mês ({mes_ref_label})</div>"
            f"<div style='font-size:22px;font-weight:900;margin-top:4px;'>{n_mes}</div>"
            f"{_kpi_local_breakdown(df_mes)}"
            "</div>"
            "<div class='quick res-vencido trein-prev-kpi' id='trein-kpi-vencidos'"
            " role='button' tabindex='0'"
            " onclick=\"treinPrevisaoSetView('vencidos');return false;\">"
            f"<div style='font-size:12px;color:#6B7280;'>Vencidos do mês ({mes_ref_label})</div>"
            f"<div style='font-size:22px;font-weight:900;margin-top:4px;'>{n_venc}</div>"
            f"{_kpi_local_breakdown(df_venc)}"
            "</div>"
            "</div>"
            f"<div id='trein-sec-mes'>"
            f"<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>"
            f"Previstos no mês ({mes_ref_label}) — no prazo</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0'>"
            f"{thead}<tbody id='trein-tbody-mes'>"
            + _rows_previsto_table(
                df_mes,
                f'Nenhum previsto em {mes_ref_label} ainda no prazo para esta praça.',
                'mes',
            )
            + "</tbody></table>"
            f"{btn_mes}"
            "</div>"
            "<div id='trein-sec-vencidos' class='trein-prev-sec-hidden'>"
            f"<div style='font-weight:800;margin:0 0 6px;font-size:12px;margin-top:14px;'>"
            f"Vencidos do mês ({mes_ref_label})</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0'>"
            f"{thead}<tbody id='trein-tbody-vencidos'>"
            + _rows_previsto_table(
                df_venc,
                f'Nenhum previsto de {mes_ref_label} vencido para esta praça.',
                'vencidos',
            )
            + "</tbody></table>"
            f"{btn_venc}"
            "</div>"
            "</div>"
        )

    previsoes_block = _render_previsoes_resumo(consolidado_geral)

    matriculas_brasilia = sorted({safe_str(v) for v in df_brasilia['Agent: UserLanID'].tolist() if safe_str(v)})
    matriculas_saocarlos = sorted({safe_str(v) for v in df_saocarlos['Agent: UserLanID'].tolist() if safe_str(v)})

    dataset = []
    for _, r in df.iterrows():
        dataset.append({
            'matricula': safe_str(r.get('Agent: UserLanID', '')),
            'nome_agente': safe_str(r.get('nome_agente', '')),
            'event_id': safe_str(r.get('Event', '')),
            'assignment_date': _fmt_deadline(r.get('AssignmentDate')),
            'event_title': safe_str(r.get('Event: EventTitle', '')),
            'status': _status_display(r),
            'deadline': _fmt_deadline(r.get('Event:EventDeadline')),
            'deadline_raw': safe_str(r.get('Event:EventDeadline', '')),
            'status_raw': safe_str(r.get('Status', '')),
            'signature_date': _fmt_deadline(r.get('SignatureDate')),
            'localidade': safe_str(r.get('Localidade', '')),
            'mat_norm': safe_str(r.get('mat_norm', '')),
            'assignment_raw': safe_str(r.get('AssignmentDate', '')),
        })

    dataset_brasilia = [item for item in dataset if item.get('localidade') == 'Brasília']
    dataset_saocarlos = [item for item in dataset if item.get('localidade') == 'São Carlos']

    # Enforce scope: if report scope limits to one BU, hide the other dataset
    if allowed_local == 'Brasília':
        dataset_saocarlos = []
    if allowed_local == 'São Carlos':
        dataset_brasilia = []

    dataset_js = json.dumps(dataset, ensure_ascii=False, default=str)
    dataset_brasilia_js = json.dumps(dataset_brasilia, ensure_ascii=False, default=str)
    dataset_saocarlos_js = json.dumps(dataset_saocarlos, ensure_ascii=False, default=str)
    matricula_localidade_js = json.dumps(hc_map, ensure_ascii=False, default=str)
    info_html = (
        "<div class='card'>"
        "<div style='font-weight:900;margin:0 0 6px;font-size:13px;'>📚 Acompanhamento de Treinamentos (Previstos, Ministrados e Assinados)</div>"
        f"<div class='muted' style='font-size:12px;'>{info_text}</div>"
        "</div>"
    )

    query_block = (
        "<div class='card'>"
        "<div style='font-weight:900;margin:0 0 6px;font-size:13px;'>🔍 Consultar treinamentos por agente</div>"
        "<div style='display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;align-items:end;'>"
        "<label><div style='font-size:12px;color:#6B7280;margin-bottom:4px;'>Matrícula</div><input id='trein-matricula' type='text' style='width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;' placeholder='Matrícula do Agente'></label>"
        "<label><div style='font-size:12px;color:#6B7280;margin-bottom:4px;'>Data inicial</div><input id='trein-data-inicial' type='date' style='width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;'></label>"
        "<label><div style='font-size:12px;color:#6B7280;margin-bottom:4px;'>Data final</div><input id='trein-data-final' type='date' style='width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;'></label>"
        "<label><div style='font-size:12px;color:#6B7280;margin-bottom:4px;'>Status (opcional)</div><select id='trein-status' style='width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;'><option value=''>Todos</option><option value='Assinado'>Assinado</option><option value='Ministrado'>Ministrado</option><option value='Previsto'>Previsto</option></select></label>"
        "</div>"
        "<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;'>"
        "<button id='trein-buscar' class='trein-filter-btn active' type='button' onclick=\"setTreinamentoConsulta();return false;\">Consultar</button>"
        "<button id='trein-ordenar-data' class='trein-filter-btn' type='button' onclick=\"ordenarTreinConsultaPorData();return false;\">Data crescente</button>"
        "<button id='trein-limpar' class='trein-filter-btn' type='button' onclick=\"limparTreinConsulta();return false;\">Limpar</button>"
        "</div>"
        "<div id='trein-consulta-hint' class='muted' style='font-size:12px;margin-top:10px;'>Informe a matrícula para visualizar apenas os treinamentos do agente selecionado.</div>"
        "<div id='trein-consulta-result' style='margin-top:10px;display:none;'></div>"
        "</div>"
    )

    query_hint_lists = (
        "<div class='card' style='margin-top:10px;'>"
        "<div style='font-size:12px;color:#6B7280;font-weight:700;margin-bottom:4px;'>Consulta separada por localidade</div>"
        f"<div style='font-size:12px;line-height:1.5;'><strong>Brasília:</strong> {len(dataset_brasilia)} registro(s) • <strong>São Carlos:</strong> {len(dataset_saocarlos)} registro(s)</div>"
        "</div>"
    )

    consolidated_block = (
        "<div style='display:block;'>"
        + _render_consolidado(consolidado_geral, 'Geral')
        + "<div class='card'><div style='font-weight:900;margin:0 0 6px;font-size:13px;'>Visões por localidade</div>"
        + "<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;'>"
        + "<span class='quick' style='padding:8px 10px;'>Brasília</span>"
        + "<span class='quick' style='padding:8px 10px;'>São Carlos</span>"
        + "</div></div>"
        + _render_consolidado(consolidado_brasilia, 'Brasília')
        + _render_consolidado(consolidado_saocarlos, 'São Carlos')
        + "</div>"
    )

    # pass allowed_local to JS to enforce per-report BU filtering
    allowed_local_js = json.dumps(allowed_local or '')

    js = """
    <script>
    var TREINAMENTOS_DATA = __DATA__;
    var TREINAMENTOS_DATA_BRASILIA = __DATA_BRASILIA__;
    var TREINAMENTOS_DATA_SAOCARLOS = __DATA_SAOCARLOS__;
    var MATRICULA_LOCALIDADE_MAP = __MAT_MAP__;
    var ALLOWED_LOCAL = __ALLOWED_LOCAL__;
    function _escHtml(v){
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    function _normDateValue(v){
        if(!v){ return ''; }
        var d = String(v).trim();
        if(!d){ return ''; }
        var m = d.match(/^(\\d{2})\\/(\\d{2})\\/(\\d{4})/);
        if(m){ return m[3] + '-' + m[2] + '-' + m[1]; }
        var p = new Date(d);
        if(!isNaN(p.getTime())){ return p.toISOString().slice(0, 10); }
        return '';
    }
    function _deadlineSituation(raw, status, ref){
        var s = String(status || '').toLowerCase();
        if(s === 'assinado'){ return 'Finalizado'; }
        if(s === 'ministrado'){ return 'Pendente de assinatura do agente'; }
        if(s === 'previsto'){ return 'Previsto'; }
        var dt = _normDateValue(raw);
        if(!dt){ return ''; }
        var d = new Date(dt + 'T00:00:00');
        if(isNaN(d.getTime())){ return ''; }
        var today = new Date(ref + 'T00:00:00');
        var limit = new Date(today);
        limit.setDate(limit.getDate() + 7);
        if(d < today){ return 'Vencido'; }
        if(d < limit){ return 'A vencer'; }
        return 'Dentro do prazo';
    }
    function _situationKey(value){
        var v = String(value || '').toLowerCase();
        if(v.indexOf('finalizado') === 0){ return 'finalizado'; }
        if(v.indexOf('pendente') === 0){ return 'pendente'; }
        if(v === 'previsto'){ return 'previsto'; }
        if(v === 'vencido'){ return 'vencido'; }
        if(v === 'a vencer'){ return 'avencer'; }
        return 'outro';
    }
    function _countSituations(rows){
        var counts = { total: rows.length, finalizado: 0, pendente: 0, previsto: 0, vencido: 0, avencer: 0 };
        rows.forEach(function(r){
            var sit = _deadlineSituation(r.deadline_raw, r.status_raw, '__REF_DATE__');
            var key = _situationKey(sit);
            if(counts.hasOwnProperty(key)){ counts[key] += 1; }
        });
        return counts;
    }
    function _renderSummaryCard(label, value, extraClass){
        return '<div class="trein-resumo-card ' + extraClass + '"><div class="trein-resumo-ttl">' + _escHtml(label) + '</div><div class="trein-resumo-qtd">' + _escHtml(value) + '</div></div>';
    }
    function _renderConsultaResumo(counts){
        return '<div class="trein-resumo">'
            + _renderSummaryCard('Total', counts.total, '')
            + _renderSummaryCard('Finalizado', counts.finalizado, 'res-finalizado')
            + _renderSummaryCard('Pendente de assinatura', counts.pendente, 'res-pendente')
            + _renderSummaryCard('Previsto', counts.previsto, 'res-previsto')
            + _renderSummaryCard('Vencido / A vencer', (counts.vencido + counts.avencer), 'res-vencido')
            + '</div>';
    }
    function _parseDateSortValue(v){
        var d = _normDateValue(v);
        if(!d){ return Number.POSITIVE_INFINITY; }
        var p = new Date(d + 'T00:00:00');
        return isNaN(p.getTime()) ? Number.POSITIVE_INFINITY : p.getTime();
    }
    function _renderConsultaTable(rows){
        var html = '';
        if(!rows.length){
            return '<tr><td colspan="7" style="color:#6B7280;">Nenhum registro encontrado para os filtros informados.</td></tr>';
        }
        rows.forEach(function(r){
            var sit = _deadlineSituation(r.deadline_raw, r.status_raw, '__REF_DATE__');
            var mat = String(r.matricula || '').trim() || '—';
            var nome = String(r.nome_agente || '').trim();
            var matHtml = '<span class="mat-val">' + _escHtml(mat) + '</span>';
            if(nome){
                matHtml += '<div class="mat-tip"><div class="ttl">Nome do agente</div><div class="sub">' + _escHtml(nome) + '</div></div>';
            }
            var sitKey = _situationKey(sit);
            var sitClass = sitKey === 'finalizado' ? 'situacao-finalizado' : (sitKey === 'pendente' ? 'situacao-pendente' : (sitKey === 'previsto' ? 'situacao-previsto' : (sitKey === 'vencido' ? 'situacao-vencido' : (sitKey === 'avencer' ? 'situacao-avencer' : ''))));
            html += '<tr>'
                + '<td class="mat-hover-cell"' + (nome ? ' title="Nome do agente: ' + _escHtml(nome) + '"' : '') + '>' + matHtml + '</td>'
                + '<td>' + _escHtml(r.assignment_date || '—') + '</td>'
                + '<td>' + _escHtml(r.event_title || '—') + '</td>'
                + '<td>' + _escHtml(r.status || '—') + '</td>'
                + '<td>' + _escHtml(r.deadline || '—') + '</td>'
                + '<td class="situacao-cell ' + sitClass + '">' + _escHtml(sit || '—') + '</td>'
                + '<td>' + _escHtml(r.localidade || 'Localidade não identificada') + '</td>'
                + '</tr>';
        });
        return html;
    }
    function _sortConsultaRows(rows){
        return rows.slice().sort(function(a, b){
            return _parseDateSortValue(b.assignment_raw) - _parseDateSortValue(a.assignment_raw);
        });
    }
    function ordenarTreinConsultaPorData(){
        var mat = document.getElementById('trein-matricula');
        var hint = document.getElementById('trein-consulta-hint');
        if(!mat || !String(mat.value || '').trim()){
            if(hint){ hint.textContent = 'Informe uma matrícula antes de ordenar por data.'; }
            return;
        }
        setTreinamentoConsulta(true);
        if(hint){ hint.textContent = 'Consulta ordenada da menor para a maior data de atribuição.'; }
    }
    function setTreinamentoConsulta(){
        var ordenarCrescente = arguments.length > 0 && !!arguments[0];
        var mat = document.getElementById('trein-matricula');
        var ini = document.getElementById('trein-data-inicial');
        var fim = document.getElementById('trein-data-final');
        var st = document.getElementById('trein-status');
        var out = document.getElementById('trein-consulta-result');
        var hint = document.getElementById('trein-consulta-hint');
        if(!mat || !out){ return; }
        var matVal = String(mat.value || '').trim();
        if(!matVal){
            out.style.display = 'none';
            out.innerHTML = '';
            if(hint){ hint.textContent = 'Informe a matrícula para visualizar apenas os treinamentos do agente selecionado.'; }
            return;
        }
        var matNorm = matVal.replace(/\\s+/g, '').toLowerCase();
        var localidade = String(MATRICULA_LOCALIDADE_MAP[matNorm] || '').trim();
        // If report scope enforces a single BU, require matricula to belong to that BU
        if(ALLOWED_LOCAL){
            if(!localidade){
                // unknown locality -> block
                out.style.display = 'block';
                out.innerHTML = '<div class="card" style="overflow:visible;"><div style="font-weight:900;margin:0 0 6px;font-size:13px;">Consulta bloqueada</div><div style="color:#6B7280;font-size:12px;">Matrícula sem localidade identificada no HC. Consulte o HC local antes.</div></div>';
                if(hint){ hint.textContent = 'Localidade não identificada para a matrícula informada.'; }
                return;
            }
            if(String(localidade).toLowerCase() !== String(ALLOWED_LOCAL).toLowerCase()){
                out.style.display = 'block';
                out.innerHTML = '<div class="card"><div style="font-weight:900;margin:0 0 6px;font-size:13px;">Consulta bloqueada</div><div style="color:#6B7280;font-size:12px;">A matrícula informada pertence a ' + localidade + '. Este relatório é do local ' + ALLOWED_LOCAL + '.</div></div>';
                if(hint){ hint.textContent = 'Matrícula pertence a outra localidade. Consulta não permitida neste relatório.'; }
                return;
            }
        }
        var sourceData = [];
        if(localidade === 'Brasília'){
            sourceData = TREINAMENTOS_DATA_BRASILIA;
        }else if(localidade === 'São Carlos'){
            sourceData = TREINAMENTOS_DATA_SAOCARLOS;
        }else{
            sourceData = [];
        }
        var iniVal = _normDateValue(ini ? ini.value : '');
        var fimVal = _normDateValue(fim ? fim.value : '');
        var statusVal = String(st ? st.value : '');
        var rows = sourceData.filter(function(r){
            var rowMat = String(r.matricula || '').replace(/\\s+/g, '').toLowerCase();
            if(rowMat !== matNorm && String(r.mat_norm || '').replace(/\\s+/g, '').toLowerCase() !== matNorm){ return false; }
            var d = _normDateValue(r.assignment_raw);
            if(iniVal && (!d || d < iniVal)){ return false; }
            if(fimVal && (!d || d > fimVal)){ return false; }
            if(statusVal && String(r.status || '').indexOf(statusVal) !== 0){ return false; }
            return true;
        });
        if(ordenarCrescente){
            rows = _sortConsultaRows(rows);
        }
        var counts = _countSituations(rows);
        var html = '';
        if(!localidade){
            out.style.display = 'block';
            out.innerHTML = '<div class="card"><div style="font-weight:900;margin:0 0 6px;font-size:13px;">Resultado da consulta</div><div style="color:#6B7280;font-size:12px;">Matrícula sem localidade identificada no HC. A consulta foi bloqueada para evitar mistura entre Brasília e São Carlos.</div></div>';
            if(hint){ hint.textContent = 'Localidade não identificada para a matrícula informada.'; }
            return;
        }
        html += '<div class="card" style="overflow:visible;position:relative;"><div style="font-weight:900;margin:0 0 6px;font-size:13px;">Resultado da consulta</div>'
            + '<div style="font-size:12px;color:#6B7280;margin-bottom:8px;">Localidade: ' + localidade + '</div>'
            + _renderConsultaResumo(counts)
            + '<table role="presentation" cellspacing="0" cellpadding="0">'
            + '<thead><tr><th>Matrícula do agente</th><th>Data de atribuição</th><th>Título do treinamento</th><th>Status</th><th>Prazo do treinamento</th><th>Situação</th><th>Localidade</th></tr></thead>'
            + '<tbody>' + _renderConsultaTable(rows) + '</tbody></table></div>';
        out.innerHTML = html;
        out.style.display = 'block';
        if(hint){ hint.textContent = 'Foram encontrados ' + rows.length + ' registro(s) para a matrícula informada.'; }
    }
    function limparTreinConsulta(){
        ['trein-matricula','trein-data-inicial','trein-data-final'].forEach(function(id){ var el = document.getElementById(id); if(el){ el.value = ''; } });
        var st = document.getElementById('trein-status');
        if(st){ st.value = ''; }
        var out = document.getElementById('trein-consulta-result');
        if(out){ out.style.display = 'none'; out.innerHTML = ''; }
        var hint = document.getElementById('trein-consulta-hint');
        if(hint){ hint.textContent = 'Informe a matrícula para visualizar apenas os treinamentos do agente selecionado.'; }
    }
    var TREIN_PREV_PAGE_SIZE = 20;
    var treinPrevisaoShown = { vencidos: TREIN_PREV_PAGE_SIZE, mes: TREIN_PREV_PAGE_SIZE };
    function treinPrevisaoSetView(which){
        var secV = document.getElementById('trein-sec-vencidos');
        var secM = document.getElementById('trein-sec-mes');
        var kpiV = document.getElementById('trein-kpi-vencidos');
        var kpiM = document.getElementById('trein-kpi-mes');
        if(!secV || !secM){ return; }
        if(which === 'mes'){
            secV.classList.add('trein-prev-sec-hidden');
            secM.classList.remove('trein-prev-sec-hidden');
            if(kpiV){ kpiV.classList.remove('trein-prev-kpi-active'); }
            if(kpiM){ kpiM.classList.add('trein-prev-kpi-active'); }
        }else{
            secM.classList.add('trein-prev-sec-hidden');
            secV.classList.remove('trein-prev-sec-hidden');
            if(kpiM){ kpiM.classList.remove('trein-prev-kpi-active'); }
            if(kpiV){ kpiV.classList.add('trein-prev-kpi-active'); }
        }
    }
    function treinPrevisaoShowMore(which){
        var rows = document.querySelectorAll('.trein-prev-row.trein-prev-' + which + '.trein-prev-row-hidden');
        var step = TREIN_PREV_PAGE_SIZE;
        var shown = 0;
        for(var i = 0; i < rows.length && shown < step; i++){
            rows[i].classList.remove('trein-prev-row-hidden');
            shown++;
        }
        treinPrevisaoShown[which] = (treinPrevisaoShown[which] || TREIN_PREV_PAGE_SIZE) + shown;
        var left = document.querySelectorAll('.trein-prev-row.trein-prev-' + which + '.trein-prev-row-hidden').length;
        var btn = document.getElementById('trein-more-' + which);
        if(btn){
            if(left > 0){
                btn.style.display = '';
                btn.textContent = 'Ver mais (' + left + ' restante(s))';
            }else{
                btn.style.display = 'none';
            }
        }
    }
    </script>
    """.replace('__DATA__', dataset_js).replace('__DATA_BRASILIA__', dataset_brasilia_js).replace('__DATA_SAOCARLOS__', dataset_saocarlos_js).replace('__MAT_MAP__', matricula_localidade_js).replace('__ALLOWED_LOCAL__', allowed_local_js).replace('__REF_DATE__', ref_date.strftime('%Y-%m-%d'))

    body = info_html + query_block + previsoes_block + js

    title = "Acompanhamento de Treinamentos"
    if safe_str(scope_name):
        title += f" - {safe_str(scope_name)}"
    return wrap_simple_page(title, body)


