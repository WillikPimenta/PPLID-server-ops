# -*- coding: utf-8 -*-
"""Orientações de tipo de achado e validações para o CS."""
from __future__ import annotations

import pandas as pd

from report_brb.brb_normalize import safe_str, strip_accents

FINDING_TYPE_GUIDANCE: dict[str, str] = {
    "Automático": "Calibrar regras, limites e exceções.",
    "Mapeamento": "Revisar cobertura e classificação dos cenários.",
    "Manual": "Reforçar procedimento, orientação ou barreira operacional.",
    "Processual": "Revisar fluxo, etapa e governança do produto.",
    "Não informado": "Completar a classificação do registro.",
}


def is_sensitive_scenario(value: str) -> bool:
    normalized = strip_accents(safe_str(value)).upper()
    return "FACE" in normalized and "BASE DE FRAUDADORES" in normalized


def short_scenario_label(value: str) -> str:
    cleaned = safe_str(value).strip()
    for prefix in (
        "NAO SINALIZADO - ",
        "NAO SINALIZADO – ",
        "SINALIZACAO INCORRETA - ",
        "SINALIZACAO INCORRETA – ",
        "DOC. ",
    ):
        if cleaned.upper().startswith(prefix.upper()):
            cleaned = cleaned[len(prefix) :].strip()
    return cleaned[:96] if cleaned else ""


def cs_validation_items(
    label: str,
    subset: pd.DataFrame,
    scenario_counts: pd.Series,
) -> list[str]:
    items: list[str] = []
    if label == "Processual":
        items.extend(
            [
                "Descrever o passo a passo esperado e comparar com a etapa em que o achado ocorreu.",
                "Validar no produto status, permissões, fila, SLA e transições disponíveis para o caso.",
                "Confirmar se a regra de negócio e a configuração do workflow produzem a decisão esperada.",
                "Registrar impacto para o cliente, evidência reproduzível, responsável e critério de aceite da correção.",
            ]
        )
        items.extend(
            [
                "Confirmar se a etapa/registro reflete o fluxo e o produto contratado com o cliente.",
                "Validar políticas, exceções e SLAs documentados para o processo observado.",
                "Verificar se a configuração do workflow cobre o cenário antes de tratar como falha operacional.",
                "Alinhar com Operação e Produto quando o achado indicar gap de processo, não execução pontual.",
            ]
        )
        if "Etapa" in subset.columns:
            stages = subset["Etapa"].fillna("").astype(str).str.strip()
            for stage, count in stages[stages.ne("")].value_counts().head(2).items():
                items.append(
                    f"Etapa recorrente: {stage} ({int(count)} achado(s)) — validar se pertence ao desenho acordado do produto."
                )
    elif label == "Automático":
        items.extend(
            [
                "Validar limites, thresholds e exceções da regra com o time de Produto/Regras.",
                "Confirmar se o comportamento esperado do motor está alinhado ao que o CS comunicou ao cliente.",
            ]
        )
    elif label == "Mapeamento":
        items.extend(
            [
                "Validar cobertura do mapeamento e critérios de classificação com Operação.",
                "Confirmar exceções documentadas para cenários limítrofes.",
            ]
        )
    elif label == "Manual":
        items.extend(
            [
                "Validar procedimento, checklist e exemplos usados na decisão operacional.",
                "Confirmar se a orientação ao time reflete o acordado com o cliente.",
            ]
        )
    for scenario, count in scenario_counts.head(2).items():
        short = short_scenario_label(str(scenario))
        if not short:
            continue
        if label == "Processual":
            items.append(
                f"Cenário recorrente: {short} ({int(count)} achado(s)) — validar regra de negócio e etapa de origem no produto."
            )
        else:
            items.append(
                f"Cenário recorrente: {short} ({int(count)} achado(s)) — validar critério e exceções com o CS."
            )
    return items[:9]
