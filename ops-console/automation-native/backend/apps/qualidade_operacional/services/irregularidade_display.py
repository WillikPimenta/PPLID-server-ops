# -*- coding: utf-8 -*-
"""Texto operacional de irregularidade apontada para listagens de detalhe."""

from __future__ import annotations


def irregularidade_apontada_falha(*, des_problemas: str = "", cenario: str = "") -> str:
    return (des_problemas or cenario or "").strip()


def irregularidade_apontada_auditado(
    *, irregularidades_apontadas: str = "", cenario: str = ""
) -> str:
    return (irregularidades_apontadas or cenario or "").strip()
