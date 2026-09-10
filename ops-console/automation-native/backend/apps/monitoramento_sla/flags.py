# -*- coding: utf-8 -*-
"""Flags de homologação (§24 da especificação) — reproduzem o modelo Power BI literal."""

from __future__ import annotations

# Rank vs volume na classificação ajustada: Power BI usa >= (comentário sugere <=).
RANK_VOLUME_OP = ">="  # valores: ">=" | "<="

# Protocolos abertos na regra padrão: modelo classifica como Dentro mesmo acima do limite.
OPEN_DEFAULT_DENTRO = True

# Ranking natural vs limite de volume: modelo usa Rank < Limite (não <=).
RANK_NATURAL_LT = True

# Fronteira SLA superior: aceita somente SLA útil < SLA_Ajuste.
SLA_AJUSTE_STRICT_LT = True

# Workflow 450 D+2U a partir desta data (inclusive).
WF450_D2U_FROM = "2026-05-01"
WF450_ID = 450

# Comparação de aberto no D+2U: usar data de corte da execução (recomendado) vs max cadastro.
D2U_OPEN_USE_CUTOVER = True
