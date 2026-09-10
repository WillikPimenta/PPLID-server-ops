# -*- coding: utf-8 -*-

"""Contestacao read/apply helpers extracted from report_falhas_criticas.py.

Etapa 1 keeps behavior compatible and avoids business-rule rewrites.
"""

import pandas as pd

from report_falhas.io.data_loader import normalize_text, norm_matricula, norm_protocolo, safe_str, safe_to_datetime
from report_falhas.config_report import (
    COL_DATA_ANALISE,
    COL_DATA_AUDITORIA,
    COL_MATRICULA,
    COL_PROTOCOLO,
    CONTEST_REMOVE_VALUE,
    CONTEST_SHEET_ALIASES_EXT,
    CONTEST_SHEET_ALIASES_INT,
)


_contest_resumo_df = pd.DataFrame()
_contest_detalhe_df = pd.DataFrame()
_contest_protocolos_removidos: set[str] = set()


def _find_sheet_case_insensitive(path: str, aliases: list[str]) -> str | None:
    try:
        xl = pd.ExcelFile(path, engine='openpyxl')
        norm_alias = {normalize_text(a) for a in aliases}
        for s in xl.sheet_names:
            if normalize_text(str(s)) in norm_alias:
                return s
        # fallback por contains
        for s in xl.sheet_names:
            ns = normalize_text(str(s))
            if any(a in ns for a in ['contestacao_interna', 'contestacao externa', 'contestacao_externa', 'contestacao interna']):
                if 'interna' in ns and any('interna' in normalize_text(a) for a in aliases):
                    return s
                if 'externa' in ns and any('externa' in normalize_text(a) for a in aliases):
                    return s
    except Exception:
        return None
    return None


def _normalize_contest_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df.rename(columns=lambda c: safe_str(c), inplace=True)
    cmap = {normalize_text(str(c).strip()): str(c).strip() for c in df.columns}

    aliases = {
        'id': 'ID',
        'cidade': 'Cidade',
        'lider responsavel': 'Líder Responsável',
        'lider responsável': 'Líder Responsável',
        'cliente': 'Cliente',
        'protocolo': 'Protocolo',
        'workflow': 'Workflow',
        'resultado esperado': 'Resultado Esperado',
        'colaborador': 'Colaborador',
        'cenario': 'Cenário',
        'cenário': 'Cenário',
        'observacoes/questionamentos': 'Observações/Questionamentos',
        'observações/questionamentos': 'Observações/Questionamentos',
        'data': 'Data',
        'falha procedente?': 'Falha Procedente?',
        'falha procedente': 'Falha Procedente?',
        'observacao': 'Observação',
        'observação': 'Observação',
        'status': 'Status',
        'quem falhou?': 'Quem Falhou?',
        'status falha retirada': 'Status Falha Retirada',
        'created': 'Created',
        'item type': 'Item Type',
        'path': 'Path',
        'solicitou reanalise': 'Solicitou Reanálise',
        'solicitou reanálise': 'Solicitou Reanálise',
    }

    rename_map = {}
    for k, target in aliases.items():
        if k in cmap and cmap[k] != target:
            rename_map[cmap[k]] = target
    if rename_map:
        df.rename(columns=rename_map, inplace=True)

    preferred = [
        'ID', 'Solicitou Reanálise', 'Cidade', 'Líder Responsável', 'Cliente', 'Protocolo', 'Workflow',
        'Resultado Esperado', 'Colaborador', 'Cenário', 'Observações/Questionamentos', 'Data',
        'Falha Procedente?', 'Observação', 'Status', 'Quem Falhou?', 'Status Falha Retirada',
        'Created', 'Item Type', 'Path'
    ]
    for c in preferred:
        if c not in df.columns:
            df[c] = ''

    df['Protocolo'] = df['Protocolo'].apply(norm_protocolo)
    for c in ['ID', 'Solicitou Reanálise', 'Cidade', 'Líder Responsável', 'Cliente', 'Workflow', 'Resultado Esperado',
              'Colaborador', 'Cenário', 'Observações/Questionamentos', 'Falha Procedente?', 'Observação',
              'Status', 'Quem Falhou?', 'Status Falha Retirada', 'Item Type', 'Path']:
        df[c] = df[c].apply(safe_str)

    df['Data'] = safe_to_datetime(df['Data'])
    if 'Created' in df.columns:
        df['Created'] = safe_to_datetime(df['Created'])
    else:
        df['Created'] = pd.NaT

    def _should_remove(row) -> bool:
        fp = normalize_text(row.get('Falha Procedente?', ''))
        st = normalize_text(row.get('Status Falha Retirada', ''))
        # regra oficial esperada
        if fp == normalize_text(CONTEST_REMOVE_VALUE):
            return True
        # fallback seguro: usa status apenas quando Falha Procedente? vier vazio
        if fp == '' and any(x in st for x in ['falha retirada', 'retirada', 'falha processual']):
            return True
        return False

    df['__RETIRADA__'] = df.apply(_should_remove, axis=1)
    df['__DATA_REF__'] = df['Data']
    mask_na = df['__DATA_REF__'].isna()
    if mask_na.any():
        df.loc[mask_na, '__DATA_REF__'] = df.loc[mask_na, 'Created']
    return df[preferred + ['__RETIRADA__', '__DATA_REF__']].copy()


