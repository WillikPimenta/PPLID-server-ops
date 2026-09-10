# -*- coding: utf-8 -*-
"""Leitura da Base Excel e mapas de nome/tempo por matricula (fluxo principal)."""

import re
import unicodedata
from datetime import datetime, date

import pandas as pd

import report_falhas.config_report as cfg_report
from report_falhas.io.data_loader import (
    safe_str,
    normalize_text,
    safe_to_datetime,
    norm_matricula,
    norm_protocolo,
)
from report_falhas.contestacoes import (
    _apply_contestacoes_to_base,
    _build_contest_globals_empty,
    _read_contestacoes,
)
from report_falhas.falhas_removidas import (
    _apply_falhas_removidas_to_base,
    _build_removidas_globals_empty,
    _read_falhas_removidas,
)
from report_falhas.display_config import DEBUG_MATS_ALVO, DEBUG_TEMPO_ETAPA
from report_falhas.matricula_utils import (
    clean_matricula_unified,
    format_cenario_text,
    normalize_categoria_falha,
    normalize_dificuldade,
)
from report_falhas.periods import humanize_ymd

COL_DATA = cfg_report.COL_DATA
COL_DATA_ANALISE = cfg_report.COL_DATA_ANALISE
COL_DATA_AUDITORIA = cfg_report.COL_DATA_AUDITORIA
COL_MATRICULA = cfg_report.COL_MATRICULA
COL_PROTOCOLO = cfg_report.COL_PROTOCOLO
COL_ETAPA = "Etapa"
COL_NOME_BASE = "Nome Agente"

def is_alteracao_atividade(obs: str) -> bool:
    """Identifica se 'observacao' indica alteração/mudança de atividade.
    Versão robusta: tolera duplicações, NBSP, caracteres invisíveis (zero-width),
    variações de espaçamento e textos antes/depois.
    """
    s = safe_str(obs)
    if not s:
        return False
    s = s.replace('\u00A0', ' ')
    try:
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Cf')
    except Exception:
        pass
    s = normalize_text(s)
    s = re.sub(r"\s+", " " , s).strip()
    return re.search(r"\b(alteracao|mudanca)\s*(de\s*)?atividade\b", s) is not None


