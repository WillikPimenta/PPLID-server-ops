# -*- coding: utf-8 -*-
"""Orquestracao legada do report por e-mail."""
import re
import time
from datetime import datetime, date
from pathlib import Path

import numpy as np
import pandas as pd

from report_falhas.io.data_loader import (
    safe_str, normalize_text, safe_to_datetime,
    norm_matricula, norm_protocolo,
    read_hc, read_suporte, read_treinamentos,
    slug,
)
from report_falhas.assets import resolve_logo_data_uri
from report_falhas.config_report import (
    ABA_BASE, ABA_HC, ABA_SUPORTE, EMAIL_EMBED_RECIPIENTS_IN_EML, EMAIL_FROM,
    require_excel_path,
    resolve_output_dir, resolve_email_from, resolve_scope_output_dir,
    resolve_email_recipients, should_skip_email_preview,
)
import report_falhas.config_report as cfg_report
from report_falhas.data_base import read_base as _read_base_mod, build_maps_nome_e_tempos as _build_maps_mod
from report_falhas.display_config import (
    INCLUIR_GRAFICO_DIARIO,
    TOP_CENARIOS_POR_GRUPO, TOP_TIPOS_MATRIX, TOP_TIPOS_TRIPLET, TOP_UFS_MATRIX, TOP_UFS_TRIPLET,
)
from report_falhas.filters import LOCALIDADES_ALVO, aplicar_recorte_oficial, filtrar_por_localidades, uses_dual_metric_mode
from report_falhas.hc_maps import (
    build_atividade_atual_hc_map,
    build_team_categoria_map,
    build_turno_hc_map,
    classify_fn_fp_from_novo_cenario,
)
from report_falhas.periods import (
    april_start, effective_audit_end, filter_by_date_range_on, first_day,
    get_comparativo_by_same_period_on,
    get_comparativo_by_same_period as _get_comparativo_by_same_period_col,
    get_reincidence_periods, is_fechamento_mes, mask_spill_auditoria, month_last_day,
    months_from_to, prev_month_first_day,
    resolve_mtd_period,
    filter_by_date_range as _filter_by_date_range_col,
)
from report_falhas.render.mailer import (
    preview_new_outlook,
    write_recipients_sidecar,
)
from report_falhas import charts_turno as _charts_turno_mod
from report_falhas.html_main import format_html as _format_html_mod
from report_falhas.html_pages import (
    build_client_workflow_page_html, build_contestacoes_page_html,
    build_falhas_retiradas_page_html, build_falhas_removidas_page_html,
    build_suporte_page_html,
    build_tabs_index_html, build_treinamentos_tracking_page_html,
    build_ult3m_agent_table, build_ult3m_training_focus_html,
    format_html_ultimos_3_meses,
    inject_email_intro,
)
from report_falhas.contestacoes import get_contest_state
from report_falhas.email_copy import build_executive_email_body, build_executive_email_subject
from report_falhas.falhas_removidas import get_removidas_state, build_removidas_email_blurb
from report_falhas.excel_exporter import (
    compute_protocol_snapshot,
    diff_protocol_snapshots,
    export_protocol_diff_xlsx,
    export_reincidencia_xlsx as _export_reincidencia_xlsx_mod,
    load_protocol_snapshot,
    save_protocol_snapshot,
)
from report_falhas.matricula_utils import normalize_dificuldade as _normalize_dificuldade
from report_falhas.reincidence import reincidencia_table_full as _reincidencia_table_full_mod, novos_no_mes as _novos_no_mes_mod
from report_falhas.kpis import make_kpis as _make_kpis_mod
from report_falhas.insights import gerar_insights as _gerar_insights_mod
from report_falhas.analytics import (
    MIN_COUNT_RANK, TOP_CENARIOS_CRESCIMENTO, TOP_CENARIOS_IMPACTO, TOP_UF_FF,
    build_quarterly_counts, top_dimension_items,
    build_matrix_tipo_x_uf, build_top_scenarios_by_dimension, build_rank_delta,
)

COL_DATA = cfg_report.COL_DATA
COL_DATA_ANALISE = cfg_report.COL_DATA_ANALISE
COL_DATA_AUDITORIA = cfg_report.COL_DATA_AUDITORIA
COL_MATRICULA = cfg_report.COL_MATRICULA
COL_CENARIO = cfg_report.COL_CENARIO
COL_ETAPA = 'Etapa'
COL_LIDER = 'Líder'
COL_PROTOCOLO = cfg_report.COL_PROTOCOLO
COL_NOME_BASE = 'Nome Agente'
COL_DIFICULDADE = 'Nível de Dificuldade'
COL_LOCALIDADE = 'Localidade'
COL_TIPO_DOCUMENTO = 'Tipo de documento'
COL_UF_DOCUMENTO = 'UF do documento'
COL_MODULO = 'Módulo'
USE_CORE_HTML_TOTAL_OFICIAL = False

try:
    from report_falhas.pipeline import build_report as _build_report_core
    _HAS_REPORT_FALHAS_CORE = True
except Exception:
    _build_report_core = None
    _HAS_REPORT_FALHAS_CORE = False

TAB_ULT3M_TOP_N_AGENTES = 15
TAB_ULT3M_TOP_N_CENARIOS = 3
TAB_ULT3M_TOP_N_UF_POR_DOC = 3
TAB_ULT3M_TOP_N_DOCS_NO_RESUMO = 6

make_kpis = _make_kpis_mod
gerar_insights = _gerar_insights_mod
reincidencia_table_full = _reincidencia_table_full_mod
novos_no_mes = _novos_no_mes_mod
export_reincidencia_xlsx = _export_reincidencia_xlsx_mod
format_html = _format_html_mod
read_base = _read_base_mod
build_maps_nome_e_tempos = _build_maps_mod
chart_falhas_por_turno_base64 = _charts_turno_mod.chart_falhas_por_turno_base64
build_agentes_ativos_por_turno = _charts_turno_mod.build_agentes_ativos_por_turno
build_falhas_por_agente_turno = _charts_turno_mod.build_falhas_por_agente_turno
chart_falhas_por_agente_turno_base64 = _charts_turno_mod.chart_falhas_por_agente_turno_base64
render_falhas_por_agente_turno_table = _charts_turno_mod.render_falhas_por_agente_turno_table
chart_barras_base64_custom = _charts_turno_mod.chart_barras_base64_custom
chart_linha_base64 = _charts_turno_mod.chart_linha_base64


def filtro_facil(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or COL_DIFICULDADE not in df.columns:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()
    s = df[COL_DIFICULDADE].apply(safe_str).apply(normalize_text)
    return df[s.str.contains('facil', na=False)].copy()


def build_dificuldade_counts_map(df_cur: pd.DataFrame) -> dict[str, str]:
    if df_cur is None or df_cur.empty:
        return {}
    if COL_MATRICULA not in df_cur.columns or COL_DIFICULDADE not in df_cur.columns:
        return {}
    tmp = df_cur.copy()
    tmp['mat_norm'] = tmp[COL_MATRICULA].apply(norm_matricula)
    if COL_PROTOCOLO in tmp.columns:
        tmp['_prot'] = tmp[COL_PROTOCOLO].apply(norm_protocolo)
    else:
        tmp['_prot'] = ''
    tmp['_dif_raw'] = tmp[COL_DIFICULDADE].apply(_normalize_dificuldade)
    tmp = tmp[(tmp['mat_norm'] != '') & (tmp['_dif_raw'] != '')].copy()
    if tmp.empty:
        return {}
    store: dict[str, dict[str, set[str]]] = {}
    for _, r in tmp.iterrows():
        mat = r['mat_norm']
        prot = safe_str(r.get('_prot', ''))
        if not prot:
            continue
        difs = [_normalize_dificuldade(d) for d in safe_str(r['_dif_raw']).split(',') if _normalize_dificuldade(d)]
        if not difs:
            continue
        dmap = store.setdefault(mat, {})
        for d in difs:
            dmap.setdefault(d, set()).add(prot)
    ordem = ['Fácil', 'Médio', 'Difícil']
    out: dict[str, str] = {}
    for mat, dmap in store.items():
        parts = []
        keys = list(dmap.keys())
        ordered = [k for k in ordem if k in keys] + [k for k in keys if k not in ordem]
        for k in ordered:
            n = len(dmap.get(k, set()))
            if n > 0:
                parts.append(f"{k} ({n})")
        out[mat] = ', '.join(parts)
    return out


def build_monthly_counts(df: pd.DataFrame, start_apr: date, cur_start: date, cur_end_mtd: date) -> list[tuple[str, int]]:
    from report_falhas.utils_report import fmt_mes_ano_br
    pares = []
    for mfirst in months_from_to(start_apr, cur_start):
        mend = month_last_day(mfirst)
        if mfirst.year == cur_start.year and mfirst.month == cur_start.month and cur_end_mtd:
            mend = cur_end_mtd
        qtd = len(_filter_by_date_range_col(df, mfirst, mend, COL_DATA))
        pares.append((fmt_mes_ano_br(mfirst), int(qtd)))
    return pares


def filter_by_date_range(df: pd.DataFrame, start_d: date, end_d: date) -> pd.DataFrame:
    return _filter_by_date_range_col(df, start_d, end_d, COL_DATA)


def get_comparativo_by_same_period(df: pd.DataFrame, cur_start: date, cur_end: date, prev_start: date):
    return _get_comparativo_by_same_period_col(df, cur_start, cur_end, prev_start, COL_DATA)


def _parse_mes_referencia(raw) -> date:
    """Converte mes_referencia (date, datetime ou 'MM/AAAA') em primeiro dia do mês."""
    if raw is None or raw == "":
        return date.today()
    if isinstance(raw, date):
        return raw.replace(day=1)
    if hasattr(raw, "date"):
        try:
            d = raw.date()
            return d.replace(day=1)
        except Exception:
            pass
    s = str(raw).strip().replace("-", "/")
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)
    if m:
        mes, ano = int(m.group(1)), int(m.group(2))
        return date(ano, mes, 1)
    m2 = re.fullmatch(r"(\d{2})(\d{4})", s)
    if m2:
        return date(int(m2.group(2)), int(m2.group(1)), 1)
    return date.today()


