# -*- coding: utf-8 -*-
"""Perfil sanitizado 110×7 alinhado ao export XLSX (forma e cardinalidades, sem PII)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from django.utils import timezone

from apps.auditoria.models import AuditoriaFalhaCadastro

SEED_PREFIX = "EO-REAL-"
TOTAL_ROWS = 110
REINSPECAO_COUNT = 101
CONTESTACAO_COUNT = 2
AUDITORIA_COUNT = 7

CLIENTE = "Cliente EO Simulado"
WORKFLOW = "Workflow EO Simulado"
AUDITOR = "eo.simulado"
AGENTE_COM_MATRICULA = "c90001a"

MOTIVO_CRIT_FACIL = "EO Sim Critica Facil"
MOTIVO_CRIT_MEDIO = "EO Sim Critica Medio"
MOTIVO_PROCEDIMENTO = "EO Sim Procedimento"

# Pesos esperados (impact_weight após 2026-08-01): 3.5 + 1.0 + 3.5 + 1.0 + 3.0 = 12.0
PROTOCOLO_DUP_559 = f"{SEED_PREFIX}55945472"
PROTOCOLO_DUP_152 = f"{SEED_PREFIX}15203"


def _aware(year: int, month: int, day: int, hour: int = 14) -> datetime:
    return timezone.make_aware(datetime(year, month, day, hour, 0))


def _brflow(data_analise: str, *, tipo_conclusao: str = "Automático") -> dict[str, str]:
    return {
        "data_analise": data_analise,
        "cliente": CLIENTE,
        "workflow": WORKFLOW,
        "tipo_conclusao": tipo_conclusao,
        "resultado_analise": "Reprovado",
    }


def _common(**extra: Any) -> dict[str, Any]:
    base = {
        "modulo": "G Auditoria",
        "cliente": CLIENTE,
        "auditor": AUDITOR,
        "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
        "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        "uf_documento": "SP",
        "tipo_documento": "RG",
        "nivel_dificuldade": "Médio",
        "qualidade_imagem": "Boa",
        "resultado_cliente": "Aprovado",
        "novo_resultado": "Reprovado",
        "descricao_irregularidades": "Irregularidade simulada para EO.",
        "etapa_falha": "Análise Documental",
    }
    base.update(extra)
    return base


# Sete candidatos elegíveis (tipo_registro=auditoria, origem=auditoria).
AUDITORIA_CANDIDATES: list[dict[str, Any]] = [
    {
        **_common(
            protocolo=f"{SEED_PREFIX}18629997",
            tipo_falha="Automático",
            usuario="",
            motivo_falha=MOTIVO_CRIT_FACIL,
            nivel_dificuldade="Fácil",
            analise_concluida_em=_aware(2026, 8, 6, 10),
            brflow_parsed=_brflow("30/07/2026"),
        ),
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "_expected_weight": 3.5,
    },
    {
        **_common(
            protocolo=f"{SEED_PREFIX}22583294",
            tipo_falha="Automático",
            usuario="",
            motivo_falha=MOTIVO_PROCEDIMENTO,
            analise_concluida_em=_aware(2026, 8, 6, 11),
            brflow_parsed=_brflow("30/07/2026"),
        ),
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "_expected_weight": 1.0,
    },
    {
        **_common(
            protocolo=PROTOCOLO_DUP_559,
            tipo_falha="Sem Falha",
            usuario=AGENTE_COM_MATRICULA,
            motivo_falha="",
            analise_concluida_em=_aware(2026, 8, 6, 12),
            brflow_parsed=_brflow("01/08/2026", tipo_conclusao="Manual"),
            novo_resultado="",
        ),
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "_expected_weight": None,
    },
    {
        **_common(
            protocolo=PROTOCOLO_DUP_559,
            tipo_falha="Automático",
            usuario="",
            motivo_falha=MOTIVO_CRIT_FACIL,
            nivel_dificuldade="Fácil",
            analise_concluida_em=_aware(2026, 8, 6, 13),
            brflow_parsed=_brflow("01/08/2026"),
        ),
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "_expected_weight": 3.5,
    },
    {
        **_common(
            protocolo=PROTOCOLO_DUP_152,
            tipo_falha="Sem Falha",
            usuario=AGENTE_COM_MATRICULA,
            motivo_falha="",
            analise_concluida_em=_aware(2026, 8, 7, 10),
            brflow_parsed=_brflow("05/08/2026", tipo_conclusao="Manual"),
            novo_resultado="",
        ),
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "_expected_weight": None,
    },
    {
        **_common(
            protocolo=PROTOCOLO_DUP_152,
            tipo_falha="Automático",
            usuario="",
            motivo_falha=MOTIVO_PROCEDIMENTO,
            analise_concluida_em=_aware(2026, 8, 7, 11),
            brflow_parsed=_brflow("01/08/2026"),
        ),
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "_expected_weight": 1.0,
    },
    {
        **_common(
            protocolo=f"{SEED_PREFIX}22658628",
            tipo_falha="Automático",
            usuario="",
            motivo_falha=MOTIVO_CRIT_MEDIO,
            analise_concluida_em=_aware(2026, 8, 7, 12),
            brflow_parsed=_brflow("05/08/2026"),
        ),
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "_expected_weight": 3.0,
    },
]

EXPECTED_AUDIT_DATES = {
    date(2026, 8, 6): {"auditados": 4, "falhas": 3},
    date(2026, 8, 7): {"auditados": 3, "falhas": 2},
}

EXPECTED_ANALISE_DATES = {
    date(2026, 7, 30): {"auditados": 2, "falhas": 2},
    date(2026, 8, 1): {"auditados": 3, "falhas": 2},
    date(2026, 8, 5): {"auditados": 2, "falhas": 1},
}

EXPECTED_IMPACTO_PONDERADO = 12.0
EXPECTED_EO_ETAPA = 28.6
EXPECTED_EO_PROTOCOLO = 0.0
EXPECTED_PROTOCOLOS_DISTINTOS = 5


def _filler_reinspecao(index: int) -> dict[str, Any]:
    day = 1 + (index % 28)
    return _common(
        protocolo=f"{SEED_PREFIX}R{index:04d}",
        tipo_falha="Colaborador",
        usuario=AGENTE_COM_MATRICULA if index % 3 == 0 else "",
        motivo_falha=MOTIVO_PROCEDIMENTO,
        analise_concluida_em=_aware(2026, 7, min(day, 28), 9 + (index % 8)),
        brflow_parsed=_brflow(f"{day:02d}/07/2026", tipo_conclusao="Manual"),
        origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
        tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
    )


def _filler_contestacao(index: int) -> dict[str, Any]:
    return _common(
        protocolo=f"{SEED_PREFIX}C{index:02d}",
        tipo_falha="Colaborador",
        usuario=AGENTE_COM_MATRICULA,
        motivo_falha=MOTIVO_PROCEDIMENTO,
        analise_concluida_em=_aware(2026, 8, 3 + index, 15),
        brflow_parsed=_brflow("03/08/2026", tipo_conclusao="Manual"),
        origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
        tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
    )


def build_realistic_rows() -> list[dict[str, Any]]:
    """110 registros determinísticos: 101 reinspeção, 2 contestação, 7 auditoria."""
    rows: list[dict[str, Any]] = []
    for i in range(1, REINSPECAO_COUNT + 1):
        rows.append(_filler_reinspecao(i))
    for i in range(1, CONTESTACAO_COUNT + 1):
        rows.append(_filler_contestacao(i))
    for candidate in AUDITORIA_CANDIDATES:
        row = {k: v for k, v in candidate.items() if not k.startswith("_")}
        rows.append(row)
    assert len(rows) == TOTAL_ROWS
    return rows


def motivo_catalog_rows() -> list[dict[str, str]]:
    return [
        {
            "motivo": MOTIVO_CRIT_FACIL,
            "criticidade": "Crítica",
            "segmentos": "Docs",
            "subsegmento": "Imagem",
        },
        {
            "motivo": MOTIVO_CRIT_MEDIO,
            "criticidade": "Crítica",
            "segmentos": "Docs",
            "subsegmento": "Imagem",
        },
        {
            "motivo": MOTIVO_PROCEDIMENTO,
            "criticidade": "Procedimento",
            "segmentos": "Docs",
            "subsegmento": "Imagem",
        },
    ]