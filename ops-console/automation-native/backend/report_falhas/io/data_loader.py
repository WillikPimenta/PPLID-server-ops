import calendar
import sys, base64, re, unicodedata
from io import BytesIO
from pathlib import Path
from datetime import datetime, date, timedelta
import numpy as np
import pandas as pd


# ====== HC (tempos) ======
# Lista base de atividades (mantida por compatibilidade com versões anteriores)
ATIVIDADES_VALIDAS = [
    "Análise Segurança Corporativa", "Análise Visual", "Análise Visual II",
    "Análise Visual RM", "Análises Biométricas", "Segurança Corporativa",
    "Sobreposição", "Sobreposição II",
]

# Quando False, NÃO filtra HC por atividade (recomendado para calcular Tempo na Etapa).
FILTRAR_HC_POR_ATIVIDADE = False

# Colunas esperadas no HC (normalizadas)
COLS_HC = [
    "id", "Created", "observacao",
    "data_inicial", "data_final", "matricula_agente", "nome_agente",
    "atividade", "localidade", "data_admissao",
    "turno", "leader_nome", "job_title", "team",
]

# Mapeamento de origem do novo HC para as colunas canônicas do report.
HC_SOURCE_TO_CANONICAL = {
    "agent: userlanid": "matricula_agente",
    "agent": "nome_agente",
    "jobactivity": "atividade",
    "journey: shift": "turno",
    "location": "localidade",
    "startdate": "data_inicial",
    "finaldate": "data_final",
    "agent:hiredate": "data_admissao",
    "leader": "leader_nome",
    "jobtitle": "job_title",
    "job title": "job_title",
    "team": "team",
    "id": "id",
    "created": "Created",
}

def safe_str(x):
    return '' if x is None or (hasattr(pd, 'isna') and pd.isna(x)) else str(x).strip()


def norm_matricula(x) -> str:
    try:
        if x is None or (hasattr(pd, 'isna') and pd.isna(x)):
            return ''
    except Exception:
        if x is None:
            return ''
    try:
        if isinstance(x, (int, np.integer)):
            return str(int(x)).lower()
        if isinstance(x, (float, np.floating)):
            if np.isfinite(x) and float(x).is_integer():
                return str(int(x)).lower()
    except Exception:
        pass
    s = safe_str(x)
    if not s:
        return ''
    try:
        num = pd.to_numeric(s, errors='coerce')
        if pd.notna(num):
            try:
                if float(num).is_integer():
                    return str(int(float(num))).lower()
            except Exception:
                pass
    except Exception:
        pass
    s = s.replace('\u00A0', ' ')
    try:
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Cf')
    except Exception:
        pass
    
    # Extrai matrícula quando concatenada com nome (ex.: c92935aNome do agente Cristiane)
    # Padrão: letra opcional + 3+ dígitos + letra opcional + STOP (não continuar com nome)
    m = re.search(r'([A-Za-z]?\d{3,}[A-Za-z]?)(?:\s|$|Nome|[^A-Za-z0-9])', s)
    if m:
        return m.group(1).lower()
    
    s = re.sub(r'\s+', '', s)
    m = re.fullmatch(r'(\d+)\.0+', s)
    if m:
        s = m.group(1)
    return s.lower()


def norm_protocolo(x) -> str:
    try:
        if x is None or (hasattr(pd, 'isna') and pd.isna(x)):
            return ''
    except Exception:
        if x is None:
            return ''
    try:
        import numpy as _np
        if isinstance(x, (int, _np.integer)):
            return str(int(x))
        if isinstance(x, (float, _np.floating)):
            if _np.isfinite(x) and float(x).is_integer():
                return str(int(x))
    except Exception:
        pass
    s = safe_str(x)
    if not s:
        return ''
    try:
        num = pd.to_numeric(s, errors='coerce')
        if pd.notna(num):
            try:
                if float(num).is_integer():
                    return str(int(float(num)))
            except Exception:
                pass
    except Exception:
        pass
    m = re.fullmatch(r"(\d+)\.0+", s)
    if m:
        return m.group(1)
    s = s.replace('\u00A0', ' ')
    try:
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Cf')
    except Exception:
        pass
    s = re.sub(r"\s+", " ", s).strip()
    return s


