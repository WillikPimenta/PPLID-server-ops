# -*- coding: utf-8 -*-
"""Classificação de jobs bot→banco em filas high / mid / low.

Fila high: syncs pesados serializados (no máx. 1 por vez no host).
Fila mid: projeções da Qualidade, independentes das demais filas.
Fila low: syncs leves (no máx. 1 por vez); podem coexistir com high.
"""
from __future__ import annotations

LANE_HIGH = "high"
LANE_MIDDLE = "middle"
# Alias de compatibilidade; o valor canonico da lane e middle.
LANE_MID = LANE_MIDDLE
LANE_LOW = "low"
LANE_CHOICES = (
    (LANE_HIGH, "Alta (pesado)"),
    (LANE_MID, "Média (Projeção da Qualidade)"),
    (LANE_LOW, "Baixa (leve)"),
)
LANES = (LANE_HIGH, LANE_MIDDLE, LANE_LOW)

# A lane middle is generic: quality projection is one of its consumers,
# but other medium-weight synchronizations may use it as well.
LANE_CHOICES = (
    (LANE_HIGH, "Alta (pesado)"),
    (LANE_MIDDLE, "M\u00e9dia (peso m\u00e9dio)"),
    (LANE_LOW, "Baixa (leve)"),
)

_MID_DOMAINS = frozenset({"qualidade_projection"})

# Domínios inteiros tratados como high (qualquer report_type).
_HIGH_DOMAINS = frozenset(
    {
        "falhas_criticas",
        "monitoramento_sla",
    }
)

# Pares (domain, report_type) high. report_type vazio = qualquer do domínio
# já coberto por _HIGH_DOMAINS; aqui listamos rotina_bruto pesados.
_HIGH_DOMAIN_REPORT = frozenset(
    {
        ("rotina_bruto", "detalhado"),
        ("rotina_bruto", "prod"),
        ("rotina_bruto", "ged_detalhado"),
        ("rotina_bruto", "ged_irregularidade"),
        ("rotina_bruto", "g_auditoria"),
    }
)


def resolve_lane(domain: str, report_type: str = "") -> str:
    """Classifica o job; domínios desconhecidos continuam na fila low."""
    domain = (domain or "").strip()
    report_type = (report_type or "").strip()
    if domain in _HIGH_DOMAINS:
        return LANE_HIGH
    if (domain, report_type) in _HIGH_DOMAIN_REPORT:
        return LANE_HIGH
    if domain in _MID_DOMAINS:
        return LANE_MIDDLE
    return LANE_LOW


def normalize_lane(lane: str | None) -> str:
    value = (lane or "").strip().lower()
    if value == LANE_HIGH:
        return LANE_HIGH
    if value in {LANE_MIDDLE, "mid"}:
        return LANE_MIDDLE
    if value == LANE_LOW:
        return LANE_LOW
    raise ValueError(f"Lane inválida: {lane!r} (use high|mid|low)")