def build_maps_nome_e_tempos(
    df_base: pd.DataFrame,
    dfh: pd.DataFrame,
    df_cur_mes: pd.DataFrame | None = None,
    period_start: date | None = None,
    period_end: date | None = None
):
    EQUIV_ETAPA_ATIV = {}
    nome_map: dict[str, str] = {}
    if not dfh.empty and {'matricula_agente','nome_agente'}.issubset(dfh.columns):
        gnome = (
            dfh.dropna(subset=['matricula_agente','nome_agente'])
               .sort_values(['matricula_agente','data_inicial'], ascending=[True, False])
        )
        for _, r in gnome.iterrows():
            nome_map[norm_matricula(r['matricula_agente'])] = safe_str(r['nome_agente'])
    if COL_NOME_BASE in df_base.columns:
        for _, r in df_base.dropna(subset=[COL_MATRICULA, COL_NOME_BASE]).iterrows():
            nome_map.setdefault(norm_matricula(r[COL_MATRICULA]), safe_str(r[COL_NOME_BASE]))
    if dfh.empty:
        return nome_map, {}, {}

    dfh = dfh.copy()
    if 'data_final' in dfh.columns:
        dfh['ativo'] = dfh['data_final'].isna()
    else:
        dfh['ativo'] = True

    def _normaliza_etapa(x: str) -> str | None:
        if pd.isna(x) or x is None:
            return None
        x = safe_str(x)
        return EQUIV_ETAPA_ATIV.get(x, x)

    atividade_por_mat: dict[str, str | None] = {}

    # Coluna-chave para matrícula (normalizada quando disponível)

    key_col_mat = 'mat_norm' if (df_cur_mes is not None and 'mat_norm' in df_cur_mes.columns) else COL_MATRICULA
    if df_cur_mes is not None and (COL_ETAPA in df_cur_mes.columns):
        tmp = df_cur_mes.dropna(subset=[COL_MATRICULA, COL_ETAPA]).copy()
        if not tmp.empty:
            tmp[COL_ETAPA] = tmp[COL_ETAPA].map(_normaliza_etapa)
            for mat, serie in tmp.groupby(key_col_mat)[COL_ETAPA]:
                serie = serie.dropna()
                if not serie.empty:
                    moda = serie.mode()
                    atividade_por_mat[norm_matricula(mat)] = safe_str(moda.iloc[0]) if not moda.empty else safe_str(serie.iloc[-1])
    if not atividade_por_mat and (COL_ETAPA in df_base.columns):
        tmpb = df_base.dropna(subset=[COL_MATRICULA, COL_ETAPA, COL_DATA]).sort_values(COL_DATA)
        if not tmpb.empty:
            tmpb[COL_ETAPA] = tmpb[COL_ETAPA].map(_normaliza_etapa)
            ult = tmpb.groupby(key_col_mat)[COL_ETAPA].last()
            atividade_por_mat = {norm_matricula(k): (None if pd.isna(v) else safe_str(v)) for k, v in ult.items()}

    tempo_casa_map: dict[str, str] = {}
    tempo_etapa_map: dict[str, str] = {}
    if 'matricula_agente' in dfh.columns:
        grp_first = (
            dfh.dropna(subset=['matricula_agente'])
               .sort_values(['matricula_agente', 'data_inicial'], ascending=[True, False])
               .groupby('matricula_agente', as_index=False)
               .first()
        )
        for _, r in grp_first.iterrows():
            mat = safe_str(r['matricula_agente'])
            adm = r.get('data_admissao')
            if not pd.isna(adm):
                tempo_casa_map[mat] = humanize_ymd(pd.Timestamp(adm).date(), datetime.now().date())
            else:
                tempo_casa_map[mat] = '—'

        def _tempo_etapa_por(mat: str) -> str:

            """Tempo na etapa.

            Regra principal: se houver Alteração/Mudança de Atividade, usa a data_inicial do próximo ciclo após a alteração.

            Fallback: se NÃO houver alteração de atividade, usa a primeira data_inicial disponível (início mais antigo).

            """

            dbg_on = DEBUG_TEMPO_ETAPA

            dbg_set = DEBUG_MATS_ALVO

            mat_raw = safe_str(mat)

            mat_n = norm_matricula(mat)

            dbg = bool(dbg_on) and (not dbg_set or (mat_raw in dbg_set) or (mat_n in dbg_set))

            def _dbg(msg: str):

                if dbg:

                    print(f'[DEBUG_TEMPO_ETAPA] {mat_raw} | {msg}')



            if not mat_n:

                _dbg('RETORNO: matrícula vazia após normalização')

                return '—'

            if dfh is None or dfh.empty:

                _dbg('RETORNO: dfh (HC) vazio')

                return '—'

            dfa = dfh[dfh['mat_norm'] == mat_n].copy() if ('mat_norm' in dfh.columns) else dfh[dfh['matricula_agente'].apply(norm_matricula) == mat_n].copy()

            _dbg(f'mat_norm={mat_n} | linhas_hc={len(dfa)}')

            if dfa.empty or 'data_inicial' not in dfa.columns:

                _dbg('RETORNO: sem linhas no HC para a matrícula (match falhou) OU sem data_inicial')

                return '—'

            if not pd.api.types.is_datetime64_any_dtype(dfa['data_inicial']):

                dfa['data_inicial'] = safe_to_datetime(dfa['data_inicial'])

            dfa = dfa.dropna(subset=['data_inicial']).copy()

            _dbg(f'linhas_hc_com_data_inicial={len(dfa)}')

            if dfa.empty:

                _dbg('RETORNO: todas as data_inicial viraram NaT após conversão')

                return '—'

            if 'id' in dfa.columns:

                dfa['_id_num'] = pd.to_numeric(dfa['id'], errors='coerce').fillna(-1)

            else:

                dfa['_id_num'] = -1

            if 'observacao' in dfa.columns:

                dfa['_alt_ativ'] = dfa['observacao'].apply(is_alteracao_atividade)

            else:

                dfa['_alt_ativ'] = False

            alt = dfa[dfa['_alt_ativ']].copy()

            _dbg(f'linhas_alt_atividade={len(alt)} | obs_exemplo=' + (str(alt['observacao'].head(2).tolist()) if (not alt.empty and 'observacao' in alt.columns) else '[]'))



            # === Fallback: sem alteração de atividade -> usa primeira data_inicial (mais antiga) ===

            if alt.empty:

                start_dt = dfa['data_inicial'].min()

                _dbg(f'FALLBACK: sem alt_ativ -> start_dt(min)={start_dt}')

                tempo = humanize_ymd(pd.Timestamp(start_dt).date(), datetime.now().date())

                _dbg(f'RETORNO(FALLBACK): tempo={tempo}')

                return tempo



            # Ordenação para escolher a última alteração e o próximo ciclo

            dfa = dfa.sort_values(['data_inicial', '_id_num'], ascending=[True, True])

            alt = alt.sort_values(['data_inicial', '_id_num'], ascending=[True, True])

            def _pick_change(dd: pd.DataFrame):

                if dd is None or dd.empty:

                    return None

                mx = dd['data_inicial'].max()

                sub = dd[dd['data_inicial'] == mx].sort_values(['_id_num'], ascending=[False])

                return sub.iloc[0]['data_inicial'] if not sub.empty else mx

            flag = ''

            change_dt = None

            if period_start and period_end:

                mask_p = (alt['data_inicial'].dt.date >= period_start) & (alt['data_inicial'].dt.date <= period_end)

                change_dt = _pick_change(alt.loc[mask_p].copy())

                if change_dt is None:

                    change_dt = _pick_change(alt)

                    if change_dt is not None:

                        flag = '' #(fora período) pensar na regra de integração ou coisa do tipo

            else:

                change_dt = _pick_change(alt)

            _dbg(f'change_dt={change_dt} | flag={flag}')

            if change_dt is None or pd.isna(change_dt):

                _dbg('RETORNO: não foi possível determinar change_dt')

                return '—'

            next_rows = dfa[dfa['data_inicial'] > change_dt].copy()

            if not next_rows.empty:

                next_dt = next_rows['data_inicial'].min()

            else:

                next_dt = change_dt

                flag = (flag + ' (sem próximo ciclo)') if flag else ' (sem próximo ciclo)'

            _dbg(f'next_dt={next_dt} | flag_final={flag}')

            tempo = humanize_ymd(pd.Timestamp(next_dt).date(), datetime.now().date())

            _dbg(f'RETORNO: tempo={tempo}{flag}')

            return tempo + (flag if flag else '')
    mats = set(df_base[COL_MATRICULA].apply(safe_str).unique().tolist())
    if 'matricula_agente' in dfh.columns:
        mats |= set(dfh['matricula_agente'].apply(safe_str).unique().tolist())
    for mat in mats:
        tempo_etapa_map[mat] = _tempo_etapa_por(mat)
    return nome_map, tempo_casa_map, tempo_etapa_map


