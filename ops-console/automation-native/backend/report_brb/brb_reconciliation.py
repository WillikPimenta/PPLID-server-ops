# -*- coding: utf-8 -*-
"""Reconciliação de métricas — por que os números diferem."""
from __future__ import annotations

from dataclasses import dataclass

from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics


@dataclass
class ReconRow:
    metrica: str
    valor: str
    fonte: str
    unidade: str
    explicacao: str
    relacionado: str


def build_reconciliation(
    metrics: BRBMetrics,
    bundle: BRBDataBundle,
    metrics_full: BRBMetrics | None = None,
    periodo_label: str = "",
) -> list[ReconRow]:
    rows: list[ReconRow] = []

    period_note = ""
    if metrics_full and periodo_label and "sem filtro" not in periodo_label.lower():
        period_note = (
            f" Período filtrado ({periodo_label}): FG {metrics.casos_unicos_fg} vs "
            f"base total {metrics_full.casos_unicos_fg}."
        )

    rows.append(
        ReconRow(
            metrica="Demandas abertas (NA_Demandas)",
            valor=str(metrics.demandas_na_registros),
            fonte="NA_Demandas",
            unidade="Registros (linhas) no período",
            explicacao="Cada linha é uma demanda aberta (ex.: QI-xxxx por mês).",
            relacionado=(
                f"Volume agregado de protocolos informado: {metrics.protocolos_na} "
                "(soma Quantidade de Protolocos — não é contagem de demandas)."
                + period_note
            ),
        )
    )
    rows.append(
        ReconRow(
            metrica="Protocolos informados (soma agregada)",
            valor=str(metrics.protocolos_na),
            fonte="NA_Demandas",
            unidade="Soma Quantidade de Protolocos",
            explicacao="Volume comunicado em cada linha de demanda pelo cliente.",
            relacionado=(
                f"Difere de demandas abertas ({metrics.demandas_na_registros}) e de casos FG "
                f"({metrics.casos_unicos_fg}) — medidas distintas."
            ),
        )
    )
    rows.append(
        ReconRow(
            metrica="Casos únicos Falhas Gerais BRB",
            valor=str(metrics.casos_unicos_fg),
            fonte="Falhas_Gerais-BRB",
            unidade="Protocolo + Matrícula Agente",
            explicacao=(
                f"{metrics.linhas_fg} linhas na planilha → {metrics.casos_unicos_fg} casos únicos "
                f"({metrics.duplicatas_fg} duplicata(s)); {metrics.protocolos_distintos_fg} protocolos distintos."
            ),
            relacionado=(
                f"Não soma com Protocolos NA ({metrics.protocolos_na}) — são fluxos e unidades diferentes."
            ),
        )
    )
    rows.append(
        ReconRow(
            metrica="Falhas NA notificadas",
            valor=str(metrics.na_falhas_registros),
            fonte="NA_Falhas",
            unidade=f"{metrics.na_falhas_protocolos} protocolos distintos",
            explicacao="Uma linha por falha comunicada ao cliente via Notificação Ativa.",
            relacionado=(
                f"FG sem match NA: {metrics.fg_sem_na} protocolos em Falhas Gerais sem registro NA. "
                f"Demanda inferida em {metrics.na_demanda_inferida} linha(s)."
            ),
        )
    )
    rows.append(
        ReconRow(
            metrica="Contestação (casos únicos)",
            valor=str(metrics.contestacao_casos_unicos),
            fonte="Contestacao",
            unidade="Protocolo + Matrícula",
            explicacao=(
                f"CONFORME Sim {metrics.conforme_sim} (conforme) + Não {metrics.conforme_nao} (falha) "
                f"= {metrics.conforme_sim + metrics.conforme_nao} decisões."
            ),
            relacionado=(
                f"FG sem avaliação CONFORME: {metrics.fg_sem_contestacao}. "
                "Contestacao pode ter casos fora do FG do período."
            ),
        )
    )
    rows.append(
        ReconRow(
            metrica="Treinamentos",
            valor=str(metrics.treinamentos_registros),
            fonte="Treinamentos",
            unidade=f"{metrics.treinamentos_agentes} agentes distintos",
            explicacao="Eventos com BRB no título — indicador de capacitação, não de falha.",
            relacionado="Independente das contagens de falha; usado para contexto de ação corretiva.",
        )
    )
    if metrics.por_tendencia:
        top_t = max(metrics.por_tendencia.items(), key=lambda x: x[1])
        rows.append(
            ReconRow(
                metrica="Tendência dominante (FG)",
                valor=f"{top_t[0]} ({top_t[1]})",
                fonte="Falhas_Gerais-BRB · coluna Tendência",
                unidade="Linhas por classificação",
                explicacao="Classifica o tipo de achado na auditoria (não confundir com volume NA).",
                relacionado="Ver aba Falhas Gerais BRB para distribuição completa.",
            )
        )
    return rows


def reconciliation_to_df(rows: list[ReconRow]):
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "Métrica": r.metrica,
                "Valor": r.valor,
                "Fonte": r.fonte,
                "Unidade": r.unidade,
                "O que significa": r.explicacao,
                "Por que difere": r.relacionado,
            }
            for r in rows
        ]
    )
