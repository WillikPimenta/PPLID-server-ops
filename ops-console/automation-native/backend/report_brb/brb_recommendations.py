# -*- coding: utf-8 -*-
"""Recomendações automáticas com base nos indicadores calculados."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from report_brb.brb_analytics import BRBAnalytics


@dataclass
class Recomendacao:
    prioridade: str  # alta | media | baixa
    texto: str
    area: str


def gerar_recomendacoes(analytics: "BRBAnalytics") -> list[Recomendacao]:
    recs: list[Recomendacao] = []
    c = analytics.cruzamentos
    total_fg = c.get("casos_fg", 0) or 1

    if c.get("pct_fg_sem_na", 0) > 10:
        recs.append(
            Recomendacao(
                "alta",
                f"Há {c.get('fg_sem_match_na', 0)} casos FG ({c.get('pct_fg_sem_na')}%) sem match em "
                "Notificação Ativa. Recomenda-se revisar a integração entre Falhas Gerais e NA_Falhas.",
                "integracao",
            )
        )

    if c.get("pct_fg_sem_av", 0) > 10:
        recs.append(
            Recomendacao(
                "alta",
                f"Há {c.get('fg_sem_contestacao', 0)} casos ({c.get('pct_fg_sem_av')}%) sem avaliação CONFORME. "
                "Recomenda-se priorizar a conclusão da análise de procedência.",
                "procedencia",
            )
        )

    if not analytics.top_categorias.empty:
        top = analytics.top_categorias.iloc[0]
        pct = round(100 * top["Quantidade"] / total_fg, 1)
        if pct > 30:
            recs.append(
                Recomendacao(
                    "alta",
                    f"A categoria '{top['Categoria']}' representa {pct}% das falhas. "
                    "Recomenda-se plano de ação específico para essa categoria.",
                    "categoria",
                )
            )

    if not analytics.por_severidade.empty:
        crit = analytics.por_severidade[analytics.por_severidade["Severidade"] == "CRITICA"]
        if not crit.empty:
            n_crit = int(crit.iloc[0]["Quantidade"])
            pct_crit = round(100 * n_crit / total_fg, 1)
            if pct_crit > 20:
                recs.append(
                    Recomendacao(
                        "alta",
                        f"Severidade CRÍTICA representa {pct_crit}% dos casos ({n_crit}). "
                        "Recomenda-se plano emergencial de revisão.",
                        "severidade",
                    )
                )

    if not analytics.reincidencia_agentes.empty:
        n_rein = len(analytics.reincidencia_agentes)
        recs.append(
            Recomendacao(
                "media",
                f"Há {n_rein} agente(s) reincidentes. Recomenda-se feedback individualizado e reciclagem.",
                "reincidencia",
            )
        )

    if not analytics.treinamento_pos_falha.empty:
        recs.append(
            Recomendacao(
                "media",
                f"{len(analytics.treinamento_pos_falha)} agente(s) com falha após treinamento. "
                "Recomenda-se medir efetividade da capacitação.",
                "capacitacao",
            )
        )
    elif c.get("treinamentos", 0) > 0 and c.get("casos_fg", 0) > 0:
        recs.append(
            Recomendacao(
                "baixa",
                "Há volume de treinamentos, mas a relação com redução de falhas não pôde ser medida com precisão. "
                "Recomenda-se acompanhar indicadores pós-capacitação.",
                "capacitacao",
            )
        )

    for alerta in analytics.alertas_qualidade:
        if alerta["nivel"] == "vermelho":
            recs.append(
                Recomendacao(
                    "alta",
                    f"Qualidade dos dados — {alerta['texto']}. Revisar fonte e preenchimento.",
                    "qualidade",
                )
            )

    if not recs:
        recs.append(
            Recomendacao(
                "baixa",
                "Indicadores dentro de faixas esperadas. Manter monitoramento periódico.",
                "geral",
            )
        )

    return recs


# Textos curtos para a visão executiva (máx. 3).
_RECOMENDACOES_EXECUTIVAS: dict[str, str] = {
    "integracao": "Revisar casos FG sem match em NA para corrigir lacunas de rastreabilidade.",
    "procedencia": "Priorizar avaliação dos casos FG sem Contestação (CONFORME pendente).",
    "categoria": "Criar plano de ação para a categoria de falha mais recorrente.",
    "severidade": "Acionar plano emergencial para casos de severidade crítica.",
    "reincidencia": "Aplicar feedback individualizado aos agentes reincidentes.",
    "capacitacao": "Medir efetividade dos treinamentos e reincidência pós-capacitação.",
    "qualidade": "Corrigir lacunas de qualidade dos dados nas fontes operacionais.",
    "geral": "Manter monitoramento periódico dos indicadores principais.",
}

_PRIORIDADE_ORDEM = {"alta": 0, "media": 1, "baixa": 2}
_ALERTA_ORDEM = {"vermelho": 0, "amarelo": 1, "verde": 2}


def priorizar_alertas(alertas: list[dict]) -> list[dict]:
    """Ordena alertas: crítico → atenção → informativo."""
    return sorted(alertas, key=lambda a: _ALERTA_ORDEM.get(a.get("nivel"), 9))


def gerar_recomendacoes_executivas(analytics: "BRBAnalytics") -> list[str]:
    """Até 3 recomendações curtas e acionáveis para o Resumo Executivo."""
    recs = gerar_recomendacoes(analytics)
    ordenadas = sorted(recs, key=lambda r: _PRIORIDADE_ORDEM.get(r.prioridade, 9))
    textos: list[str] = []
    for r in ordenadas:
        curto = _RECOMENDACOES_EXECUTIVAS.get(r.area)
        if curto and curto not in textos:
            textos.append(curto)
        elif len(textos) < 3:
            # Fallback: primeira frase da recomendação completa
            parte = r.texto.split(".")[0].strip()
            if parte and parte not in textos:
                textos.append(parte + ".")
        if len(textos) >= 3:
            break
    return textos[:3] or [_RECOMENDACOES_EXECUTIVAS["geral"]]
