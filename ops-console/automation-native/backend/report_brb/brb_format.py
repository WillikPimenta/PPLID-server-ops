# -*- coding: utf-8 -*-
"""Formatação de células e rótulos para exibição HTML."""
from __future__ import annotations

import html as html_lib
from datetime import datetime

import numpy as np
import pandas as pd

HIDDEN_COLS = frozenset(
    {
        "_protocolo_norm",
        "_matricula_norm",
        "chave_caso",
        "matricula_ausente",
        "classificacao_conforme",
        "possivel_ataque",
        "divergencia_risco",
        "fn_fp",
        "sem_matricula_fg",
        "matricula_fg",
        "status_cruzamento",
        "Cenario_Contestacao",
        "Origem",
        "match_na",
        "avaliado_contestacao",
        "sem_avaliacao_contestacao",
        "agente_treinado",
        "mes_ref",
        "dt_falha",
        "dt_trein",
        "chave_protocolo_matricula",
        "chave_protocolo",
    }
)

COLUMN_LABELS: dict[str, str] = {
    "PROTOCOLO": "Protocolo",
    "Protocolo": "Protocolo",
    "DATA DE CADASTRO": "Data cadastro",
    "DATA DE NOTIFICAÇÃO": "Data notificação",
    "DEMANDA": "Demanda",
    "demanda_inferida": "Demanda inferida",
    "MOTIVO DA FALHA": "Motivo",
    "RESULTADO DO CLIENTE": "Resultado cliente",
    "RESULTADO DA AUDITORIA": "Resultado auditoria",
    "Quantidade de Protolocos": "Qtd. protocolos",
    "Data da Abertura": "Data abertura",
    "Matrícula Agente": "Matrícula",
    "Matrícula": "Matrícula",
    "Nome Agente": "Agente",
    "Novo cenário": "Cenário",
    "Cenário": "Cenário",
    "CONFORME": "CONFORME",
    "Colaborador": "Colaborador",
    "Data de Análise": "Data análise",
    "Tipo de falha": "Tipo de falha",
    "n_matriculas": "Nº matrículas",
    "Situação": "Situação",
    "Status": "Status",
    "Quantidade": "Quantidade",
    "Motivo": "Motivo",
    "Mês": "Mês",
    "Demanda": "Demanda",
    "categoria_macro": "Categoria",
    "severidade": "Severidade",
    "descricao_padrao": "Descrição",
    "chave_demanda": "Demanda QI",
    "origem_base": "Origem",
    "Ocorrências": "Ocorrências",
    "Falhas": "Falhas",
    "Falhas pós-treino": "Falhas pós-treino",
    "Treinamentos": "Treinamentos",
    "Categoria": "Categoria",
    "Severidade": "Severidade",
    "Agente": "Agente",
    "Descrição": "Descrição",
}

DATE_COL_HINTS = (
    "data",
    "date",
    "cadastro",
    "notifica",
    "abertura",
    "análise",
    "analise",
    "retorno",
    "assignment",
    "signature",
    "session",
)

PROTOCOL_COL_HINTS = ("protocolo", "PROTOCOLO")


