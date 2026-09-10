# -*- coding: utf-8 -*-
"""
Excel de exportação para CS/cliente — bases organizadas, sem dados pessoais.
Sem matrícula, nome, CPF, e-mail, auditor, responsável etc.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from report_brb.brb_format import format_int_br
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics

# Tokens que indicam PII / identificação pessoal (coluna é removida se o nome casar)
_PII_TOKENS = (
    "matricula",
    "matricula",
    "cpf",
    "nome",
    "colaborador",
    "agente",
    "auditor",
    "lider",
    "líder",
    "facilitator",
    "email",
    "e-mail",
    "userlan",
    "lanid",
    "signatureuser",
    "responsavel",
    "responsável",
    "created by",
    "modified by",
    "assignmentby",
)

# Colunas permitidas por base (allowlist). Só entram se existirem no DataFrame.
_COLS_FALHAS_NA = (
    "CLIENTE",
    "WORKFLOW",
    "PROTOCOLO",
    "DATA DE CADASTRO",
    "DATA DE NOTIFICAÇÃO",
    "MÊS",
    "DEMANDA",
    "TIPO DE FALHA",
    "MOTIVO DA FALHA",
    "RESULTADO DO CLIENTE",
    "RESULTADO DA AUDITORIA",
    "LEGADO/CONSULTA",
)

_COLS_AUDITADOS = (
    "Data",
    "Data análise",
    "Protocolo",
    "Etapa",
    "Cliente",
    "Workflow",
    "Tipo de análise",
    "Cenário",
    "Novo cenário",
    "STATUS",
    "IRREGULARIDADES_APONTADAS",
    "Cadastrado anteriormente",
    "Resultado Destino",
    "Resultado Origem",
    "Protocolo Destino",
)

_COLS_FG = (
    "Data Auditoria",
    "Data de Análise",
    "Protocolo",
    "Etapa",
    "Cliente",
    "Categoria falha",
    "Cenário",
    "Novo cenário",
    "Workflow",
    "Módulo",
    "Tipo de análise",
    "Tipo de Falha",
    "Tipo de documento",
    "UF do documento",
    "Tipo de solicitação",
    "Tendência",
    "Segmento",
    "Sub Segmento",
    "fn_fp",
)

_COLS_CONTESTACAO = (
    "Cliente",
    "Protocolo",
    "Data",
    "Data de Análise",
    "Data de Cadastro",
    "Data de Conclusão",
    "Workflow",
    "Etapa",
    "Status",
    "CONFORME",
    "classificacao_conforme",
    "Cenário",
    "Tipo de falha",
    "Tipo de documento",
    "UF",
    "Tipo de solicitação",
    "Resultado da Análise",
    "Prioridade",
    # "Origem" omitido: traz nomes de analistas (ex.: Cadu, Elton)
)

_COLS_DEMANDAS = (
    "Demanda",
    "Cliente",
    "Mês",
    "Quantidade de Protolocos",
    "Falhas Manuais",
    "Falhas Processuais",
    "Falhas Automáticas",
    "Alteração Aprovada?",
    "Data da Abertura",
    "Situação",
    "Tempo em Aberto",
    "Data do Retorno",
)

_COLS_CAPACITACAO = (
    "Event: EventTitle",
    "StartDate",
    "FinalDate",
    "SessionStatus",
    "Event: EstimatedDuration",
    "horas",
    "Event: EventDemmand",
    "Room: RoomState",
)

_RENAME = {
    "CLIENTE": "Cliente",
    "WORKFLOW": "Workflow",
    "PROTOCOLO": "Protocolo",
    "DATA DE CADASTRO": "Data cadastro",
    "DATA DE NOTIFICAÇÃO": "Data notificação",
    "MÊS": "Mês",
    "DEMANDA": "Demanda",
    "TIPO DE FALHA": "Tipo de falha",
    "MOTIVO DA FALHA": "Motivo da falha",
    "RESULTADO DO CLIENTE": "Resultado do cliente",
    "RESULTADO DA AUDITORIA": "Resultado da auditoria",
    "LEGADO/CONSULTA": "Legado/consulta",
    "Notificado Teams": "Notificado Teams",
    "NOTIFICADO JIRA?": "Notificado Jira",
    "IDENTIFICADO FORA DO G.A?": "Identificado fora do GA",
    "IRREGULARIDADES_APONTADAS": "Irregularidades apontadas",
    "STATUS": "Status",
    "fn_fp": "FN/FP",
    "classificacao_conforme": "Classificação",
    "Event: EventTitle": "Evento",
    "StartDate": "Início",
    "FinalDate": "Fim",
    "SessionStatus": "Status da sessão",
    "Event: EstimatedDuration": "Duração estimada",
    "horas": "Horas",
    "Event: EventDemmand": "Demanda (QI)",
    "Room: RoomState": "Local",
    "Quantidade de Protolocos": "Quantidade de protocolos",
}


def _norm_col(name: str) -> str:
    from report_brb.brb_normalize import strip_accents

    return strip_accents(str(name)).lower().strip()


def _is_pii_column(name: str) -> bool:
    n = _norm_col(name)
    if n in ("nome da origem",):  # campo de sistema, não pessoa
        return False
    return any(tok in n for tok in _PII_TOKENS)


def _pick_columns(df: pd.DataFrame, allow: tuple[str, ...]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    cols = [c for c in allow if c in df.columns and not _is_pii_column(c)]
    # Também remove qualquer coluna allowlist que por engano tenha PII no nome
    out = df.loc[:, cols].copy()
    # Segurança extra: dropa colunas PII que tenham entrado por alias
    drop = [c for c in out.columns if _is_pii_column(c)]
    if drop:
        out = out.drop(columns=drop, errors="ignore")
    # Remove internos _
    hide = [c for c in out.columns if str(c).startswith("_")]
    out = out.drop(columns=hide, errors="ignore")
    return out.rename(columns={k: v for k, v in _RENAME.items() if k in out.columns})


def _strip_remaining_pii(df: pd.DataFrame) -> pd.DataFrame:
    """Cinto de segurança: remove qualquer coluna PII restante."""
    if df.empty:
        return df
    keep = [c for c in df.columns if not _is_pii_column(c)]
    return df.loc[:, keep].copy()


def _drop_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Remove colunas 100% vazias (NaN / string em branco) — não fazem sentido no export."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    keep: list[str] = []
    for c in df.columns:
        s = df[c]
        if s.isna().all():
            continue
        as_str = s.astype(str).str.strip()
        blank = as_str.isna() | as_str.isin(("", "nan", "None", "NaT", "<NA>"))
        if bool(blank.all()):
            continue
        keep.append(c)
    return df.loc[:, keep].copy() if keep else pd.DataFrame()


def _legenda_df(periodo_label: str, metrics: BRBMetrics) -> pd.DataFrame:
    rows = [
        ("Documento", "Exportação Quality / CS — bases do período sem dados pessoais"),
        ("Período", periodo_label or "—"),
        ("Privacidade", "Removidos: matrícula, nome, CPF, e-mail, auditor, agente, responsáveis e nomes de analistas"),
        ("Protocolo", "Mantido como identificador operacional do caso (não é dado cadastral de pessoa)"),
        ("", ""),
        ("Aba", "Conteúdo"),
        ("01_Resumo", "Indicadores agregados do período"),
        ("02_Falhas_Notificadas", "Falhas NA sinalizadas ao CS (sem auditor/pessoa)"),
        ("03_Casos_Auditados", "Volume auditado por protocolo/etapa (sem matrícula/nome)"),
        ("04_Achados_Auditoria", "Achados FG tipificados (sem agente/líder)"),
        ("05_Contestacao", "Avaliações CONFORME (sem CPF/matrícula/colaborador)"),
        ("06_Demandas", "Lotes/QI informados (sem responsáveis)"),
        ("07_Capacitacao_Horas", "Esforço em horas por sessão (sem facilitador)"),
        ("", ""),
        ("Falhas NA (registros)", format_int_br(metrics.na_falhas_registros)),
        ("Casos auditados", format_int_br(metrics.auditados_casos)),
        ("Procedência acumulada", f"{metrics.pct_procedente}%"),
        ("Capacitação (horas)", f"{metrics.treinamentos_horas} h"),
    ]
    return pd.DataFrame(rows, columns=["Campo", "Descrição"])


def _resumo_df(metrics: BRBMetrics) -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("Solicitações (demandas)", metrics.demandas_na_registros),
            ("Protocolos informados nos lotes", metrics.protocolos_na),
            ("Falhas notificadas (NA)", metrics.na_falhas_registros),
            ("Protocolos distintos NA", metrics.na_falhas_protocolos),
            ("Casos auditados", metrics.auditados_casos),
            ("Registros/etapas auditados", metrics.auditados_registros),
            ("Razão descritiva NA/auditados (%)", metrics.auditados_taxa_achado),
            ("Achados de auditoria (FG)", metrics.casos_unicos_fg),
            ("Avaliações de contestação", metrics.conforme_sim + metrics.conforme_nao),
            ("Procedentes", metrics.conforme_nao),
            ("Improcedentes", metrics.conforme_sim),
            ("Procedência (%)", metrics.pct_procedente),
            ("Capacitação — horas", metrics.treinamentos_horas),
            ("Capacitação — sessões", metrics.treinamentos_sessoes),
        ],
        columns=["Indicador", "Valor"],
    )


def _auditados_mensal_df(metrics: BRBMetrics) -> pd.DataFrame:
    m = metrics.auditados_mensal
    if m is None or m.empty:
        return pd.DataFrame()
    out = m.copy()
    keep = [c for c in ("label", "casos", "registros", "falhas", "taxa_achado") if c in out.columns]
    out = out[keep].rename(
        columns={
            "label": "Mês",
            "casos": "Casos auditados",
            "registros": "Registros",
            "falhas": "Falhas notificadas",
            "taxa_achado": "Razão (%)",
        }
    )
    return out


def render_excel_cliente(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    out_path: Path,
    *,
    periodo_label: str = "",
) -> Path:
    """Gera workbook limpo para compartilhar com CS/cliente."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    falhas = _drop_empty_columns(
        _strip_remaining_pii(_pick_columns(bundle.na_falhas, _COLS_FALHAS_NA))
    )
    auditados = _drop_empty_columns(
        _strip_remaining_pii(_pick_columns(bundle.auditados, _COLS_AUDITADOS))
    )
    fg = _drop_empty_columns(
        _strip_remaining_pii(_pick_columns(bundle.falhas_gerais, _COLS_FG))
    )
    cont = _drop_empty_columns(
        _strip_remaining_pii(_pick_columns(bundle.contestacao, _COLS_CONTESTACAO))
    )
    dem = _drop_empty_columns(
        _strip_remaining_pii(_pick_columns(bundle.na_demandas, _COLS_DEMANDAS))
    )
    cap = _drop_empty_columns(
        _strip_remaining_pii(
            _pick_columns(getattr(bundle, "treinamentos_horas", pd.DataFrame()), _COLS_CAPACITACAO)
        )
    )
    mensal = _auditados_mensal_df(metrics)

    # Classificação amigável na contestação
    if not cont.empty and "Classificação" in cont.columns:
        cont["Classificação"] = cont["Classificação"].replace(
            {"falha": "Procedente", "nao_falha": "Improcedente", "sem_avaliacao": "Sem avaliação"}
        )

    # Exportação para apresentação: somente as duas bases solicitadas pelo CS.
    # A legenda, resumo, auditoria, demandas e capacitação ficam fora deste arquivo.
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        falhas.to_excel(writer, sheet_name="02_Falhas_Notificadas", index=False)
        cont.to_excel(writer, sheet_name="05_Contestacao", index=False)

    _assert_no_pii(out_path)
    return out_path


def _assert_no_pii(path: Path) -> None:
    """Valida que nenhuma aba exportada trouxe coluna PII."""
    xl = pd.ExcelFile(path)
    bad: list[str] = []
    for sheet in xl.sheet_names:
        cols = list(pd.read_excel(path, sheet, nrows=0).columns)
        for c in cols:
            if _is_pii_column(str(c)):
                bad.append(f"{sheet}.{c}")
    if bad:
        raise ValueError(f"Export cliente contém colunas pessoais: {bad}")