def _emit_pipeline_status(msg: str) -> None:
    """Emite STATUS| para o painel de logs do bot (stdout do runner)."""
    try:
        print(f"STATUS|{msg}", flush=True)
    except Exception:
        pass


def _coerce_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("s", "sim", "y", "yes", "1", "true", "on"):
            return True
        if v in ("n", "nao", "não", "no", "0", "false", "off"):
            return False
    return bool(value)


def _display_falhas_path(path: str | Path) -> str:
    """Caminho relativo a partir da biblioteca OneDrive (para exibir no portal)."""
    s = str(path).replace("/", "\\")
    for marker in (
        "Planejamento - IDF - Bots",
        r"Planejamento - IDF - Bases\Bots",
    ):
        idx = s.lower().find(marker.lower())
        if idx >= 0:
            return s[idx:]
    marker2 = "report-falhas-criticas"
    idx = s.lower().find(marker2)
    if idx >= 0:
        return s[idx:]
    return s


def _scope_output_entry(scope_name: str, path: Path) -> dict[str, str]:
    resolved = str(path.resolve())
    return {
        "scope": scope_name,
        "path": resolved,
        "display": _display_falhas_path(resolved),
    }


def run_report(settings: dict | None = None) -> dict | None:
    """Gera relatórios HTML/Outlook sem prompts interativos (uso pelo bot)."""
    settings = dict(settings or {})
    import report_falhas.config_report as cfg_report

    excel_path = (settings.get("falhas_excel_path") or settings.get("excel_path") or "").strip()
    if excel_path:
        cfg_report.EXCEL_PATH = excel_path
    output_dir = (settings.get("output_dir") or "").strip()
    if output_dir:
        cfg_report.OUTPUT_DIR = output_dir
    email_from = (settings.get("email_from") or "").strip()
    if email_from:
        cfg_report.EMAIL_FROM = email_from
    return _legacy_main_impl(
        settings={
            "mes_referencia": settings.get("mes_referencia"),
            "gerar_consolidado": _coerce_bool(settings.get("gerar_consolidado"), True),
            "usar_periodo_custom": _coerce_bool(settings.get("usar_periodo_custom"), False),
            "custom_start": settings.get("custom_start"),
            "custom_end": settings.get("custom_end"),
            "preview_email": _coerce_bool(settings.get("preview_email"), True),
            "email_from": email_from or None,
            "gerar_executivo": _coerce_bool(settings.get("gerar_executivo"), False),
            "salvar_html_individuais": _coerce_bool(settings.get("salvar_html_individuais"), False),
        },
    )


