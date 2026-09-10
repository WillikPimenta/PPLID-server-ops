# -*- coding: utf-8 -*-
"""Aliases explícitos e auditáveis de cliente/workflow para projeção EO."""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.qualidade_operacional.models import QualidadeDimAlias
from apps.qualidade_operacional.services.normalize import clean_text, fold_ascii_upper


def _alias_key(nome: str) -> str:
    return " ".join(fold_ascii_upper(clean_text(nome)).split())

# Evidência documentada — não inclui SICREDI (decisão de negócio pendente).
DEFAULT_DIM_ALIASES: tuple[dict[str, str | int], ...] = (
    {"kind": QualidadeDimAlias.KIND_CLIENTE, "alias_key": "PICPAY", "target_id": 10, "evidence": "56.573 ocorrências coocorrentes workflow Picpay"},
    {"kind": QualidadeDimAlias.KIND_CLIENTE, "alias_key": "BRADESCO", "target_id": 6, "evidence": "79.246 ocorrências Segurança Corporativa"},
    {"kind": QualidadeDimAlias.KIND_CLIENTE, "alias_key": "BMG", "target_id": 7, "evidence": "33.926 ocorrências Banco BMG"},
    {"kind": QualidadeDimAlias.KIND_CLIENTE, "alias_key": "MERCANTIL", "target_id": 34, "evidence": "Dois workflows históricos consistentes"},
    {"kind": QualidadeDimAlias.KIND_CLIENTE, "alias_key": "BRB", "target_id": 35, "evidence": "Dois workflows históricos consistentes"},
    {"kind": QualidadeDimAlias.KIND_CLIENTE, "alias_key": "VIVO", "target_id": 4, "evidence": "434.280 ocorrências Telefônica Brasil / Vivo"},
    {"kind": QualidadeDimAlias.KIND_CLIENTE, "alias_key": "SERASA PME", "target_id": 17, "evidence": "Provável Serasa Experian — validar com negócio"},
    {
        "kind": QualidadeDimAlias.KIND_CLIENTE,
        "alias_key": "SICREDI",
        "target_id": 21,
        "evidence": "Nome genérico + WF746 SICREDI 15; Confederação (id 21) — validar com negócio",
    },
    {
        "kind": QualidadeDimAlias.KIND_WORKFLOW,
        "alias_key": "TIM BRASIL - DOCUMENTOSCOPIA ESPECIALIZADA",
        "target_id": 623,
        "evidence": "ID canônico; 836 permanece duplicata inativa no catálogo",
    },
)


@dataclass
class DimAliasIndex:
    clientes: dict[str, int] = field(default_factory=dict)
    workflows: dict[str, int] = field(default_factory=dict)


def load_dim_alias_index() -> DimAliasIndex:
    index = DimAliasIndex()
    for row in QualidadeDimAlias.objects.filter(active=True).only(
        "kind", "alias_key", "target_id"
    ):
        key = _alias_key(row.alias_key)
        if not key:
            continue
        if row.kind == QualidadeDimAlias.KIND_CLIENTE:
            index.clientes[key] = int(row.target_id)
        elif row.kind == QualidadeDimAlias.KIND_WORKFLOW:
            index.workflows[key] = int(row.target_id)
    return index


def seed_default_dim_aliases(*, force: bool = False) -> int:
    created = 0
    for spec in DEFAULT_DIM_ALIASES:
        key = _alias_key(str(spec["alias_key"]))
        defaults = {
            "target_id": int(spec["target_id"]),
            "evidence": str(spec.get("evidence") or ""),
            "active": True,
        }
        obj, was_created = QualidadeDimAlias.objects.get_or_create(
            kind=str(spec["kind"]),
            alias_key=key,
            defaults=defaults,
        )
        if was_created:
            created += 1
        elif force:
            QualidadeDimAlias.objects.filter(pk=obj.pk).update(**defaults)
    return created