def safe_to_datetime(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    try:
        if pd.api.types.is_numeric_dtype(series):
            dt_excel = pd.to_datetime(series, errors='coerce', unit='d', origin='1899-12-30')
            if dt_excel.notna().mean() > 0.2:
                return dt_excel
    except Exception:
        pass
    dt = pd.to_datetime(series, errors='coerce', dayfirst=True, format='%d/%m/%Y')
    if dt.isna().mean() > 0.5:
        dt = pd.to_datetime(series, errors='coerce', dayfirst=True, format='%d/%m/%Y %H:%M')
    if dt.isna().mean() > 0.5:
        dt = pd.to_datetime(series, errors='coerce', dayfirst=True, format='%d/%m/%Y %H:%M:%S')
    if dt.isna().mean() > 0.5:
        # Fallback genérico: dayfirst=False para datas ISO (YYYY-MM-DD) sem UserWarning
        dt2 = pd.to_datetime(series, errors='coerce', dayfirst=False)
        dt3 = pd.to_datetime(series, errors='coerce', dayfirst=False)
        dt = dt2 if dt2.isna().mean() <= dt3.isna().mean() else dt3
    try:
        if dt.isna().mean() > 0.7:
            ser_num = pd.to_numeric(series, errors='coerce')
            if ser_num.notna().mean() > 0.2:
                dt_excel = pd.to_datetime(ser_num, errors='coerce', unit='d', origin='1899-12-30')
                if dt_excel.notna().mean() >= dt.notna().mean():
                    dt = dt_excel
    except Exception:
        pass
    return dt

def normalize_text(s):
    if pd.isna(s) or s is None:
        return ''
    s = str(s).lower().strip()
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c))


def slug(texto: str) -> str:
    s = normalize_text(texto)
    s = s.replace(' ', '_')
    s = re.sub(r'[^a-z0-9_-]+', '', s)
    return s or 'local'


def normalize_columns_hc(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    norm_map = {normalize_text(str(c).strip()): c for c in df.columns}

    # Primeiro mapeia as colunas do novo HC para os nomes canônicos esperados pelo report.
    for source_norm, target in HC_SOURCE_TO_CANONICAL.items():
        if source_norm in norm_map and norm_map[source_norm] != target:
            df.rename(columns={norm_map[source_norm]: target}, inplace=True)

    # Mantém compatibilidade com variações já conhecidas do HC legado.
    for expected in COLS_HC:
        k = normalize_text(expected)
        if k in norm_map and norm_map[k] != expected:
            df.rename(columns={norm_map[k]: expected}, inplace=True)
    df.rename(columns={c: str(c).strip() for c in df.columns}, inplace=True)

    # Garante que todas as colunas canônicas existam, mesmo quando o novo HC vier incompleto.
    for col in COLS_HC:
        if col not in df.columns:
            df[col] = pd.NaT if col in {"Created", "data_inicial", "data_final", "data_admissao"} else ''

    # "observacao" pode vir de Formalization ou InssType; usa o primeiro valor não vazio disponível.
    formal_col = norm_map.get(normalize_text('Formalization'))
    inss_col = norm_map.get(normalize_text('InssType'))
    if 'observacao' not in df.columns:
        df['observacao'] = ''
    if formal_col or inss_col:
        formal_series = df[formal_col].apply(safe_str) if formal_col in df.columns else pd.Series([''] * len(df), index=df.index)
        inss_series = df[inss_col].apply(safe_str) if inss_col in df.columns else pd.Series([''] * len(df), index=df.index)
        df['observacao'] = formal_series.where(formal_series != '', inss_series)

    return df

def read_hc(path: str, sheet: str = 'hc') -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet, engine='openpyxl')
    df = normalize_columns_hc(df)
    for col in ["atividade", "localidade", "nome_agente", "matricula_agente", "observacao", "turno", "leader_nome", "job_title"]:
        if col in df.columns:
            df[col] = df[col].apply(safe_str)
    for dcol in ["data_inicial", "data_final", "data_admissao", "created", "Created"]:
        if dcol in df.columns:
            df[dcol] = safe_to_datetime(df[dcol])
    # Mantém as colunas canônicas existentes, mas evita quebrar o report se alguma vier ausente.
    if 'matricula_agente' in df.columns:
        df['matricula_agente'] = df['matricula_agente'].apply(safe_str)
    if 'atividade' in df.columns:
        df['atividade'] = df['atividade'].apply(safe_str)
    if 'turno' in df.columns:
        df['turno'] = df['turno'].apply(safe_str)
    if FILTRAR_HC_POR_ATIVIDADE and ATIVIDADES_VALIDAS and 'atividade' in df.columns:
        df = df[df['atividade'].isin(ATIVIDADES_VALIDAS)].copy()
    if 'matricula_agente' in df.columns:
        df['mat_norm'] = df['matricula_agente'].apply(norm_matricula)
    return df