def _legacy_main_impl(prompt_bool_fn=None, parse_date_br_fn=None, parse_mes_ano_input_fn=None, *, settings: dict | None = None):
    if settings is not None:
        hoje = _parse_mes_referencia(settings.get("mes_referencia"))
        gerar_consolidado = _coerce_bool(settings.get("gerar_consolidado"), True)
        usar_periodo_custom = _coerce_bool(settings.get("usar_periodo_custom"), False)
        custom_start = custom_end = None
        if usar_periodo_custom:
            try:
                cs = settings.get("custom_start")
                ce = settings.get("custom_end")
                if cs and ce:
                    if isinstance(cs, str):
                        custom_start = parse_date_br_fn(cs) if parse_date_br_fn else datetime.strptime(cs, "%d/%m/%Y").date()
                    else:
                        custom_start = cs
                    if isinstance(ce, str):
                        custom_end = parse_date_br_fn(ce) if parse_date_br_fn else datetime.strptime(ce, "%d/%m/%Y").date()
                    else:
                        custom_end = ce
                    if custom_end < custom_start:
                        custom_start, custom_end = custom_end, custom_start
                else:
                    usar_periodo_custom = False
            except Exception as e:
                print("Período inválido. Usando mês atual. Erro:", e)
                usar_periodo_custom = False
                custom_start = custom_end = None
        preview_email = _coerce_bool(settings.get("preview_email"), True)
        email_from = settings.get("email_from") or resolve_email_from()
        gerar_executivo = _coerce_bool(settings.get("gerar_executivo"), False)
        salvar_html_individuais = _coerce_bool(settings.get("salvar_html_individuais"), False)
        if gerar_executivo:
            print("📊 Opção ativa: relatório executivo + e-mail gerência (último no Outlook)")
            _emit_pipeline_status("Relatório executivo: opção ativa — será gerado por último")
        else:
            _emit_pipeline_status(
                "Relatório executivo: desativado na config "
                "(marque Executivo comparativo no bot)"
            )
    else:
        hoje = parse_mes_ano_input_fn()
        gerar_consolidado = prompt_bool_fn('Gerar também o consolidado (Brasília + São Carlos)? (S/N): ')
        usar_periodo_custom = prompt_bool_fn('Deseja informar um período personalizado (Data de Análise)? (S/N): ')
        custom_start = custom_end = None
        if usar_periodo_custom:
            try:
                custom_start = parse_date_br_fn(input('Data inicial (DD/MM/AAAA): ').strip())
                custom_end = parse_date_br_fn(input('Data final (DD/MM/AAAA): ').strip())
                if custom_end < custom_start:
                    custom_start, custom_end = custom_end, custom_start
            except Exception as e:
                print('Período inválido. Usando mês atual. Erro:', e)
                usar_periodo_custom = False
                custom_start = custom_end = None

        preview_email = True
        email_from = resolve_email_from(prompt_fn=input)
        gerar_executivo = prompt_bool_fn('Gerar relatório executivo comparativo (Brasília x São Carlos) para a gerência? (S = sim / Enter = não): ')
        salvar_html_individuais = False

    excel_path = require_excel_path()
    df_base = read_base(excel_path, ABA_BASE)
    global COL_CENARIO
    COL_CENARIO = cfg_report.COL_CENARIO
    # Snapshot e diff de protocolos (para identificar protocolos que sumiram)
    out_dir = resolve_output_dir()
    prev_snap = load_protocol_snapshot(out_dir)
    cur_snap = compute_protocol_snapshot(df_base)
    proto_diff_global = diff_protocol_snapshots(prev_snap, cur_snap)
    # salva snapshot atual para próxima execução
    save_protocol_snapshot(out_dir, cur_snap)

    # Export interno: divergência de protocolos (não aparece no HTML nem vai para líderes)
    try:
        has_any_diff = bool((proto_diff_global.get('missing_protocols') or []) or (proto_diff_global.get('added_protocols') or []) or (proto_diff_global.get('changed_counts') or []))
        if has_any_diff:
            ts_snap = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            diff_xlsx = out_dir / f'_ACOMP_divergencia_protocolos_{ts_snap}.xlsx'
            export_protocol_diff_xlsx(proto_diff_global, diff_xlsx)
    except Exception:
        pass

    try:
        if proto_diff_global.get('missing_protocols'):
            ts_snap = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            backup_path = out_dir / f'_backup_base_{ts_snap}.xlsx'
            # salva uma cópia da Base atual (para auditoria rápida)
            try:
                with pd.ExcelWriter(backup_path, engine='openpyxl') as w:
                    df_base.to_excel(w, sheet_name='Base', index=False)
            except Exception:
                pass
    except Exception:
        pass

    # Export interno: log de falhas removidas (aba SharePoint)
    try:
        resumo_rem, detalhe_rem, _ = get_removidas_state()
        if (detalhe_rem is not None and not detalhe_rem.empty) or (
            resumo_rem is not None and not resumo_rem.empty
            and int(resumo_rem.iloc[0].get("Registros log", 0) or 0) > 0
        ):
            ts_rem = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            rem_xlsx = out_dir / f'_ACOMP_falhas_removidas_{ts_rem}.xlsx'
            with pd.ExcelWriter(rem_xlsx, engine='openpyxl') as w:
                if resumo_rem is not None and not resumo_rem.empty:
                    resumo_rem.to_excel(w, sheet_name='Resumo', index=False)
                if detalhe_rem is not None and not detalhe_rem.empty:
                    detalhe_rem.to_excel(w, sheet_name='Detalhe', index=False)
            print(f"✅ XLSX FALHAS REMOVIDAS (log): {rem_xlsx}")
    except Exception:
        pass

    df_hc = read_hc(excel_path, ABA_HC)
    trein_path = excel_path
    try:
        if Path(excel_path).name.lower() != 'falhas_criticas_manual.xlsx':
            alt_path = Path(excel_path).parent / 'FALHAS_CRITICAS_MANUAL.xlsx'
            if alt_path.exists():
                trein_path = str(alt_path)
    except Exception:
        trein_path = excel_path
    df_hc_trein = read_hc(trein_path, ABA_HC)
    df_trein = read_treinamentos(trein_path, 'Treinamentos')

    if 'localidade' in df_hc.columns:
        df_hc['localidade_norm'] = df_hc['localidade'].apply(normalize_text)
    else:
        df_hc['localidade_norm'] = ''

    out_dir = resolve_output_dir()
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M')

    def hc_por_mats(mats: set[str], df_hc_local: pd.DataFrame):
        if df_hc_local is None or df_hc_local.empty or 'matricula_agente' not in df_hc_local.columns:
            return df_hc_local.iloc[0:0].copy() if df_hc_local is not None else pd.DataFrame()
        mats_norm = {norm_matricula(m) for m in mats if norm_matricula(m)}
        if not mats_norm:
            return df_hc_local.iloc[0:0].copy()
        if 'mat_norm' in df_hc_local.columns:
            return df_hc_local[df_hc_local['mat_norm'].isin(mats_norm)].copy()
        return df_hc_local[df_hc_local['matricula_agente'].apply(norm_matricula).isin(mats_norm)].copy()

    def periodo_atual():
        if usar_periodo_custom and custom_start and custom_end:
            return custom_start, custom_end
        return resolve_mtd_period(hoje)

    scope_outputs: list[dict[str, str]] = []

    def gerar_relatorio(scope_name: str, df_scope: pd.DataFrame):
        # Filtrar HC pela mesma localidade de df_scope
        df_hc_local = df_hc.copy() if not df_hc.empty else df_hc
        hc_localidade_col = None
        for c in df_hc_local.columns:
            if normalize_text(c) == normalize_text(COL_LOCALIDADE):
                hc_localidade_col = c
                break

        if COL_LOCALIDADE in df_scope.columns and hc_localidade_col is not None:
            # Obter localidade normalizada de df_scope
            localidades_scope = set(df_scope[COL_LOCALIDADE].apply(normalize_text).dropna().unique())
            if localidades_scope:
                df_hc_local['localidade_norm'] = df_hc_local[hc_localidade_col].apply(normalize_text)
                mask = df_hc_local['localidade_norm'].isin(localidades_scope)
                df_hc_local = df_hc_local[mask].copy()
        
        cur_start, cur_end = periodo_atual()
        dual_metric_mode = uses_dual_metric_mode(cur_start)
        _metric_suffix = " (Métrica Oficial)" if dual_metric_mode else ""
        today_real = date.today()
        ref_month = first_day(hoje)
        fechamento_mes = is_fechamento_mes(ref_month, cur_start, cur_end, today_real)
        eff_audit_end = effective_audit_end(cur_start, cur_end, ref_month, today_real)
        scope_out = resolve_scope_output_dir(out_dir, scope_name, ref_date=cur_end)
        scope_outputs.append(_scope_output_entry(scope_name, scope_out))
        print(f"📁 Saída ({scope_name}): {scope_out}")
        fy_label = f"FY{(cur_end.year + 1) if cur_end.month >= 4 else cur_end.year}"
        prev_month_start = prev_month_first_day(cur_start)

        # Reincidência: mês anterior cheio vs período atual
        prev_start_full, prev_end_full, _, _ = get_reincidence_periods(cur_end)
        df_prev_reinc = filter_by_date_range(df_scope, prev_start_full, prev_end_full)
        df_cur_reinc = filter_by_date_range(df_scope, cur_start, cur_end)
        df_reinc_full = reincidencia_table_full(df_prev_reinc, df_cur_reinc)

        df_scope_of = aplicar_recorte_oficial(df_scope, cur_start)
        df_prev_reinc_of = filter_by_date_range(df_scope_of, prev_start_full, prev_end_full)
        df_cur_reinc_of = filter_by_date_range(df_scope_of, cur_start, cur_end)
        # Gráfico de falhas por turno (mês atual) - para HTML (Métrica Oficial)
        try:
            b64_turno_of, txt_turno_of = chart_falhas_por_turno_base64(df_cur_reinc_of, turno_map, titulo=f'Falhas por turno - mês atual{_metric_suffix}')
        except Exception:
            b64_turno_of, txt_turno_of = None, ''
        df_reinc_full_of = reincidencia_table_full(df_prev_reinc_of, df_cur_reinc_of)

        # Top 3 cenários
        top3_cenarios = []
        if COL_CENARIO in df_cur_reinc.columns and not df_cur_reinc.empty:
            vc = df_cur_reinc[COL_CENARIO].value_counts().head(3)
            top3_cenarios = [(c, int(q)) for c, q in vc.items()]
        top3_cenarios_of = []
        if COL_CENARIO in df_cur_reinc_of.columns and not df_cur_reinc_of.empty:
            vc = df_cur_reinc_of[COL_CENARIO].value_counts().head(3)
            top3_cenarios_of = [(c, int(q)) for c, q in vc.items()]

        # Etapa/dificuldade maps
        etapa_map = {}
        dificuldade_map = {}
        if not df_cur_reinc.empty and (COL_ETAPA in df_cur_reinc.columns):
            etapa_map = (df_cur_reinc.dropna(subset=[COL_MATRICULA, COL_ETAPA])
                         .groupby(COL_MATRICULA)[COL_ETAPA]
                         .apply(lambda s: ', '.join(sorted(pd.Series(s).apply(safe_str).unique())))
                         .to_dict())
        if not df_cur_reinc.empty and (COL_DIFICULDADE in df_cur_reinc.columns):
            dificuldade_map = (df_cur_reinc.dropna(subset=[COL_MATRICULA, COL_DIFICULDADE])
                               .groupby(COL_MATRICULA)[COL_DIFICULDADE]
                               .apply(lambda s: ', '.join(sorted(pd.Series(s).apply(safe_str).unique())))
                               .to_dict())

        novos_mes_list = novos_no_mes(df_prev_reinc, df_cur_reinc)
        novos_mes_list_of = novos_no_mes(df_prev_reinc_of, df_cur_reinc_of)

        # Contexto
        n_dias_falha = df_cur_reinc[COL_DATA].dt.date.nunique() if not df_cur_reinc.empty else 0
        dias_corridos = (cur_end - cur_start).days + 1
        dias_sem_falha = max(dias_corridos - n_dias_falha, 0)

        # Comparativo: mesmos dias corridos de calendário no mês anterior
        total_atual, total_prev_equal, prev_equal_start, prev_equal_end = get_comparativo_by_same_period(df_scope, cur_start, cur_end, prev_month_start)
        total_atual_of, total_prev_equal_of, prev_equal_start_of, prev_equal_end_of = get_comparativo_by_same_period(df_scope_of, cur_start, cur_end, prev_month_start)


        # ===== Treinamento: comparativo de cenários (geral) e recorte Formatação/Fonte =====
        # Objetivo: na página OFICIAL usar somente Métrica Oficial; na página TOTAL usar todas as falhas.
        def _calc_treinamento(df_cur: pd.DataFrame, df_scope_base: pd.DataFrame, prev_s: date, prev_e: date, scenario_col: str):
            # Calcula rankings de treinamento (geral e Formatação/Fonte) para um recorte.
            try:
                df_prev_equiv = filter_by_date_range(df_scope_base, prev_s, prev_e) if (prev_s and prev_e) else df_scope_base.iloc[0:0].copy()
                scen_geral_local = build_rank_delta(
                    df_cur, df_prev_equiv, scenario_col,
                    top_impact=TOP_CENARIOS_IMPACTO,
                    top_growth=TOP_CENARIOS_CRESCIMENTO,
                    min_count=MIN_COUNT_RANK,
                )
                df_cur_ff = df_cur[df_cur[scenario_col].apply(is_formatacao_fonte)].copy() if (scenario_col in df_cur.columns) else df_cur.iloc[0:0].copy()
                df_prev_ff = df_prev_equiv[df_prev_equiv[scenario_col].apply(is_formatacao_fonte)].copy() if (df_prev_equiv is not None and scenario_col in df_prev_equiv.columns) else (df_prev_equiv.iloc[0:0].copy() if df_prev_equiv is not None else pd.DataFrame())
                uf_ff_local = build_rank_delta(
                    df_cur_ff, df_prev_ff, COL_UF_DOCUMENTO,
                    top_impact=TOP_UF_FF,
                    top_growth=TOP_UF_FF,
                    min_count=MIN_COUNT_RANK,
                )
                # Tipo | UF (substitui o card de cenários no recorte Formatação/Fonte)
                try:
                    cur_ff = df_cur_ff.copy()
                    prev_ff = df_prev_ff.copy() if df_prev_ff is not None else df_prev_ff
                    if (COL_TIPO_DOCUMENTO in cur_ff.columns) and (COL_UF_DOCUMENTO in cur_ff.columns):
                        cur_ff['_tipo_uf'] = cur_ff[COL_TIPO_DOCUMENTO].apply(safe_str) + ' | ' + cur_ff[COL_UF_DOCUMENTO].apply(safe_str)
                    else:
                        cur_ff['_tipo_uf'] = ''
                    if (prev_ff is not None) and (not prev_ff.empty) and (COL_TIPO_DOCUMENTO in prev_ff.columns) and (COL_UF_DOCUMENTO in prev_ff.columns):
                        prev_ff['_tipo_uf'] = prev_ff[COL_TIPO_DOCUMENTO].apply(safe_str) + ' | ' + prev_ff[COL_UF_DOCUMENTO].apply(safe_str)
                    elif prev_ff is not None:
                        prev_ff['_tipo_uf'] = ''
                    tipo_uf_local = build_rank_delta(
                        cur_ff, (prev_ff if prev_ff is not None else cur_ff.iloc[0:0].copy()), '_tipo_uf',
                        top_impact=TOP_CENARIOS_IMPACTO,
                        top_growth=TOP_CENARIOS_CRESCIMENTO,
                        min_count=MIN_COUNT_RANK,
                    )
                except Exception:
                    tipo_uf_local = {}
                return scen_geral_local, uf_ff_local, tipo_uf_local
            except Exception:
                return {}, {}, {}

        # --- TOTAL (todas as falhas) ---
        scenario_col_total = 'Novo cenário' if 'Novo cenário' in df_cur_reinc.columns else COL_CENARIO
        scen_geral_total, uf_ff_total, scen_ff_total = _calc_treinamento(df_cur_reinc, df_scope, prev_equal_start, prev_equal_end, scenario_col_total)

        # --- OFICIAL (Métrica Oficial) ---
        scenario_col_of = 'Novo cenário' if 'Novo cenário' in df_cur_reinc_of.columns else COL_CENARIO
        scen_geral_of, uf_ff_of, scen_ff_of = _calc_treinamento(df_cur_reinc_of, df_scope_of, prev_equal_start_of, prev_equal_end_of, scenario_col_of)

        # Texto do comparativo (datas) para Treinamento - TOTAL
        try:
            cur_end_comp_total = df_cur_reinc[COL_DATA].dt.date.max() if (df_cur_reinc is not None and (not df_cur_reinc.empty) and (COL_DATA in df_cur_reinc.columns)) else cur_end
            if prev_equal_start and prev_equal_end:
                comp_txt_treinamento_total = (
                    f"{cur_start.strftime('%d/%m')}→{pd.Timestamp(cur_end_comp_total).strftime('%d/%m')} "
                    f"vs {prev_equal_start.strftime('%d/%m')}→{prev_equal_end.strftime('%d/%m')} (mesmos dias de calendário)"
                )
            else:
                comp_txt_treinamento_total = 'Sem comparação disponível'
        except Exception:
            comp_txt_treinamento_total = 'Sem comparação disponível'

        # Texto do comparativo (datas) para Treinamento - OFICIAL
        try:
            cur_end_comp_of = df_cur_reinc_of[COL_DATA].dt.date.max() if (df_cur_reinc_of is not None and (not df_cur_reinc_of.empty) and (COL_DATA in df_cur_reinc_of.columns)) else cur_end
            if prev_equal_start_of and prev_equal_end_of:
                comp_txt_treinamento_of = (
                    f"{cur_start.strftime('%d/%m')}→{pd.Timestamp(cur_end_comp_of).strftime('%d/%m')} "
                    f"vs {prev_equal_start_of.strftime('%d/%m')}→{prev_equal_end_of.strftime('%d/%m')} (mesmos dias de calendário)"
                )
            else:
                comp_txt_treinamento_of = 'Sem comparação disponível'
        except Exception:
            comp_txt_treinamento_of = 'Sem comparação disponível'

