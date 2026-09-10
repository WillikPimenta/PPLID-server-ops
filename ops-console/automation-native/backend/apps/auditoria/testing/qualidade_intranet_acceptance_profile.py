# -*- coding: utf-8 -*-
"""Perfil sintético de quatro fluxos, sem PII, pelo resultado canônico."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django.utils import timezone

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.qualidade_operacional.services.intranet_metric_rules import classify_intranet_source

SEED_PREFIX = "EO-ACC-"
TOTAL_ROWS = 355
DISTINCT_PROTOCOLOS = 298
AUDIT_DATE = date(2026, 8, 10)

CLIENTE = "Cliente EO Acc"
WORKFLOW = "WF EO Acc"
MOTIVO_DEFAULT = "EO Acc Motivo"
MOTIVO_CRIT_MEDIO = "EO Acc Critica Medio"

EXPECTED_ETAPA = {"auditados": 355, "falhas": 18, "eo_pct": 94.9}
EXPECTED_PROTOCOLO = {"auditados": 298, "falhas": 18, "eo_pct": 94.0}
EXPECTED_IMPACTO_ETAPA = 18.0
EXPECTED_IMPACTO_PROTOCOLO = 18.0
EXPECTED_EO_PONDERADO_ETAPA = 94.9
EXPECTED_EO_PONDERADO_PROTOCOLO = 94.0


def _dt(day: date, hour: int = 12) -> datetime:
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, 0))


def motivo_catalog_rows() -> list[dict[str, str]]:
    return [
        {
            "motivo": MOTIVO_DEFAULT,
            "criticidade": "Procedimento",
            "segmentos": "Segmento",
            "subsegmento": "Sub",
        },
        {
            "motivo": MOTIVO_CRIT_MEDIO,
            "criticidade": "Crítica",
            "segmentos": "Segmento",
            "subsegmento": "Sub",
        },
    ]


def _row(
    idx: int,
    *,
    protocolo: str,
    tipo_registro: str,
    origem: str,
    tipo_falha: str,
    procedencia: str = "",
    motivo_falha: str = MOTIVO_DEFAULT,
    data_analise: str | None = None,
) -> dict[str, Any]:
    brflow: dict[str, str] = {"cliente": CLIENTE, "workflow": WORKFLOW}
    is_auditoria_compliance = (
        origem == "auditoria"
        and tipo_registro == "reinspecao"
        and tipo_falha == "auditoria"
    )
    if is_auditoria_compliance:
        brflow["fila_contexto"] = "auditoria_compliance"
    if data_analise:
        brflow["data_analise"] = data_analise
    row = {
        "protocolo": protocolo,
        "modulo": "G Auditoria",
        "origem": origem,
        "tipo_registro": tipo_registro,
        "tipo_falha": tipo_falha,
        "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
        "status": procedencia,
        "usuario": "",
        "motivo_falha": motivo_falha if tipo_falha not in ("Sem Falha",) else "",
        "etapa_falha": "Análise Documental",
        "nivel_dificuldade": "Médio",
        "cliente": CLIENTE,
        "analise_concluida_em": _dt(AUDIT_DATE, 8 + (idx % 10)),
        "brflow_parsed": brflow,
    }
    if origem in ("contestacao", "reinspecao"):
        row["data_contestacao"] = _dt(AUDIT_DATE, 8 + (idx % 10))
    elif is_auditoria_compliance:
        row["data_analise"] = _dt(AUDIT_DATE, 8 + (idx % 10))
    row["resultado_qualidade"] = AuditoriaFalhaCadastro.inferir_resultado_qualidade(
        status=procedencia,
        tipo_falha=tipo_falha,
        origem=origem,
        tipo_registro=tipo_registro,
        status_falha=row["status_falha"],
        brflow_parsed=brflow,
    )
    return row


def build_acceptance_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    idx = 0

    def add(count: int, **kwargs) -> None:
        nonlocal idx
        for _ in range(count):
            prot = f"{SEED_PREFIX}P{(idx % DISTINCT_PROTOCOLOS) + 1:04d}"
            rows.append(_row(idx, protocolo=prot, **kwargs))
            idx += 1

    segments = [
        (158, dict(tipo_registro="reinspecao", origem="reinspecao", tipo_falha="reinspecao", procedencia="Procedente")),
        (9, dict(tipo_registro="reinspecao", origem="reinspecao", tipo_falha="reinspecao", procedencia="Improcedente")),
        (159, dict(tipo_registro="reinspecao", origem="auditoria", tipo_falha="auditoria", procedencia="Improcedente")),
        (1, dict(tipo_registro="reinspecao", origem="auditoria", tipo_falha="auditoria", procedencia="Procedente")),
        (16, dict(tipo_registro="contestacao", origem="contestacao", tipo_falha="Automático", procedencia="Improcedente")),
        (2, dict(tipo_registro="contestacao", origem="contestacao", tipo_falha="Colaborador", procedencia="Improcedente")),
        (8, dict(tipo_registro="auditoria", origem="auditoria", tipo_falha="Colaborador", procedencia="")),
        (2, dict(tipo_registro="auditoria", origem="auditoria", tipo_falha="Sem Falha", procedencia="")),
    ]
    for count, kwargs in segments:
        add(count, **kwargs)

    # Reinspeção é sempre Procedimento; as 18 falhas mantêm peso 1.0.
    rows[158]["motivo_falha"] = MOTIVO_CRIT_MEDIO

    # Dez linhas com data_analise: 8 falhas + 2 Sem Falha (sem falha no numerador).
    sem_falha_idxs = [len(rows) - 2, len(rows) - 1]
    falha_analise_idxs = list(range(158, 166))
    for j, idx in enumerate(falha_analise_idxs + sem_falha_idxs):
        rows[idx]["brflow_parsed"] = {
            **rows[idx]["brflow_parsed"],
            "data_analise": f"{4 + (j % 4):02d}/08/2026",
        }

    # 298 protocolos distintos; 18 com falha e 280 somente sem falha.
    failure_rows: list[dict[str, Any]] = []
    conforme_rows: list[dict[str, Any]] = []
    for row in rows:
        cls = classify_intranet_source(
            resultado_qualidade=row["resultado_qualidade"],
            status_falha=row["status_falha"],
            tipo_falha=row["tipo_falha"],
            procedencia_raw=row["status"],
            tipo_registro=row["tipo_registro"],
            origem=row["origem"],
        )
        (failure_rows if cls.is_falha else conforme_rows).append(row)

    for i, row in enumerate(failure_rows):
        row["protocolo"] = f"{SEED_PREFIX}P{i + 1:04d}"
    for i, row in enumerate(conforme_rows):
        row["protocolo"] = f"{SEED_PREFIX}P{len(failure_rows) + (i % 280) + 1:04d}"

    return rows