def compute_conformidade_suporte(row) -> str:
    """Aplica a regra de conformidade do Suporte.

    Regras:
      1) Categorias Iguais: Dúvida == Conclusão -> CONFORME
      2) Dificuldade MÉDIO/DIFÍCIL/COMPLEXO -> CONFORME (independente das categorias)
      3) Crítico=Sim e Dificuldade=FÁCIL -> NÃO CONFORME (mesmo que categorias coincidam)

    Observação: comparação é normalizada (sem acento / case-insensitive).
    """
    try:
        from report_falhas.config_report import (
            SUP_COL_CONCL,
            SUP_COL_CRIT,
            SUP_COL_DIFIC,
            SUP_COL_DUVIDA,
        )
    except ImportError:
        SUP_COL_DUVIDA = 'Dúvida'
        SUP_COL_CONCL = 'Conclusão'
        SUP_COL_DIFIC = 'Grau de dificuldade'
        SUP_COL_CRIT = 'Crítico'

    duv = safe_str(row.get(SUP_COL_DUVIDA, ''))
    conc = safe_str(row.get(SUP_COL_CONCL, ''))
    dif = normalize_text(safe_str(row.get(SUP_COL_DIFIC, '')))
    crit = normalize_text(safe_str(row.get(SUP_COL_CRIT, '')))

    # Regra 2
    if dif in {'medio', 'dificil', 'complexo'}:
        return 'CONFORME'

    # Regra 3
    if crit == 'sim' and dif == 'facil':
        return 'NÃO CONFORME'

    # Regra 1
    if normalize_text(duv) and normalize_text(duv) == normalize_text(conc):
        return 'CONFORME'

    return 'NÃO CONFORME'



# Schema novo (SharePoint TB_SUPORTE_OPERACIONAL) → colunas canônicas do report/portal.
# Mantém compatibilidade com a planilha legada (Data, Protocolo, Agente, …).
_SUPORTE_TBSO_ALIASES = {
    'tbso_data_cadastro': 'Data',
    'tbso_protocolo': 'Protocolo',
    'tbso_cliente': 'Cliente',
    'tbso_workflow': 'Workflow',
    'tbso_matricula_operacao': 'Agente',
    'tbso_duvida': 'Dúvida',
    'tbso_resultado_correto': 'Conclusão',
    'tbso_grau_de_dificuldade': 'Grau de dificuldade',
    'tbso_critico': 'Crítico',
    'tbso_uf': 'UF de emissão',
    'tbso_tipo_de_documento': 'Tipo de documento',
    'tbso_observacao_suporte': 'Observação suporte',
    'tbso_observacao_solicitante': 'Observação',
    'tbso_tipo_de_solicitacao': 'Tipo de solicitação',
    'tbso_localidade_solicitante': 'Localidade solicitante',
    'tbso_lider_solicitante': 'Líder solicitante',
    'tbso_conformidade': 'Conformidade',
}