# ===== Auditoria (Data Auditoria) - total no período + origem (do mês x outro período) =====
        try:
            df_aud_periodo = filter_by_date_range_on(df_scope, cur_start, eff_audit_end, COL_DATA_AUDITORIA)
            total_atual_aud = int(len(df_aud_periodo))
            if total_atual_aud > 0:
                mask_mesmo = (df_aud_periodo[COL_DATA].dt.year == cur_start.year) & (df_aud_periodo[COL_DATA].dt.month == cur_start.month)
                aud_mesmo_mes = int(mask_mesmo.sum())
                aud_outro_mes = int(total_atual_aud - aud_mesmo_mes)
            else:
                aud_mesmo_mes = 0
                aud_outro_mes = 0
        except Exception:
            total_atual_aud = 0
            aud_mesmo_mes = 0
            aud_outro_mes = 0

        try:
            df_aud_periodo_of = filter_by_date_range_on(df_scope_of, cur_start, eff_audit_end, COL_DATA_AUDITORIA)
            total_atual_aud_of = int(len(df_aud_periodo_of))
            if total_atual_aud_of > 0:
                mask_mesmo_of = (df_aud_periodo_of[COL_DATA].dt.year == cur_start.year) & (df_aud_periodo_of[COL_DATA].dt.month == cur_start.month)
                aud_mesmo_mes_of = int(mask_mesmo_of.sum())
                aud_outro_mes_of = int(total_atual_aud_of - aud_mesmo_mes_of)
            else:
                aud_mesmo_mes_of = 0
                aud_outro_mes_of = 0
        except Exception:
            total_atual_aud_of = 0
            aud_mesmo_mes_of = 0
            aud_outro_mes_of = 0

        # ===== Análises do mês que aparecem auditadas em outro período =====
        spill_df = pd.DataFrame()
        spill_df_of = pd.DataFrame()
        spill_count_prev = 0
        spill_count_outros = 0
        spill_count_prev_of = 0
        spill_count_outros_of = 0
        spill_breakdown = {}
        spill_breakdown_of = {}

        try:
            df_analise_periodo = filter_by_date_range_on(df_scope, cur_start, cur_end, COL_DATA_ANALISE)
            if not df_analise_periodo.empty and (COL_DATA_AUDITORIA in df_analise_periodo.columns):
                if not pd.api.types.is_datetime64_any_dtype(df_analise_periodo[COL_DATA_AUDITORIA]):
                    df_analise_periodo[COL_DATA_AUDITORIA] = safe_to_datetime(df_analise_periodo[COL_DATA_AUDITORIA])
                mask_spill = mask_spill_auditoria(
                    df_analise_periodo, COL_DATA_AUDITORIA, cur_start, cur_end, eff_audit_end,
                )
                spill_df = df_analise_periodo[mask_spill].copy()
                spill_count_outros = int(len(spill_df))
        except Exception:
            pass

        try:
            df_analise_periodo_of = filter_by_date_range_on(df_scope_of, cur_start, cur_end, COL_DATA_ANALISE)
            if not df_analise_periodo_of.empty and (COL_DATA_AUDITORIA in df_analise_periodo_of.columns):
                if not pd.api.types.is_datetime64_any_dtype(df_analise_periodo_of[COL_DATA_AUDITORIA]):
                    df_analise_periodo_of[COL_DATA_AUDITORIA] = safe_to_datetime(df_analise_periodo_of[COL_DATA_AUDITORIA])
                mask_spill_of = mask_spill_auditoria(
                    df_analise_periodo_of, COL_DATA_AUDITORIA, cur_start, cur_end, eff_audit_end,
                )
                spill_df_of = df_analise_periodo_of[mask_spill_of].copy()
                spill_count_outros_of = int(len(spill_df_of))
        except Exception:
            pass


        # Gráficos
        pares_meses = build_monthly_counts(df_scope, april_start(cur_end), cur_start, cur_end)
        b64_meses, txt_meses, slope_meses = chart_barras_base64_custom(
            pares_meses,
            f'Falhas - {fy_label}',
            min_points=3,
        )
        hist_meses = len([v for (_lbl, v) in (pares_meses or []) if not (isinstance(v, float) and np.isnan(v))])
        df_facil = filtro_facil(df_scope)
        mensais_facil = build_monthly_counts(df_facil if df_facil is not None else df_scope, april_start(cur_end), cur_start, cur_end)
        b64_meses_facil, txt_meses_facil, slope_facil = chart_barras_base64_custom(
            mensais_facil,
            f'Falhas fáceis - {fy_label}',
            min_points=3,
        )
        quarters_facil = build_quarterly_counts(df_facil if df_facil is not None else df_scope, april_start(cur_end), cur_end)
        b64_quarters_facil, txt_quarters_facil, _ = chart_barras_base64_custom(quarters_facil, f'Falhas fáceis por quarter ({fy_label})')

        pares_meses_of = build_monthly_counts(df_scope_of, april_start(cur_end), cur_start, cur_end)
        b64_meses_of, txt_meses_of, slope_meses_of = chart_barras_base64_custom(
            pares_meses_of,
            f'Falhas - {fy_label}{_metric_suffix}',
            min_points=3,
        )
        hist_meses_of = len([v for (_lbl, v) in (pares_meses_of or []) if not (isinstance(v, float) and np.isnan(v))])
        df_facil_of = filtro_facil(df_scope_of)
        mensais_facil_of = build_monthly_counts(df_facil_of if df_facil_of is not None else df_scope_of, april_start(cur_end), cur_start, cur_end)
        b64_meses_facil_of, txt_meses_facil_of, slope_facil_of = chart_barras_base64_custom(
            mensais_facil_of,
            f'Falhas fáceis - {fy_label}{_metric_suffix}',
            min_points=3,
        )
        quarters_facil_of = build_quarterly_counts(df_facil_of if df_facil_of is not None else df_scope_of, april_start(cur_end), cur_end)
        b64_quarters_facil_of, txt_quarters_facil_of, _ = chart_barras_base64_custom(quarters_facil_of, f'Falhas fáceis por quarter ({fy_label}){_metric_suffix}')

        b64_diario_of, txt_diario_of, slope_diario_of = None, '', None
        if INCLUIR_GRAFICO_DIARIO and not df_cur_reinc_of.empty:
            s_of = df_cur_reinc_of.groupby(df_cur_reinc_of[COL_DATA].dt.date).size().sort_index()
            diario_of = [(d.strftime('%d/%m'), int(v)) for d, v in s_of.items()]
            b64_diario_of, txt_diario_of, slope_diario_of = chart_linha_base64(diario_of) if diario_of else (None, 'Sem dados para análise de tendência.', None)

        b64_diario, txt_diario, slope_diario = None, '', None
        if INCLUIR_GRAFICO_DIARIO and not df_cur_reinc.empty:
            s = df_cur_reinc.groupby(df_cur_reinc[COL_DATA].dt.date).size().sort_index()
            diario = [(d.strftime('%d/%m'), int(v)) for d, v in s.items()]
            b64_diario, txt_diario, slope_diario = chart_linha_base64(diario) if diario else (None, 'Sem dados para análise de tendência.', None)

        # Visões Top/Matrix/Cenários
        docs_total = docs_fn = docs_fp = []
        ufs_total = ufs_fn = ufs_fp = []
        mat_tipo_uf_total = mat_tipo_uf_fn = mat_tipo_uf_fp = pd.DataFrame()
        scen_tipo_total = scen_tipo_fn = scen_tipo_fp = []
        scen_uf_total = scen_uf_fn = scen_uf_fp = []
        try:
            cenario_col = 'Novo cenário' if 'Novo cenário' in df_cur_reinc.columns else COL_CENARIO
            tmp = df_cur_reinc.copy(); tmp['__Classe__'] = tmp[cenario_col].apply(classify_fn_fp_from_novo_cenario)
            docs_total = top_dimension_items(tmp, COL_TIPO_DOCUMENTO, top_n=TOP_TIPOS_TRIPLET, cls_filter=None)
            docs_fn = top_dimension_items(tmp, COL_TIPO_DOCUMENTO, top_n=TOP_TIPOS_TRIPLET, cls_filter=CLS_FN)
            docs_fp = top_dimension_items(tmp, COL_TIPO_DOCUMENTO, top_n=TOP_TIPOS_TRIPLET, cls_filter=CLS_FP)
            ufs_total = top_dimension_items(tmp, COL_UF_DOCUMENTO, top_n=TOP_UFS_TRIPLET, cls_filter=None)
            ufs_fn = top_dimension_items(tmp, COL_UF_DOCUMENTO, top_n=TOP_UFS_TRIPLET, cls_filter=CLS_FN)
            ufs_fp = top_dimension_items(tmp, COL_UF_DOCUMENTO, top_n=TOP_UFS_TRIPLET, cls_filter=CLS_FP)
            mat_tipo_uf_total = build_matrix_tipo_x_uf(tmp, COL_TIPO_DOCUMENTO, COL_UF_DOCUMENTO, top_tipos=TOP_TIPOS_MATRIX, top_ufs=TOP_UFS_MATRIX, cls_filter=None)
            mat_tipo_uf_fn = build_matrix_tipo_x_uf(tmp, COL_TIPO_DOCUMENTO, COL_UF_DOCUMENTO, top_tipos=TOP_TIPOS_MATRIX, top_ufs=TOP_UFS_MATRIX, cls_filter=CLS_FN)
            mat_tipo_uf_fp = build_matrix_tipo_x_uf(tmp, COL_TIPO_DOCUMENTO, COL_UF_DOCUMENTO, top_tipos=TOP_TIPOS_MATRIX, top_ufs=TOP_UFS_MATRIX, cls_filter=CLS_FP)
            scen_tipo_total = build_top_scenarios_by_dimension(tmp, COL_TIPO_DOCUMENTO, cenario_col, top_dim=TOP_TIPOS_TRIPLET, top_scen=TOP_CENARIOS_POR_GRUPO, cls_filter=None)
            scen_tipo_fn = build_top_scenarios_by_dimension(tmp, COL_TIPO_DOCUMENTO, cenario_col, top_dim=TOP_TIPOS_TRIPLET, top_scen=TOP_CENARIOS_POR_GRUPO, cls_filter=CLS_FN)
            scen_tipo_fp = build_top_scenarios_by_dimension(tmp, COL_TIPO_DOCUMENTO, cenario_col, top_dim=TOP_TIPOS_TRIPLET, top_scen=TOP_CENARIOS_POR_GRUPO, cls_filter=CLS_FP)
            scen_uf_total = build_top_scenarios_by_dimension(tmp, COL_UF_DOCUMENTO, cenario_col, top_dim=TOP_UFS_MATRIX, top_scen=TOP_CENARIOS_POR_GRUPO, cls_filter=None)
            scen_uf_fn = build_top_scenarios_by_dimension(tmp, COL_UF_DOCUMENTO, cenario_col, top_dim=TOP_UFS_MATRIX, top_scen=TOP_CENARIOS_POR_GRUPO, cls_filter=CLS_FN)
            scen_uf_fp = build_top_scenarios_by_dimension(tmp, COL_UF_DOCUMENTO, cenario_col, top_dim=TOP_UFS_MATRIX, top_scen=TOP_CENARIOS_POR_GRUPO, cls_filter=CLS_FP)
        except Exception:
            pass

        insights = gerar_insights(txt_meses, txt_meses_facil, txt_diario, slope_meses, slope_facil, slope_diario, top3_cenarios)

        # Mapas
        mats = set(df_scope['mat_norm'].tolist()) if ('mat_norm' in df_scope.columns) else set(df_scope[COL_MATRICULA].apply(norm_matricula).tolist()) if not df_scope.empty else set()
        nome_map, tempo_casa_map, tempo_etapa_map = build_maps_nome_e_tempos(df_scope, hc_por_mats(mats, df_hc_local), df_cur_reinc, period_start=cur_start, period_end=cur_end)
        atividade_atual_hc_map = build_atividade_atual_hc_map(hc_por_mats(mats, df_hc_local))
        turno_map = build_turno_hc_map(hc_por_mats(mats, df_hc_local))
        team_categoria_map = build_team_categoria_map(hc_por_mats(mats, df_hc_local))
        agentes_turno_map, agentes_turno_total = build_agentes_ativos_por_turno(df_hc_local)
        falhas_por_agente_rows = build_falhas_por_agente_turno(df_cur_reinc, turno_map, agentes_turno_map)
        b64_falhas_por_agente_turno, txt_falhas_por_agente_turno = chart_falhas_por_agente_turno_base64(falhas_por_agente_rows)
        tab_falhas_por_agente_turno = render_falhas_por_agente_turno_table(falhas_por_agente_rows)
        # Gráfico HTML: volume de falhas por turno (Total e Oficial)
        b64_turno_total, txt_turno_total = chart_falhas_por_turno_base64(df_cur_reinc, turno_map, titulo='Volume de falhas por turno (Total)')
        b64_turno_of, txt_turno_of = chart_falhas_por_turno_base64(df_cur_reinc_of, turno_map, titulo=f'Volume de falhas por turno{_metric_suffix}')

        # Gráfico HTML: volume de falhas por turno - FY (acumulado)
        try:
            fy_start = april_start(cur_end)
            fy_label = fy_start.year + 1  # FYYYYY (ex.: 2025-04 -> FY2026)
            df_fy_total = filter_by_date_range(df_scope, fy_start, cur_end)
            b64_turno_fy_total, txt_turno_fy_total = chart_falhas_por_turno_base64(
                df_fy_total, turno_map,
                titulo=f'Volume de falhas por turno - FY{fy_label} (Total)'
            )
        except Exception:
            b64_turno_fy_total, txt_turno_fy_total = None, ''

        try:
            fy_start = april_start(cur_end)
            fy_label = fy_start.year + 1
            df_fy_of = filter_by_date_range(df_scope_of, fy_start, cur_end)
            b64_turno_fy_of, txt_turno_fy_of = chart_falhas_por_turno_base64(
                df_fy_of, turno_map,
                titulo=f'Volume de falhas por turno - FY{fy_label}{_metric_suffix}'
            )
        except Exception:
            b64_turno_fy_of, txt_turno_fy_of = None, ''
        dificuldade_counts_map = build_dificuldade_counts_map(df_cur_reinc)

        # KPIs
        kpis_total = make_kpis(cur_start, cur_end, total_atual, total_prev_equal, prev_equal_start, prev_equal_end, df_reinc_full, top3_cenarios, novos_mes_list)
        kpis_oficial = make_kpis(cur_start, cur_end, total_atual_of, total_prev_equal_of, prev_equal_start_of, prev_equal_end_of, df_reinc_full_of, top3_cenarios_of, novos_mes_list_of)

        # HTML TOTAL (somente até jun/2026 — dual Total + Oficial)
        html_total = None
        if dual_metric_mode:
            if _HAS_REPORT_FALHAS_CORE and USE_CORE_HTML_TOTAL_OFICIAL:
                try:
                    _pares_mes_core = build_monthly_counts(df_scope, april_start(cur_end), cur_start, cur_end)
                    _pares_diario_core = diario if 'diario' in locals() else None
                    _pares_facil_core = mensais_facil if 'mensais_facil' in locals() else None
                    _res_core = _build_report_core(
                        df_base=df_scope,
                        col_data=COL_DATA_ANALISE,
                        today=(cur_end or cur_start),
                        pares_mes=_pares_mes_core,
                        pares_diario=_pares_diario_core,
                        pares_facil=_pares_facil_core,
                        top3_cenarios=top3_cenarios,
                    )
                    html_total = _res_core.get('html')
                except Exception:
                    html_total = None
            if not html_total:
                html_total = format_html(
                    insights,
                    cur_start, cur_end, n_dias_falha, dias_corridos, dias_sem_falha,
                    prev_equal_start, prev_equal_end, total_atual, total_prev_equal,
                    df_reinc_full, tempo_etapa_map, tempo_casa_map, nome_map, atividade_atual_hc_map, dificuldade_counts_map,
                    top3_cenarios, novos_mes_list,
                    turno_map=turno_map,
                    team_categoria_map=team_categoria_map,
                    b64_meses=b64_meses, txt_meses=txt_meses,
                    b64_diario=b64_diario, txt_diario=txt_diario,
                    b64_meses_facil=b64_meses_facil, txt_meses_facil=txt_meses_facil,
                    b64_quarters_facil=b64_quarters_facil, txt_quarters_facil=txt_quarters_facil,
                    docs_total=docs_total, docs_fn=docs_fn, docs_fp=docs_fp,
                    ufs_total=ufs_total, ufs_fn=ufs_fn, ufs_fp=ufs_fp,
                    mat_tipo_uf_total=mat_tipo_uf_total, mat_tipo_uf_fn=mat_tipo_uf_fn, mat_tipo_uf_fp=mat_tipo_uf_fp,
                    scen_tipo_total=scen_tipo_total, scen_tipo_fn=scen_tipo_fn, scen_tipo_fp=scen_tipo_fp,
                    scen_uf_total=scen_uf_total, scen_uf_fn=scen_uf_fn, scen_uf_fp=scen_uf_fp,
                    etapa_map=etapa_map, dificuldade_map=dificuldade_map,
                    aud_mesmo_mes=aud_mesmo_mes, aud_outro_mes=aud_outro_mes,
                    total_atual_aud=total_atual_aud,
                    spill_df=spill_df, spill_count_prev=spill_count_prev, spill_count_outros=spill_count_outros, spill_breakdown=spill_breakdown,
                    official_block=None,
                    proto_diff=proto_diff_global,
                    scen_geral=scen_geral_total,
                    uf_ff=uf_ff_total,
                    scen_ff=scen_ff_total,
                    comp_txt_treinamento=comp_txt_treinamento_total,
                    b64_turno=b64_turno_total,
                    txt_turno=txt_turno_total,
                    b64_turno_fy=b64_turno_fy_total,
                    txt_turno_fy=txt_turno_fy_total,
                    b64_falhas_por_agente_turno=b64_falhas_por_agente_turno,
                    txt_falhas_por_agente_turno=txt_falhas_por_agente_turno,
                    tab_falhas_por_agente_turno=tab_falhas_por_agente_turno,
                    slope_meses=slope_meses, slope_facil=slope_facil, hist_meses=hist_meses,
                    is_fechamento_mes=fechamento_mes, audit_grace_end=eff_audit_end,
                )

        # HTML OFICIAL
        insights_of = gerar_insights(txt_meses_of, txt_meses_facil_of, txt_diario_of, slope_meses_of, slope_facil_of, slope_diario_of, top3_cenarios_of)
        mats_of = set(df_scope_of[COL_MATRICULA].apply(safe_str).tolist()) if not df_scope_of.empty else set()
        nome_map_of, tempo_casa_map_of, tempo_etapa_map_of = build_maps_nome_e_tempos(df_scope_of, hc_por_mats(mats_of, df_hc_local), df_cur_reinc_of, period_start=cur_start, period_end=cur_end)
        atividade_atual_hc_map_of = build_atividade_atual_hc_map(hc_por_mats(mats_of, df_hc_local))
        turno_map_of = build_turno_hc_map(hc_por_mats(mats_of, df_hc_local))
        team_categoria_map_of = build_team_categoria_map(hc_por_mats(mats_of, df_hc_local))
        dificuldade_counts_map_of = build_dificuldade_counts_map(df_cur_reinc_of)
        falhas_por_agente_rows_of = build_falhas_por_agente_turno(df_cur_reinc_of, turno_map_of, agentes_turno_map)
        b64_falhas_por_agente_turno_of, txt_falhas_por_agente_turno_of = chart_falhas_por_agente_turno_base64(falhas_por_agente_rows_of)
        tab_falhas_por_agente_turno_of = render_falhas_por_agente_turno_table(falhas_por_agente_rows_of)

        html_oficial = None
        if _HAS_REPORT_FALHAS_CORE and USE_CORE_HTML_TOTAL_OFICIAL:
            try:
                _pares_mes_core = build_monthly_counts(df_scope_of, april_start(cur_end), cur_start, cur_end)
                _pares_diario_core = diario if 'diario' in locals() else None
                _pares_facil_core = mensais_facil if 'mensais_facil' in locals() else None
                _res_core = _build_report_core(
                    df_base=df_scope_of,
                    col_data=COL_DATA_ANALISE,
                    today=(cur_end or cur_start),
                    pares_mes=_pares_mes_core,
                    pares_diario=_pares_diario_core,
                    pares_facil=_pares_facil_core,
                    top3_cenarios=top3_cenarios_of,
                )
                html_oficial = _res_core.get('html')
            except Exception:
                html_oficial = None
        if not html_oficial:
            html_oficial = None
            if _HAS_REPORT_FALHAS_CORE and USE_CORE_HTML_TOTAL_OFICIAL:
                try:
                    _pares_mes_core = build_monthly_counts(df_scope_of, april_start(cur_end), cur_start, cur_end)
                    _pares_diario_core = diario if 'diario' in locals() else None
                    _pares_facil_core = mensais_facil if 'mensais_facil' in locals() else None
                    _res_core = _build_report_core(
                        df_base=df_scope_of,
                        col_data=COL_DATA_ANALISE,
                        today=(cur_end or cur_start),
                        pares_mes=_pares_mes_core,
                        pares_diario=_pares_diario_core,
                        pares_facil=_pares_facil_core,
                        top3_cenarios=top3_cenarios_of,
                    )
                    html_oficial = _res_core.get('html')
                except Exception:
                    html_oficial = None
            if not html_oficial:
                html_oficial = format_html(
            insights_of,
            cur_start, cur_end,
            df_cur_reinc_of[COL_DATA].dt.date.nunique() if not df_cur_reinc_of.empty else 0,
            dias_corridos,
            max(dias_corridos - (df_cur_reinc_of[COL_DATA].dt.date.nunique() if not df_cur_reinc_of.empty else 0), 0),
            prev_equal_start_of, prev_equal_end_of, total_atual_of, total_prev_equal_of,
            df_reinc_full_of, tempo_etapa_map_of, tempo_casa_map_of, nome_map_of, atividade_atual_hc_map_of, dificuldade_counts_map_of,
            top3_cenarios_of, novos_mes_list_of,
            turno_map=turno_map_of,
            team_categoria_map=team_categoria_map_of,
            b64_meses=b64_meses_of, txt_meses=txt_meses_of,
            b64_diario=b64_diario_of, txt_diario=txt_diario_of,
            b64_meses_facil=b64_meses_facil_of, txt_meses_facil=txt_meses_facil_of,
            b64_quarters_facil=b64_quarters_facil_of, txt_quarters_facil=txt_quarters_facil_of,
            docs_total=docs_total, docs_fn=docs_fn, docs_fp=docs_fp,
            ufs_total=ufs_total, ufs_fn=ufs_fn, ufs_fp=ufs_fp,
            mat_tipo_uf_total=mat_tipo_uf_total, mat_tipo_uf_fn=mat_tipo_uf_fn, mat_tipo_uf_fp=mat_tipo_uf_fp,
            scen_tipo_total=scen_tipo_total, scen_tipo_fn=scen_tipo_fn, scen_tipo_fp=scen_tipo_fp,
            scen_uf_total=scen_uf_total, scen_uf_fn=scen_uf_fn, scen_uf_fp=scen_uf_fp,
            etapa_map=etapa_map, dificuldade_map=dificuldade_map,
            aud_mesmo_mes=aud_mesmo_mes_of, aud_outro_mes=aud_outro_mes_of,
            total_atual_aud=total_atual_aud_of,
            spill_df=spill_df_of, spill_count_prev=spill_count_prev_of, spill_count_outros=spill_count_outros_of, spill_breakdown=spill_breakdown_of,
            official_block=None,
        proto_diff=proto_diff_global,
 scen_geral=scen_geral_of,
 uf_ff=uf_ff_of,
 scen_ff=scen_ff_of,
 comp_txt_treinamento=comp_txt_treinamento_of,
 b64_turno=b64_turno_of,
 txt_turno=txt_turno_of,
            b64_turno_fy=b64_turno_fy_of,
            txt_turno_fy=txt_turno_fy_of,
 b64_falhas_por_agente_turno=b64_falhas_por_agente_turno_of,
 txt_falhas_por_agente_turno=txt_falhas_por_agente_turno_of,
 tab_falhas_por_agente_turno=tab_falhas_por_agente_turno_of,
            slope_meses=slope_meses_of, slope_facil=slope_facil_of, hist_meses=hist_meses_of,
 is_fechamento_mes=fechamento_mes, audit_grace_end=eff_audit_end,
)

        # HTML - Falhas últimos 3 meses (recorrência por agente)
        html_ult3m = format_html_ultimos_3_meses(
            scope_name=scope_name,
            cur_start=cur_start,
            cur_end=cur_end,
            df_total=df_scope,
            df_oficial=df_scope_of,
            nome_map_total=nome_map,
            turno_map_total=turno_map,
            nome_map_oficial=nome_map_of,
            turno_map_oficial=turno_map_of,
            dual_metric_mode=dual_metric_mode,
        )


        # NOVO: páginas auxiliares (tabs) · Clientes/Workflows e Suporte
        try:
            df_sup = read_suporte(excel_path, ABA_SUPORTE)
            if df_sup is None or df_sup.empty:
                print(f"⚠️ Aba Suporte vazia ou não encontrada em {excel_path}")
            else:
                print(f"ℹ️ Suporte TEAMS: {len(df_sup)} registros carregados da planilha")
        except Exception as e:
            print(f"⚠️ Falha ao ler aba Suporte: {e}")
            df_sup = pd.DataFrame()

        # NOVO (v8): Contestações (por período + por localidade) + HTML exclusivo de falhas retiradas
        df_cont_res, df_cont_det, _ = get_contest_state()
        try:
            html_contestacoes = build_contestacoes_page_html(cur_start, cur_end, scope_name=scope_name)
        except Exception:
            html_contestacoes = ''
        try:
            html_falhas_retiradas = build_falhas_retiradas_page_html(cur_start, cur_end, scope_name=scope_name)
        except Exception:
            html_falhas_retiradas = ''
        try:
            html_falhas_removidas = build_falhas_removidas_page_html(cur_start, cur_end, scope_name=scope_name)
        except Exception:
            html_falhas_removidas = ''

        try:
            html_treinamentos = build_treinamentos_tracking_page_html(
                df_trein,
                df_hc=df_hc_trein,
                scope_name=scope_name,
                ref_date=(cur_end or datetime.now().date())
            )
        except Exception:
            html_treinamentos = ''

        html_suporte = build_suporte_page_html(df_sup, cur_start, cur_end, scope_name=scope_name, nome_map=nome_map, df_hc=df_hc)
        html_clientwf = build_client_workflow_page_html(
            df_scope, df_scope_of, cur_start, cur_end,
            scope_name=scope_name,
            dual_metric_mode=dual_metric_mode,
        )

        # Robustez: nunca deixar HTML Clientes/Workflows quebrar o write_text
        if not isinstance(html_clientwf, str):
            html_clientwf = ''

        html_total_path = scope_out / f"relatorio_{slug(scope_name)}_TOTAL_{ts}.html"
        html_oficial_path = scope_out / f"relatorio_{slug(scope_name)}_OFICIAL_{ts}.html"
        html_abas_path = scope_out / f"relatorio_{slug(scope_name)}_ABAS_{ts}.html"
        html_ult3m_path = scope_out / f"relatorio_{slug(scope_name)}_ULT3M_{ts}.html"
        html_clientwf_path = scope_out / f"relatorio_{slug(scope_name)}_CLIENTES_WF_{ts}.html"
        html_suporte_path = scope_out / f"relatorio_{slug(scope_name)}_SUPORTE_{ts}.html"
        html_contest_path = scope_out / f"relatorio_{slug(scope_name)}_CONTESTACOES_{ts}.html"
        html_retiradas_path = scope_out / f"relatorio_{slug(scope_name)}_FALHAS_RETIRADAS_{ts}.html"
        html_removidas_path = scope_out / f"relatorio_{slug(scope_name)}_FALHAS_REMOVIDAS_{ts}.html"

        xlsx_suffix = "TOTAL_E_OFICIAL" if dual_metric_mode else "OFICIAL"
        xlsx_combo = scope_out / f"reincidencia_{slug(scope_name)}_{xlsx_suffix}_{ts}.xlsx"

        file_oficial = html_oficial_path.name if salvar_html_individuais else ""
        file_total = html_total_path.name if (salvar_html_individuais and dual_metric_mode) else ""
        file_clientwf = html_clientwf_path.name if salvar_html_individuais else ""
        file_suporte = html_suporte_path.name if salvar_html_individuais else ""
        file_ult3m = html_ult3m_path.name if salvar_html_individuais else ""
        file_contest = html_contest_path.name if salvar_html_individuais else ""
        file_retiradas = html_retiradas_path.name if salvar_html_individuais else ""
        file_removidas = html_removidas_path.name if salvar_html_individuais else ""

        if salvar_html_individuais:
            if dual_metric_mode and html_total:
                html_total_path.write_text(html_total, encoding='utf-8')
            html_oficial_path.write_text(html_oficial, encoding='utf-8')
            html_ult3m_path.write_text(html_ult3m, encoding='utf-8')
            html_clientwf_path.write_text((html_clientwf or ''), encoding='utf-8')
            html_suporte_path.write_text(html_suporte, encoding='utf-8')
            try:
                html_contest_path.write_text((html_contestacoes or ''), encoding='utf-8')
            except Exception:
                pass
            try:
                html_retiradas_path.write_text((html_falhas_retiradas or ''), encoding='utf-8')
            except Exception:
                pass
            try:
                html_removidas_path.write_text((html_falhas_removidas or ''), encoding='utf-8')
            except Exception:
                pass

        html_abas_path.write_text(
            build_tabs_index_html(
                f"Report Falhas Críticas - {scope_name}",
                file_oficial,
                file_total,
                file_clientwf,
                file_suporte,
                file_ult3m,
                html_oficial=html_oficial,
                html_total=html_total,
                html_clientwf=html_clientwf,
                html_suporte=html_suporte,
                html_ult3m=html_ult3m,
                html_treinamentos=html_treinamentos,
                file_contestacoes=file_contest,
                file_retiradas=file_retiradas,
                file_removidas=file_removidas,
                logo_data_uri=resolve_logo_data_uri(),
                embed_only=not salvar_html_individuais,
                include_total_tab=dual_metric_mode,
                primary_tab_label="Report" if not dual_metric_mode else "Métrica Oficial",
            ),
            encoding='utf-8'
        )


        # DataFrames (somente dados) para Excel - Falhas últimos 3 meses
        ult3m_total_df_tab, _meta_total = build_ult3m_agent_table(df_scope, cur_start, cur_end, nome_map, turno_map)
        ult3m_oficial_df_tab, _meta_of = build_ult3m_agent_table(df_scope_of, cur_start, cur_end, nome_map_of, turno_map_of)

        export_reincidencia_xlsx(
            insights,
            df_reinc_full=df_reinc_full,
            df_cur_reinc=df_cur_reinc,
            tempo_map_etapa=tempo_etapa_map,
            tempo_map_casa=tempo_casa_map,
            nome_map=nome_map,
        atividade_atual_hc_map=atividade_atual_hc_map,
        dificuldade_counts_map=dificuldade_counts_map,
            team_categoria_map=team_categoria_map,
            destino=xlsx_combo,
            etapa_map=etapa_map,
            dificuldade_map=dificuldade_map,
            spill_df=spill_df_of,
            df_reinc_full_oficial=df_reinc_full_of,
            df_cur_reinc_oficial=df_cur_reinc_of,
            kpis_total=kpis_total if dual_metric_mode else None,
            kpis_oficial=kpis_oficial,
            turno_map=turno_map,
            rank_tipo_uf_total=scen_ff_total if dual_metric_mode else None,
            rank_tipo_uf_oficial=scen_ff_of,
            ult3m_total_df=ult3m_total_df_tab if dual_metric_mode else None,
            ult3m_oficial_df=ult3m_oficial_df_tab,
            dual_metric_mode=dual_metric_mode,
        )

        print(f"✅ HTML ABAS: {html_abas_path}")
        if salvar_html_individuais:
            if dual_metric_mode:
                print(f"✅ HTML TOTAL: {html_total_path}")
            print(f"✅ HTML OFICIAL: {html_oficial_path}")
            print(f"✅ HTML ULT3M: {html_ult3m_path}")
            print(f"✅ HTML CONTESTAÇÕES: {html_contest_path}")
            print(f"✅ HTML FALHAS RETIRADAS: {html_retiradas_path}")
            print(f"✅ HTML FALHAS REMOVIDAS: {html_removidas_path}")
        print(f"✅ XLSX ({xlsx_suffix}): {xlsx_combo}")

        # Preview: corpo com texto + HTML oficial, anexos XLSX + HTML abas, destinatarios por BU
        if preview_email:
            if should_skip_email_preview(scope_name):
                print(f"ℹ️ Prévia de e-mail omitida para {scope_name} (envio manual se necessário).")
            else:
                try:
                    attachments = []
                    if xlsx_combo.exists():
                        attachments.append(xlsx_combo)
                    if html_abas_path.exists():
                        attachments.append(html_abas_path)

                    to_list, cc_list = resolve_email_recipients(scope_name)
                    if not to_list and not cc_list:
                        print(
                            "⚠️ Nenhum destinatário configurado. "
                            "Preencha EMAIL_LISTS em config_report.local.py."
                        )

                    recipients_sidecar_path = None
                    if EMAIL_EMBED_RECIPIENTS_IN_EML:
                        eml_to, eml_cc = to_list, cc_list
                    else:
                        if to_list or cc_list:
                            recipients_sidecar_path = write_recipients_sidecar(
                                out_dir, slug(scope_name), to_list, cc_list,
                            )
                        eml_to, eml_cc = [], []

                    subj = f"Report Falhas Críticas - {scope_name} ({cur_start.strftime('%d/%m/%Y')} a {cur_end.strftime('%d/%m/%Y')})"
                    periodo_txt = f"{cur_start.strftime('%d/%m/%Y')} a {cur_end.strftime('%d/%m/%Y')}"
                    anexos_desc = []
                    if xlsx_combo.exists():
                        anexos_desc.append("• <b>XLSX consolidado</b>: base para filtro, ordenação e extrações.")
                    if html_abas_path.exists():
                        anexos_desc.append("• <b>HTML com abas</b>: melhor opção para navegação rápida e leitura executiva.")
                    anexos_txt = '<br>'.join(anexos_desc) if anexos_desc else 'Sem anexos complementares.'
                    k_email_oficial = make_kpis(cur_start, cur_end, total_atual_of, total_prev_equal_of, prev_equal_start_of, prev_equal_end_of, df_reinc_full_of, top3_cenarios_of, novos_mes_list_of)
                    k_email_total = make_kpis(cur_start, cur_end, total_atual, total_prev_equal, prev_equal_start, prev_equal_end, df_reinc_full, top3_cenarios, novos_mes_list)
                    removidas_html = build_removidas_email_blurb(
                        cur_start, cur_end, scope_name=scope_name, out_dir=out_dir,
                    )
                    html_email = inject_email_intro(
                        html_oficial, scope_name, periodo_txt, anexos_txt,
                        kpis_total=k_email_total, kpis_oficial=k_email_oficial,
                        removidas_html=removidas_html,
                        dual_metric_mode=dual_metric_mode,
                    )
                    preview_new_outlook(
                        html_email,
                        subject=subj,
                        out_dir=scope_out,
                        to_list=eml_to,
                        cc_list=eml_cc,
                        from_addr=email_from,
                        attachments=attachments,
                        scope_slug=slug(scope_name),
                        resolved_to_list=to_list,
                        resolved_cc_list=cc_list,
                        recipients_sidecar_path=recipients_sidecar_path,
                    )
                except Exception as e:
                    print('⚠️ Não foi possível abrir prévia no Outlook:', e)

    def run_executivo_report():
        """Relatório comparativo BSB × SC + e-mail para gerência (por último no Outlook)."""
        if not gerar_executivo:
            return
        try:
            from report_falhas.executive_report import build_and_save_executive_report

            cur_start_exec, cur_end_exec = periodo_atual()
            _emit_pipeline_status("Relatório executivo: gerando HTML comparativo BSB × SC…")
            print('🧭 Gerando relatório executivo (Brasília x São Carlos)...')
            exec_out = resolve_scope_output_dir(out_dir, "executivo", ref_date=cur_end_exec)
            scope_outputs.append(_scope_output_entry("executivo", exec_out))
            exec_path = build_and_save_executive_report(
                df_base=df_base,
                df_hc=df_hc,
                localidades_alvo=LOCALIDADES_ALVO,
                filtrar_por_localidades=filtrar_por_localidades,
                cur_start=cur_start_exec,
                cur_end=cur_end_exec,
                out_dir=exec_out,
                ts=ts,
                col_data=COL_DATA,
                col_cenario=COL_CENARIO,
            )
            if not exec_path:
                _emit_pipeline_status(
                    "Relatório executivo: falhou ao gerar HTML — veja avisos acima no log"
                )
                return

            _emit_pipeline_status(f"Relatório executivo: HTML gerado ({exec_path.name})")

            # Executivo comparativo marcado → sempre gera e abre o .eml (independe do toggle geral).
            try:
                fechamento_exec = is_fechamento_mes(
                    first_day(hoje), cur_start_exec, cur_end_exec, date.today(),
                )
                exec_attachments = [exec_path]
                to_list, cc_list = resolve_email_recipients("Executivo")
                if not to_list and not cc_list:
                    print(
                        "⚠️ Nenhum destinatário configurado para Executivo. "
                        "Preencha gerencia_executivo em config_report.local.py."
                    )
                recipients_sidecar_path = None
                if EMAIL_EMBED_RECIPIENTS_IN_EML:
                    eml_to, eml_cc = to_list, cc_list
                else:
                    if to_list or cc_list:
                        recipients_sidecar_path = write_recipients_sidecar(
                            exec_out, slug("Executivo"), to_list, cc_list,
                        )
                    eml_to, eml_cc = [], []

                html_email = build_executive_email_body(
                    cur_start_exec, cur_end_exec, fechamento_mes=fechamento_exec,
                )
                subj = build_executive_email_subject(cur_start_exec, cur_end_exec)
                preview_new_outlook(
                    html_email,
                    subject=subj,
                    out_dir=exec_out,
                    to_list=eml_to,
                    cc_list=eml_cc,
                    from_addr=email_from,
                    attachments=exec_attachments,
                    scope_slug=slug("Executivo"),
                    resolved_to_list=to_list,
                    resolved_cc_list=cc_list,
                    recipients_sidecar_path=recipients_sidecar_path,
                )
            except Exception as e:
                print("⚠️ Não foi possível abrir prévia do e-mail executivo:", e)
        except Exception as e:
            print('⚠️ Falha ao gerar relatório executivo:', e)
            _emit_pipeline_status(f"Relatório executivo: erro — {e}")

    if gerar_consolidado:
        aliases_all = set().union(*LOCALIDADES_ALVO.values())
        df_all = filtrar_por_localidades(df_base, aliases_all)
        if not df_all.empty:
            print('🩺 Gerando CONSOLIDADO (Brasília + São Carlos)...')
            gerar_relatorio('Consolidado', df_all)

    for loc, aliases in LOCALIDADES_ALVO.items():
        print(f"🔎 Gerando relatório para: {loc}")
        df_loc = filtrar_por_localidades(df_base, set(aliases))

        if df_loc.empty:
            print(f"⚠️ Sem dados para {loc}")
            continue

        gerar_relatorio(loc, df_loc)

    run_executivo_report()

    if settings is not None:
        base_resolved = str(Path(out_dir).resolve())
        return {
            "output_dir": base_resolved,
            "output_dir_display": _display_falhas_path(base_resolved),
            "scope_dirs": scope_outputs,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
