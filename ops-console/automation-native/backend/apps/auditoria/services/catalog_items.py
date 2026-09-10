from __future__ import annotations

import json
from pathlib import Path

from apps.auditoria.constants import (
    CRUZAMENTO_BASES,
    DUVIDAS_SUPORTE_OPERACIONAL,
    ETAPAS_FALHA,
    ETAPA_FALHA_AUTOMATICO,
    MODULOS,
    MOTIVOS_BASE_NEGATIVA,
    NIVEIS_DIFICULDADE,
    NOVOS_RESULTADOS,
    QUALIDADE_IMAGEM,
    SINALIZACAO,
    TIPOS_ACAO_CONTROLE,
    TIPOS_DOCUMENTO,
    TIPOS_FALHA,
    UFS_DOCUMENTO,
)
from apps.auditoria.models import AuditoriaCatalogItem
from apps.auditoria.services.text_format import format_auditoria_label

CATALOG_DEFAULTS: dict[str, list[str]] = {
    AuditoriaCatalogItem.CATALOG_MODULO: MODULOS,
    AuditoriaCatalogItem.CATALOG_TIPO_FALHA: TIPOS_FALHA,
    AuditoriaCatalogItem.CATALOG_NOVO_RESULTADO: NOVOS_RESULTADOS,
    AuditoriaCatalogItem.CATALOG_SINALIZACAO: SINALIZACAO,
    AuditoriaCatalogItem.CATALOG_ETAPA_FALHA: ETAPAS_FALHA,
    AuditoriaCatalogItem.CATALOG_NIVEL_DIFICULDADE: NIVEIS_DIFICULDADE,
    AuditoriaCatalogItem.CATALOG_TIPO_DOCUMENTO: TIPOS_DOCUMENTO,
    AuditoriaCatalogItem.CATALOG_UF_DOCUMENTO: UFS_DOCUMENTO,
    AuditoriaCatalogItem.CATALOG_CRUZAMENTO_BASES: CRUZAMENTO_BASES,
    AuditoriaCatalogItem.CATALOG_QUALIDADE_IMAGEM: QUALIDADE_IMAGEM,
    AuditoriaCatalogItem.CATALOG_TIPO_ACAO_CONTROLE: TIPOS_ACAO_CONTROLE,
    AuditoriaCatalogItem.CATALOG_MOTIVO_BASE_NEGATIVA: MOTIVOS_BASE_NEGATIVA,
    # Seed completo via JSON (seed_irregularidades_confer_defaults).
    AuditoriaCatalogItem.CATALOG_IRREGULARIDADES_CONFER: [],
    AuditoriaCatalogItem.CATALOG_DUVIDA_SUPORTE_OPERACIONAL: DUVIDAS_SUPORTE_OPERACIONAL,
}

VALID_CATALOGS = set(CATALOG_DEFAULTS.keys())

PRESERVE_CASE_CATALOGS = {
    AuditoriaCatalogItem.CATALOG_TIPO_ACAO_CONTROLE,
    AuditoriaCatalogItem.CATALOG_MOTIVO_BASE_NEGATIVA,
    AuditoriaCatalogItem.CATALOG_IRREGULARIDADES_CONFER,
    AuditoriaCatalogItem.CATALOG_DUVIDA_SUPORTE_OPERACIONAL,
}

IRREGULARIDADES_CONFER_SEED_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "irregularidades_confer_seed.json"
)


def normalize_catalog_value(catalog: str, value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if catalog in PRESERVE_CASE_CATALOGS:
        return text
    return format_auditoria_label(text)


def _format_values(catalog: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    formatted: list[str] = []
    for value in values:
        label = normalize_catalog_value(catalog, value)
        if not label or label in seen:
            continue
        seen.add(label)
        formatted.append(label)
    return formatted


def seed_catalog_defaults() -> int:
    created = 0
    for catalog, values in CATALOG_DEFAULTS.items():
        if catalog == AuditoriaCatalogItem.CATALOG_IRREGULARIDADES_CONFER:
            continue
        if AuditoriaCatalogItem.objects.filter(catalog=catalog).exists():
            continue
        items = [
            AuditoriaCatalogItem(
                catalog=catalog,
                value=normalize_catalog_value(catalog, value),
                label=normalize_catalog_value(catalog, value),
                sort_order=index,
                active=True,
            )
            for index, value in enumerate(values)
        ]
        AuditoriaCatalogItem.objects.bulk_create(items)
        created += len(items)
    created += seed_irregularidades_confer_defaults()
    return created


def seed_irregularidades_confer_defaults() -> int:
    """Carrega irregularidades Confer do JSON (cria apenas se o catálogo estiver vazio)."""
    catalog = AuditoriaCatalogItem.CATALOG_IRREGULARIDADES_CONFER
    if AuditoriaCatalogItem.objects.filter(catalog=catalog).exists():
        return 0
    if not IRREGULARIDADES_CONFER_SEED_FILE.is_file():
        return 0
    rows = json.loads(IRREGULARIDADES_CONFER_SEED_FILE.read_text(encoding="utf-8"))
    items: list[AuditoriaCatalogItem] = []
    seen: set[str] = set()
    for row in rows:
        value = normalize_catalog_value(catalog, str(row.get("value") or ""))
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        sort_order = int(row.get("sort_order") or len(items))
        active = bool(row.get("active", True))
        items.append(
            AuditoriaCatalogItem(
                catalog=catalog,
                value=value,
                label=value,
                sort_order=sort_order,
                active=active,
            )
        )
    if not items:
        return 0
    AuditoriaCatalogItem.objects.bulk_create(items, batch_size=500)
    return len(items)


def get_catalog_values(catalog: str, *, active_only: bool = True) -> list[str]:
    if catalog not in VALID_CATALOGS:
        return []
    qs = AuditoriaCatalogItem.objects.filter(catalog=catalog)
    if active_only:
        qs = qs.filter(active=True)
    values = list(qs.order_by("sort_order", "value").values_list("value", flat=True))
    if values:
        return _format_values(catalog, values)
    return _format_values(catalog, list(CATALOG_DEFAULTS.get(catalog, [])))


def get_valid_tipo_falha_values() -> set[str]:
    return set(get_catalog_values(AuditoriaCatalogItem.CATALOG_TIPO_FALHA))


def get_etapa_falha_automatico() -> str:
    return format_auditoria_label(ETAPA_FALHA_AUTOMATICO)
