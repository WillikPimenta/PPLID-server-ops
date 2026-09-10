# -*- coding: utf-8 -*-
"""Renderização Excel analítico BRB."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from report_brb.brb_analytics import BRBAnalytics, compute_analytics
from report_brb.brb_classify import categoria_label, severidade_label
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics, build_procedencia_join, resumo_dict
from report_brb.brb_reconciliation import build_reconciliation, reconciliation_to_df
from report_brb.brb_timeline import build_timeline

DICIONARIO = pd.DataFrame(
    [
        ("chave_caso", "Protocolo normalizado + Matrícula normalizada"),
        ("CONFORME Sim", "Conforme — não é falha (improcedente)"),
        ("CONFORME Não", "É falha (procedente)"),
        ("possivel_ataque", "Motivo ou resultado com keywords de fraude/ataque"),
        ("fn_fp", "FN = Não sinalizado; FP = Sinalização incorreta/validada"),
        ("classificacao_conforme", "falha | nao_falha | sem_avaliacao | indefinido"),
        ("categoria_macro", "Categoria padronizada de falha (ADULTERACAO_VISUAL, etc.)"),
        ("severidade", "CRITICA | ALTA | MEDIA | BAIXA | INDEFINIDA"),
        ("match_na", "FG com protocolo encontrado em NA_Falhas"),
        ("Filtro BRB", "Cliente/EventTitle = BRB, Banco de Brasília ou aliases (DTVM/CFI)"),
    ],
    columns=["Campo/Regra", "Descrição"],
)


def _export_base(df: pd.DataFrame) -> pd.DataFrame:
    """Colunas legíveis para Excel."""
    if df.empty:
        return df
    out = df.copy()
    if "categoria_macro" in out.columns:
        out["Categoria"] = out["categoria_macro"].map(categoria_label)
    if "severidade" in out.columns:
        out["Severidade"] = out["severidade"].map(severidade_label)
    hide = [c for c in out.columns if c.startswith("_") or c in ("chave_caso", "mes_ref", "dt_falha", "dt_trein")]
    return out.drop(columns=[c for c in hide if c in out.columns], errors="ignore")


def _kpis_df(analytics: BRBAnalytics) -> pd.DataFrame:
    return pd.DataFrame(list(analytics.cruzamentos.items()), columns=["Indicador", "Valor"])


def _recomendacoes_df(analytics: BRBAnalytics) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Prioridade": r.prioridade, "Área": r.area, "Recomendação": r.texto} for r in analytics.recomendacoes]
    )


def _rastreabilidade_df(analytics: BRBAnalytics) -> pd.DataFrame:
    parts = []
    for origem, df in [
        ("FG sem match NA", analytics.fg_sem_match_na),
        ("FG sem CONFORME", analytics.fg_sem_contestacao),
        ("Dados incompletos", analytics.dados_incompletos),
        ("Sem categoria/severidade", analytics.sem_categoria_severidade),
    ]:
        if df is not None and not df.empty:
            t = _export_base(df.head(500))
            t.insert(0, "Tipo", origem)
            parts.append(t)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def render_excel(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    out_path: Path,
    metrics_full: BRBMetrics | None = None,
    periodo_label: str = "",
    analytics: BRBAnalytics | None = None,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if analytics is None:
        analytics = compute_analytics(bundle)

    resumo = pd.DataFrame(list(resumo_dict(metrics).items()), columns=["Indicador", "Valor"])
    procedencia = build_procedencia_join(bundle)
    timeline = build_timeline(bundle, limit=2000)

    na_export = bundle.na_falhas.copy()
    fg_export = bundle.falhas_gerais.copy()
    cont_export = bundle.contestacao.copy()

    recon_rows = build_reconciliation(metrics, bundle, metrics_full, periodo_label)
    recon = reconciliation_to_df(recon_rows)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        resumo.to_excel(writer, sheet_name="Resumo", index=False)
        recon.to_excel(writer, sheet_name="Reconciliacao", index=False)
        _export_base(analytics.base_consolidada).to_excel(writer, sheet_name="Base_Consolidada", index=False)
        _kpis_df(analytics).to_excel(writer, sheet_name="KPIs", index=False)
        _rastreabilidade_df(analytics).to_excel(writer, sheet_name="Rastreabilidade", index=False)
        analytics.reincidencia_agentes.to_excel(writer, sheet_name="Reincidencia", index=False)
        analytics.top_categorias.to_excel(writer, sheet_name="Categorias", index=False)
        analytics.por_severidade.to_excel(writer, sheet_name="Severidade", index=False)
        analytics.qualidade.to_excel(writer, sheet_name="Qualidade_Dados", index=False)
        _recomendacoes_df(analytics).to_excel(writer, sheet_name="Recomendacoes", index=False)
        bundle.na_demandas.to_excel(writer, sheet_name="NA_Demandas_BRB", index=False)
        na_export.to_excel(writer, sheet_name="NA_Falhas_BRB", index=False)
        fg_export.to_excel(writer, sheet_name="Falhas_Gerais_BRB", index=False)
        cont_export.to_excel(writer, sheet_name="Contestacao_BRB", index=False)
        procedencia.to_excel(writer, sheet_name="Procedencia", index=False)
        timeline.to_excel(writer, sheet_name="Timeline", index=False)
        bundle.treinamentos.to_excel(writer, sheet_name="Treinamentos_BRB", index=False)
        DICIONARIO.to_excel(writer, sheet_name="Dicionario", index=False)

    return out_path