def read_base(path: str, sheet_name: str) -> pd.DataFrame:
    """Versão patchada do read_base com aplicação correta de contestações.
    - lê Base com as colunas exatas do usuário
    - normaliza Protocolo
    - unifica Cenário/Novo cenário
    - aplica contestações por Protocolo
    - remove da Base apenas quando Falha Procedente? = 'Desconsiderar a Falha'
    """
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
    except Exception:
        try:
            xl = pd.ExcelFile(path, engine='openpyxl')
            sh = None
            for s in xl.sheet_names:
                if normalize_text(str(s)) == normalize_text(sheet_name):
                    sh = s
                    break
            if sh is None:
                raise
            df = pd.read_excel(path, sheet_name=sh, engine='openpyxl')
        except Exception:
            return pd.DataFrame()

    df.rename(columns=lambda c: safe_str(c), inplace=True)
    norm_map = {normalize_text(str(c)): str(c) for c in df.columns}
    base_aliases = {
        'data auditoria': 'Data Auditoria',
        'data de analise': 'Data de Análise',
        'data de análise': 'Data de Análise',
        'protocolo': 'Protocolo',
        'etapa': 'Etapa',
        'cliente': 'Cliente',
        'matricula agente': 'Matrícula Agente',
        'matrícula agente': 'Matrícula Agente',
        'localidade': 'Localidade',
        'nome agente': 'Nome Agente',
        'lider': 'Líder',
        'líder': 'Líder',
        'categoria falha': 'Categoria falha',
        'cenario': 'Cenário',
        'cenário': 'Cenário',
        'workflow': 'Workflow',
        'tipo de analise': 'Tipo de análise',
        'tipo de análise': 'Tipo de análise',
        'tipo de falha': 'Tipo de Falha',
        'tipo de documento': 'Tipo de documento',
        'uf do documento': 'UF do documento',
        'tipo de solicitacao': 'Tipo de solicitação',
        'tipo de solicitação': 'Tipo de solicitação',
        'modulo': 'Módulo',
        'módulo': 'Módulo',
        'status ativo': 'Status ativo',
        'prazo': 'Prazo',
        'operations': 'Operations',
        'tendencia': 'Tendência',
        'tendência': 'Tendência',
        'nivel de dificuldade': 'Nível de Dificuldade',
        'nível de dificuldade': 'Nível de Dificuldade',
        'nome da origem': 'Nome da Origem',
        'novo cenario': 'Novo cenário',
        'novo cenário': 'Novo cenário',
        'tempo de etapa': 'Tempo de etapa',
    }
    rename_map = {}
    for k, target in base_aliases.items():
        if k in norm_map and norm_map[k] != target:
            rename_map[norm_map[k]] = target
    if rename_map:
        df.rename(columns=rename_map, inplace=True)

    if 'Data de Análise' not in df.columns:
        raise KeyError(f"Coluna 'Data de Análise' não encontrada. Colunas disponíveis: {list(df.columns)}")
    if 'Protocolo' not in df.columns:
        raise KeyError(f"Coluna 'Protocolo' não encontrada. Colunas disponíveis: {list(df.columns)}")
    if 'Matrícula Agente' not in df.columns:
        raise KeyError(f"Coluna 'Matrícula Agente' não encontrada. Colunas disponíveis: {list(df.columns)}")

    # cenário unificado priorizando Novo cenário
    if 'Novo cenário' in df.columns:
        df['Cenário Unificado'] = df['Novo cenário'].apply(safe_str)
    else:
        df['Cenário Unificado'] = ''
    if 'Cenário' in df.columns:
        mask = df['Cenário Unificado'].apply(safe_str).eq('')
        df.loc[mask, 'Cenário Unificado'] = df.loc[mask, 'Cenário'].apply(safe_str)
    cfg_report.COL_CENARIO = 'Cenário Unificado'

    df['Data de Análise'] = safe_to_datetime(df['Data de Análise'])
    if 'Data Auditoria' in df.columns:
        df['Data Auditoria'] = safe_to_datetime(df['Data Auditoria'])
    df = df.dropna(subset=['Data de Análise']).copy()

    text_cols = [
        'Protocolo', 'Etapa', 'Cliente', 'Matrícula Agente', 'Localidade', 'Nome Agente', 'Líder',
        'Categoria falha', 'Workflow', 'Tipo de análise', 'Tipo de Falha', 'Tipo de documento',
        'UF do documento', 'Tipo de solicitação', 'Módulo', 'Status ativo', 'Prazo', 'Operations',
        'Tendência', 'Nível de Dificuldade', 'Nome da Origem', 'Novo cenário', 'Tempo de etapa',
        'Cenário', 'Cenário Unificado'
    ]
    for c in text_cols:
        if c in df.columns:
            df[c] = df[c].apply(safe_str)

    if 'Categoria falha' in df.columns:
        df['Categoria falha'] = df['Categoria falha'].apply(normalize_categoria_falha)
    if 'Nível de Dificuldade' in df.columns:
        df['Nível de Dificuldade'] = df['Nível de Dificuldade'].apply(normalize_dificuldade)

    df['Protocolo'] = df['Protocolo'].apply(norm_protocolo)
    df['Cenário Unificado'] = df['Cenário Unificado'].apply(format_cenario_text)
    
    # Clean 'Matrícula Agente' and preserve extracted names in 'Nome Agente' when empty
    _raw_matricula = df['Matrícula Agente'].copy()
    _mat_nome_pairs = _raw_matricula.apply(lambda raw: clean_matricula_unified(safe_str(raw)))
    df['Matrícula Agente'] = [
        pair[0] if pair[0] else safe_str(raw)
        for raw, pair in zip(_raw_matricula, _mat_nome_pairs)
    ]
    if COL_NOME_BASE in df.columns:
        _needs_nome = df[COL_NOME_BASE].apply(safe_str).eq("") & _mat_nome_pairs.apply(lambda pair: bool(safe_str(pair[1])))
        if _needs_nome.any():
            df.loc[_needs_nome, COL_NOME_BASE] = _mat_nome_pairs[_needs_nome].apply(lambda pair: safe_str(pair[1]))
    df['mat_norm'] = df['Matrícula Agente'].apply(norm_matricula)

    ci, ce = _read_contestacoes(path)
    df, resumo, detalhe = _apply_contestacoes_to_base(df, ci, ce)
    # garante globals mesmo quando vazios
    if resumo is None or resumo.empty or detalhe is None:
        _build_contest_globals_empty()

    df_removidas = _read_falhas_removidas(path)
    df, resumo_rem, detalhe_rem = _apply_falhas_removidas_to_base(df, df_removidas)
    if resumo_rem is None or resumo_rem.empty or detalhe_rem is None:
        _build_removidas_globals_empty()

    return df