def _read_contest_sheet(path: str, aliases: list[str]) -> pd.DataFrame:
    sh = _find_sheet_case_insensitive(path, aliases)
    if not sh:
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name=sh, engine='openpyxl')
    except Exception:
        return pd.DataFrame()
    return _normalize_contest_cols(df)


def _read_contestacoes(path: str):
    ci = _read_contest_sheet(path, CONTEST_SHEET_ALIASES_INT)
    ce = _read_contest_sheet(path, CONTEST_SHEET_ALIASES_EXT)
    return ci, ce


def _base_protocolo_col(df_base: pd.DataFrame) -> str | None:
    for c in [COL_PROTOCOLO, 'Protocolo']:
        if c in df_base.columns:
            return c
    return None


def _contest_detail_from_match(base_row: pd.Series, contest_row: pd.Series, fonte: str) -> dict:
    return {
        'ID': safe_str(contest_row.get('ID', '')),
        'Fonte': safe_str(fonte),
        'Data': contest_row.get('__DATA_REF__', pd.NaT),
        'Data Contestação': contest_row.get('Data', pd.NaT),
        'Created': contest_row.get('Created', pd.NaT),
        'Protocolo': safe_str(contest_row.get('Protocolo', '')),
        'Cliente Contestação': safe_str(contest_row.get('Cliente', '')),
        'Workflow Contestação': safe_str(contest_row.get('Workflow', '')),
        'Colaborador': safe_str(contest_row.get('Colaborador', '')),
        'Cenário Contestação': safe_str(contest_row.get('Cenário', '')),
        'Observações/Questionamentos': safe_str(contest_row.get('Observações/Questionamentos', '')),
        'Falha Procedente?': safe_str(contest_row.get('Falha Procedente?', '')),
        'Observação': safe_str(contest_row.get('Observação', '')),
        'Status': safe_str(contest_row.get('Status', '')),
        'Quem Falhou?': safe_str(contest_row.get('Quem Falhou?', '')),
        'Status Falha Retirada': safe_str(contest_row.get('Status Falha Retirada', '')),
        'Data de Análise Base': base_row.get(COL_DATA_ANALISE, pd.NaT),
        'Data Auditoria Base': base_row.get(COL_DATA_AUDITORIA, pd.NaT) if COL_DATA_AUDITORIA in base_row.index else pd.NaT,
        'Cliente Base': safe_str(base_row.get('Cliente', '')),
        'Workflow Base': safe_str(base_row.get('Workflow', '')),
        'Localidade Base': safe_str(base_row.get('Localidade', '')),
        'Matrícula Agente Base': safe_str(base_row.get('Matrícula Agente', '')),
        'Nome Agente Base': safe_str(base_row.get('Nome Agente', '')),
        'Líder Base': safe_str(base_row.get('Líder', '')),
        'Etapa Base': safe_str(base_row.get('Etapa', '')),
        'Tipo de documento Base': safe_str(base_row.get('Tipo de documento', '')),
        'UF do documento Base': safe_str(base_row.get('UF do documento', '')),
        'Módulo Base': safe_str(base_row.get('Módulo', '')),
        'Novo cenário Base': safe_str(base_row.get('Novo cenário', '')),
    }


def _build_contest_globals_empty() -> None:
    global _contest_resumo_df, _contest_detalhe_df, _contest_protocolos_removidos

    _contest_resumo_df = pd.DataFrame({
        'Contestações aplicadas': [0],
        'Protocolos retirados': [0],
        'Linhas removidas': [0],
        'Registros base antes': [0],
        'Registros base depois': [0],
    })
    _contest_detalhe_df = pd.DataFrame(columns=[
        'ID', 'Fonte', 'Data', 'Data Contestação', 'Created', 'Protocolo', 'Cliente Contestação',
        'Workflow Contestação', 'Colaborador', 'Cenário Contestação', 'Observações/Questionamentos',
        'Falha Procedente?', 'Observação', 'Status', 'Quem Falhou?', 'Status Falha Retirada',
        'Data de Análise Base', 'Data Auditoria Base', 'Cliente Base', 'Workflow Base', 'Localidade Base',
        'Matrícula Agente Base', 'Nome Agente Base', 'Líder Base', 'Etapa Base', 'Tipo de documento Base',
        'UF do documento Base', 'Módulo Base', 'Novo cenário Base'
    ])
    _contest_protocolos_removidos = set()