def _is_empty(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and np.isnan(val):
        return True
    if pd.isna(val):
        return True
    s = str(val).strip().lower()
    return s in ("", "nan", "none", "nat", "<na>")


def format_date_br(val) -> str:
    """Único formatador de data: dd/mm/yyyy (sem hora)."""
    if _is_empty(val):
        return "—"
    if isinstance(val, bool):
        return "—"
    from report_brb.brb_filters import fix_swapped_dmy_if_future, parse_excel_date

    if isinstance(val, (datetime, pd.Timestamp)):
        dt = fix_swapped_dmy_if_future(pd.Timestamp(val))
        return dt.strftime("%d/%m/%Y")
    if isinstance(val, (int, float, np.integer, np.floating)):
        try:
            f = float(val)
            if 30000 < f < 60000:
                dt = fix_swapped_dmy_if_future(
                    pd.Timestamp("1899-12-30") + pd.Timedelta(days=f)
                )
                return dt.strftime("%d/%m/%Y")
        except (ValueError, OverflowError):
            pass
    parsed = parse_excel_date(pd.Series([val]))
    if parsed.notna().iloc[0]:
        return parsed.iloc[0].strftime("%d/%m/%Y")
    s = str(val).strip()
    return s


def clean_text(val, max_len: int = 80) -> str:
    if _is_empty(val):
        return "—"
    if isinstance(val, bool):
        return "Sim" if val else "Não"
    s = str(val).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _is_date_col(col: str) -> bool:
    cu = col.lower()
    return any(h in cu for h in DATE_COL_HINTS)


def _is_protocol_col(col: str) -> bool:
    return col in PROTOCOL_COL_HINTS or col.lower() == "protocolo"


def format_int_br(n) -> str:
    """Inteiro com separador de milhar pt-BR (ex.: 136382 → 136.382)."""
    if n is None or (isinstance(n, float) and pd.isna(n)):
        return "—"
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


def format_cell(val, col: str = "") -> str:
    if _is_empty(val):
        return "—"
    if isinstance(val, bool) or (col == "demanda_inferida"):
        if col == "demanda_inferida":
            return "Sim" if val in (True, "True", "true", 1) else "Não"
        return "Sim" if val else "Não"
    if _is_date_col(col) or isinstance(val, (datetime, pd.Timestamp)):
        if isinstance(val, (datetime, pd.Timestamp)) or (
            isinstance(val, (int, float)) and 30000 < float(val) < 60000
        ):
            return format_date_br(val)
    if _is_protocol_col(col):
        if isinstance(val, (int, float, np.integer)) and not isinstance(val, bool):
            if float(val).is_integer():
                return str(int(float(val)))
        return str(val).strip()
    if isinstance(val, float) and float(val).is_integer():
        return str(int(val))
    return clean_text(val)


def column_label(col: str) -> str:
    if col in COLUMN_LABELS:
        return COLUMN_LABELS[col]
    if col in HIDDEN_COLS:
        return col
    return col.replace("_", " ").strip().title()


def badge_conforme(val) -> str:
    v = str(val).strip().upper() if not _is_empty(val) else ""
    v = v.replace("Ã", "A").replace("Õ", "O")
    if v in ("SIM", "S"):
        return '<span class="badge badge-sim">Sim</span>'
    if v in ("NÃO", "NAO", "N"):
        return '<span class="badge badge-nao">Não</span>'
    return f'<span class="badge badge-muted">{html_lib.escape(clean_text(val, 20))}</span>'


def badge_inferida(val) -> str:
    if val in (True, "True", "true", 1, "Sim", "SIM"):
        return '<span class="badge badge-inferida">Inferida</span>'
    return '<span class="badge badge-muted">—</span>'


def badge_fn_fp(val) -> str:
    v = str(val).strip().upper() if not _is_empty(val) else ""
    if v == "FN":
        return '<span class="badge badge-fn">FN</span>'
    if v == "FP":
        return '<span class="badge badge-fp">FP</span>'
    return "—"


def badge_severidade(val) -> str:
    v = str(val).strip().upper() if not _is_empty(val) else "INDEFINIDA"
    css = {
        "CRITICA": "sev-critica",
        "ALTA": "sev-alta",
        "MEDIA": "sev-media",
        "BAIXA": "sev-baixa",
        "INDEFINIDA": "sev-indef",
    }.get(v, "sev-indef")
    labels = {
        "CRITICA": "Crítica",
        "ALTA": "Alta",
        "MEDIA": "Média",
        "BAIXA": "Baixa",
        "INDEFINIDA": "Indefinida",
    }
    return f'<span class="badge {css}">{labels.get(v, clean_text(val, 20))}</span>'


def badge_categoria(val) -> str:
    from report_brb.brb_classify import categoria_label

    code = str(val).strip() if not _is_empty(val) else "OUTROS"
    return f'<span class="badge cat-badge">{html_lib.escape(categoria_label(code))}</span>'


def cell_html(val, col: str, esc_fn) -> str:
    """Retorna HTML da célula com badges quando aplicável."""
    col_lower = col.lower()
    if col in ("CONFORME",) or col_lower == "conforme":
        return badge_conforme(val)
    if col == "demanda_inferida":
        return badge_inferida(val)
    if col == "fn_fp":
        return badge_fn_fp(val)
    if col in ("severidade", "Severidade"):
        return badge_severidade(val)
    if col in ("categoria_macro", "Categoria"):
        return badge_categoria(val)
    text = format_cell(val, col)
    full = format_cell(val, col) if len(str(val or "")) <= 80 else clean_text(val, 500)
    css = "cell-mono cell-right" if _is_protocol_col(col) else ""
    css = (css + " cell-text") if len(text) > 40 else css
    title = ""
    if full != text and full != "—":
        title = f' title="{esc_fn(full)}"'
    return f'<td class="{css.strip()}"{title}>{esc_fn(text)}</td>'


def mes_label_pt(ts: pd.Timestamp) -> str:
    meses = (
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
        "Outubro",
        "Novembro",
        "Dezembro",
    )
    return f"{meses[ts.month - 1]} {ts.year}"