def _normalize_suporte_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia colunas TBSO_* / legadas para o contrato canônico do report."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    df = df.copy()
    df.rename(columns=lambda c: str(c).strip(), inplace=True)
    # Remove colunas de export SharePoint sem valor analítico
    drop_cols = [c for c in df.columns if str(c).startswith('Unnamed')]
    if drop_cols:
        df.drop(columns=drop_cols, inplace=True, errors='ignore')

    rename = {}
    for col in list(df.columns):
        key = normalize_text(str(col).replace('\r', ' ').replace('\n', ' ').strip())
        target = _SUPORTE_TBSO_ALIASES.get(key)
        if target and target not in df.columns and col not in rename:
            rename[col] = target
    if rename:
        df.rename(columns=rename, inplace=True)

    norm_map = {normalize_text(str(c).strip()): str(c).strip() for c in df.columns}

    def pick(colname: str):
        return norm_map.get(normalize_text(colname))

    canonical_cols = [
        'Data', 'Protocolo', 'Cliente', 'Workflow',
        'Agente', 'Líder solicitante', 'Tipo de solicitação',
        'Dúvida', 'Conclusão', 'Localidade solicitante',
        'Observação', 'Observação suporte', 'Conformidade',
        'Grau de dificuldade', 'Crítico', 'Tipo de documento', 'UF de emissão',
    ]
    rename_legacy = {}
    for canonical in canonical_cols:
        if canonical in df.columns:
            continue
        orig_col = pick(canonical)
        if orig_col and orig_col != canonical:
            rename_legacy[orig_col] = canonical
    if rename_legacy:
        df.rename(columns=rename_legacy, inplace=True)

    # Schema TBSO: Agente = matrícula da operação; fallback solicitante.
    if 'Agente' not in df.columns:
        mat_op = pick('TBSO_MATRICULA_OPERACAO') or pick('matricula_operacao')
        mat_sol = pick('TBSO_MATRICULA_SOLICITANTE') or pick('matricula_solicitante')
        src = mat_op if (mat_op and mat_op in df.columns) else (
            mat_sol if (mat_sol and mat_sol in df.columns) else None
        )
        df['Agente'] = df[src] if src else ''

    # Descarta linhas totalmente vazias (export costuma trazer milhares de blanks).
    if 'Data' in df.columns or 'Protocolo' in df.columns:
        has_data = (
            df['Data'].notna()
            if 'Data' in df.columns
            else pd.Series(False, index=df.index)
        )
        has_proto = (
            df['Protocolo'].fillna('').astype(str).str.strip().ne('')
            if 'Protocolo' in df.columns
            else pd.Series(False, index=df.index)
        )
        df = df[has_data | has_proto].copy()

    # Sempre recalcula após o rename — evita Conformidade (Regra) gerada com colunas TBSO cruas.
    try:
        df['Conformidade (Regra)'] = df.apply(compute_conformidade_suporte, axis=1)
    except Exception:
        df['Conformidade (Regra)'] = ''

    return df


def read_suporte(path: str, sheet_name: str = 'Suporte') -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
    except Exception:
        try:
            xl = pd.ExcelFile(path, engine='openpyxl')
            sh = None
            for s in xl.sheet_names:
                if 'suporte' in normalize_text(str(s)):
                    sh = s
                    break
            if sh is None:
                raise
            df = pd.read_excel(path, sheet_name=sh, engine='openpyxl')
        except Exception:
            df = pd.DataFrame()
    return _normalize_suporte_schema(df)


def read_treinamentos(path: str, sheet_name: str = 'Treinamentos') -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
    except Exception:
        try:
            xl = pd.ExcelFile(path, engine='openpyxl')
            sh = None
            for s in xl.sheet_names:
                if 'trein' in normalize_text(str(s)):
                    sh = s
                    break
            if sh is None:
                raise
            df = pd.read_excel(path, sheet_name=sh, engine='openpyxl')
        except Exception:
            return pd.DataFrame()

    df.rename(columns=lambda c: str(c).strip(), inplace=True)
    norm_map = {normalize_text(str(c).strip()): str(c).strip() for c in df.columns}

    def pick(colname: str):
        return norm_map.get(normalize_text(colname))

    canonical = {
        'Agent: UserLanID': pick('Agent: UserLanID'),
        'Agent': pick('Agent'),
        'Event': pick('Event'),
        'Event: EventTitle': pick('Event: EventTitle'),
        'Status': pick('Status'),
        'AssignmentDate': pick('AssignmentDate'),
        'SignatureDate': pick('SignatureDate'),
        'Event:EventDeadline': pick('Event:EventDeadline'),
        'Session: StartDate': pick('Session: StartDate'),
        'Session: FinalDate': pick('Session: FinalDate'),
        'Created': pick('Created'),
        'Modified': pick('Modified'),
    }

    rename = {orig: canon for canon, orig in canonical.items() if orig and orig != canon}
    if rename:
        df.rename(columns=rename, inplace=True)

    for col in ['Agent: UserLanID', 'Agent', 'Event', 'Event: EventTitle', 'Status']:
        if col not in df.columns:
            df[col] = ''

    for dcol in ['AssignmentDate', 'SignatureDate', 'Event:EventDeadline', 'Session: StartDate', 'Session: FinalDate', 'Created', 'Modified']:
        if dcol not in df.columns:
            df[dcol] = pd.NaT
        else:
            df[dcol] = safe_to_datetime(df[dcol])

    df['Agent: UserLanID'] = df['Agent: UserLanID'].apply(safe_str)
    df['Agent'] = df['Agent'].apply(safe_str)
    df['Event'] = df['Event'].apply(safe_str)
    df['Event: EventTitle'] = df['Event: EventTitle'].apply(safe_str)
    df['Status'] = df['Status'].apply(safe_str)
    df['mat_norm'] = df['Agent: UserLanID'].apply(norm_matricula)

    return df