def _apply_contestacoes_to_base(df_base: pd.DataFrame, df_ci: pd.DataFrame, df_ce: pd.DataFrame):
    global _contest_resumo_df, _contest_detalhe_df, _contest_protocolos_removidos

    if df_base is None or df_base.empty:
        _build_contest_globals_empty()
        return df_base, _contest_resumo_df, _contest_detalhe_df

    prot_col = _base_protocolo_col(df_base)
    if not prot_col:
        _build_contest_globals_empty()
        resumo = _contest_resumo_df.copy()
        resumo['Registros base antes'] = [int(len(df_base))]
        resumo['Registros base depois'] = [int(len(df_base))]
        _contest_resumo_df = resumo
        return df_base.copy(), resumo, _contest_detalhe_df

    base = df_base.copy()
    base[prot_col] = base[prot_col].apply(norm_protocolo)
    base = base[base[prot_col].apply(safe_str) != ''].copy()
    if COL_MATRICULA in base.columns:
        base['_mat_norm'] = base[COL_MATRICULA].apply(norm_matricula)
    else:
        base['_mat_norm'] = ''

    detalhe_rows = []
    removed_indices: set[int] = set()
    for fonte, dfx in [('Interna', df_ci if df_ci is not None else pd.DataFrame()), ('Externa', df_ce if df_ce is not None else pd.DataFrame())]:
        if dfx is None or dfx.empty:
            continue
        if '__RETIRADA__' not in dfx.columns:
            continue
        sub = dfx[dfx['__RETIRADA__'] == True].copy()
        if sub.empty:
            continue
        sub['Fonte'] = fonte
        for _, crow in sub.iterrows():
            prot = safe_str(crow.get('Protocolo', ''))
            if not prot:
                continue
            matches = base[base[prot_col] == prot].copy()
            if matches.empty:
                # mantém rastro mesmo sem match na Base
                detalhe_rows.append({
                    'ID': safe_str(crow.get('ID', '')),
                    'Fonte': fonte,
                    'Data': crow.get('__DATA_REF__', pd.NaT),
                    'Data Contestação': crow.get('Data', pd.NaT),
                    'Created': crow.get('Created', pd.NaT),
                    'Protocolo': prot,
                    'Cliente Contestação': safe_str(crow.get('Cliente', '')),
                    'Workflow Contestação': safe_str(crow.get('Workflow', '')),
                    'Colaborador': safe_str(crow.get('Colaborador', '')),
                    'Cenário Contestação': safe_str(crow.get('Cenário', '')),
                    'Observações/Questionamentos': safe_str(crow.get('Observações/Questionamentos', '')),
                    'Falha Procedente?': safe_str(crow.get('Falha Procedente?', '')),
                    'Observação': safe_str(crow.get('Observação', '')),
                    'Status': safe_str(crow.get('Status', '')),
                    'Quem Falhou?': safe_str(crow.get('Quem Falhou?', '')),
                    'Status Falha Retirada': safe_str(crow.get('Status Falha Retirada', '')),
                    'Data de Análise Base': pd.NaT,
                    'Data Auditoria Base': pd.NaT,
                    'Cliente Base': '',
                    'Workflow Base': '',
                    'Localidade Base': '',
                    'Matrícula Agente Base': '',
                    'Nome Agente Base': '',
                    'Líder Base': '',
                    'Etapa Base': '',
                    'Tipo de documento Base': '',
                    'UF do documento Base': '',
                    'Módulo Base': '',
                    'Novo cenário Base': '',
                })
                continue
            unique_mats = {safe_str(x) for x in matches['_mat_norm'].dropna().tolist() if safe_str(x)}
            can_remove = len(unique_mats) <= 1 and (COL_MATRICULA in base.columns)
            if can_remove:
                removed_indices.update(matches.index.tolist())
            for _, brow in matches.iterrows():
                detalhe_rows.append(_contest_detail_from_match(brow, crow, fonte))

    detalhe = pd.DataFrame(detalhe_rows)
    protocolos_removidos = set()
    if removed_indices:
        protocolos_removidos = set(base.loc[list(removed_indices), prot_col].dropna().astype(str).tolist())

    base_before = int(len(base))
    if protocolos_removidos:
        base_after_df = base.drop(index=list(removed_indices)).copy()
    else:
        base_after_df = base.copy()
    base_after = int(len(base_after_df))

    if '_mat_norm' in base_after_df.columns:
        base_after_df = base_after_df.drop(columns=['_mat_norm'])

    resumo = pd.DataFrame({
        'Contestações aplicadas': [int(0 if detalhe.empty else len(detalhe))],
        'Protocolos retirados': [int(len(protocolos_removidos))],
        'Linhas removidas': [int(base_before - base_after)],
        'Registros base antes': [base_before],
        'Registros base depois': [base_after],
    })

    _contest_resumo_df = resumo
    _contest_detalhe_df = detalhe if not detalhe.empty else _contest_detalhe_df if not _contest_detalhe_df.empty else pd.DataFrame()
    _contest_protocolos_removidos = protocolos_removidos
    return base_after_df, resumo, detalhe


def get_contest_state() -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    return _contest_resumo_df.copy(), _contest_detalhe_df.copy(), set(_contest_protocolos_removidos)


__all__ = [
    '_read_contestacoes',
    '_apply_contestacoes_to_base',
    '_build_contest_globals_empty',
    'get_contest_state',
]
