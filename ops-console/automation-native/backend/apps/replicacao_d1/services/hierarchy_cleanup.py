# -*- coding: utf-8 -*-
"""Remove resíduos das migrações 0023/0024 e restaura hierarquia Segmento → Categoria."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.db import transaction

from apps.replicacao_d1.config_models import (
    ReplicacaoD1Categoria,
    ReplicacaoD1Cliente,
    ReplicacaoD1Segmento,
)
from apps.replicacao_d1.normalization import normalize_key

CATEGORIA_TIER_NAMES = frozenset({"HIGH", "LOW", "MID"})


@dataclass
class HierarchyCleanupReport:
    segmentos_reactivated: int = 0
    categorias_consolidated: int = 0
    clientes_relinked: int = 0
    segmentos_deleted: int = 0
    categorias_deleted: int = 0
    errors: list[str] = field(default_factory=list)


def _is_tier_segmento(nome: str) -> bool:
    return (nome or "").strip().upper() in CATEGORIA_TIER_NAMES


def _segmento_by_chave() -> dict[str, ReplicacaoD1Segmento]:
    result: dict[str, ReplicacaoD1Segmento] = {}
    for seg in ReplicacaoD1Segmento.objects.order_by("-ativo", "id"):
        if _is_tier_segmento(seg.nome):
            continue
        chave = normalize_key(seg.nome)
        if chave and chave not in result:
            result[chave] = seg
    return result


def _pick_canonical_categoria(nome: str) -> ReplicacaoD1Categoria | None:
    tier = (nome or "").strip().upper()
    if tier not in CATEGORIA_TIER_NAMES:
        return None
    chave = normalize_key(tier)
    candidates = list(ReplicacaoD1Categoria.objects.filter(nome__iexact=tier).order_by("-ativo", "-id"))
    if not candidates:
        candidates = [
            c
            for c in ReplicacaoD1Categoria.objects.order_by("-ativo", "-id")
            if re.match(rf"^{re.escape(chave)}(-[0-9]+)?$", c.chave_normalizada or "")
        ]
    if not candidates:
        return None
    # Prefer record already referenced by clientes.
    best = candidates[0]
    best_refs = ReplicacaoD1Cliente.objects.filter(categoria_id=best.id).count()
    for cand in candidates[1:]:
        refs = ReplicacaoD1Cliente.objects.filter(categoria_id=cand.id).count()
        if refs > best_refs or (refs == best_refs and cand.ativo and not best.ativo):
            best = cand
            best_refs = refs
    return best


def cleanup_replicacao_d1_hierarchy_residue(*, dry_run: bool = True) -> HierarchyCleanupReport:
    report = HierarchyCleanupReport()

    seg_by_chave = _segmento_by_chave()
    canonical_categorias: dict[str, ReplicacaoD1Categoria] = {}

    for tier in sorted(CATEGORIA_TIER_NAMES):
        cat = _pick_canonical_categoria(tier)
        if cat is None:
            report.errors.append(f"Categoria canônica ausente: {tier}")
        else:
            canonical_categorias[normalize_key(tier)] = cat

    if report.errors:
        return report

    def _apply():
        nonlocal report

        # 1) Reativar segmentos de negócio.
        for seg in ReplicacaoD1Segmento.objects.all():
            if _is_tier_segmento(seg.nome):
                continue
            if not seg.ativo:
                seg.ativo = True
                seg.save(update_fields=["ativo", "updated_at"])
                report.segmentos_reactivated += 1

        seg_by_chave.clear()
        seg_by_chave.update(_segmento_by_chave())

        # 2) Consolidar categorias HIGH/LOW/MID.
        for tier in sorted(CATEGORIA_TIER_NAMES):
            chave = normalize_key(tier)
            canonical = canonical_categorias[chave]
            if canonical.nome.upper() != tier:
                canonical.nome = tier
            if canonical.chave_normalizada != chave:
                # Remove conflito de chave antes de renomear.
                ReplicacaoD1Categoria.objects.filter(chave_normalizada=chave).exclude(pk=canonical.pk).delete()
                canonical.chave_normalizada = chave
            canonical.segmento = None
            canonical.ativo = True
            canonical.save(update_fields=["nome", "chave_normalizada", "segmento", "ativo", "updated_at"])

            dupes = ReplicacaoD1Categoria.objects.filter(nome__iexact=tier).exclude(pk=canonical.pk)
            for dupe in dupes:
                ReplicacaoD1Cliente.objects.filter(categoria_id=dupe.pk).update(
                    categoria_id=canonical.pk,
                    categoria_nome=canonical.nome,
                )
                dupe.delete()
                report.categorias_consolidated += 1

        cat_by_chave = {normalize_key(c.nome): c for c in ReplicacaoD1Categoria.objects.filter(ativo=True)}

        # 3) Relink clientes a partir dos nomes desnormalizados (fonte confiável).
        for cliente in ReplicacaoD1Cliente.objects.all().iterator():
            updates: dict = {}
            seg_nome = (cliente.segmento_nome or "").strip()
            cat_nome = (cliente.categoria_nome or "").strip()

            if seg_nome and not _is_tier_segmento(seg_nome):
                seg = seg_by_chave.get(normalize_key(seg_nome))
                if seg and cliente.segmento_id != seg.pk:
                    updates["segmento_id"] = seg.pk

            if cat_nome:
                cat = cat_by_chave.get(normalize_key(cat_nome))
                if cat and cliente.categoria_id != cat.pk:
                    updates["categoria_id"] = cat.pk

            if updates:
                ReplicacaoD1Cliente.objects.filter(pk=cliente.pk).update(**updates)
                report.clientes_relinked += 1

        # 4) Remover segmentos resíduo (tiers ou inativos sem referência).
        for seg in ReplicacaoD1Segmento.objects.all():
            refs = ReplicacaoD1Cliente.objects.filter(segmento_id=seg.pk).count()
            if refs:
                continue
            if _is_tier_segmento(seg.nome) or not seg.ativo:
                seg.delete()
                report.segmentos_deleted += 1

        # 5) Remover categorias inativas/duplicadas sem referência.
        for cat in ReplicacaoD1Categoria.objects.all():
            refs = ReplicacaoD1Cliente.objects.filter(categoria_id=cat.pk).count()
            if refs:
                continue
            chave = normalize_key(cat.nome)
            if chave in canonical_categorias and cat.pk != canonical_categorias[chave].pk:
                cat.delete()
                report.categorias_deleted += 1
            elif not cat.ativo:
                cat.delete()
                report.categorias_deleted += 1

    if dry_run:
        # Simulação: contagem sem gravar.
        inactive_segs = ReplicacaoD1Segmento.objects.filter(ativo=False).exclude(
            nome__iregex=r"^(HIGH|LOW|MID)$"
        ).count()
        report.segmentos_reactivated = inactive_segs
        report.categorias_consolidated = (
            ReplicacaoD1Categoria.objects.filter(nome__iregex=r"^(HIGH|LOW|MID)$").count() - 3
        )
        report.clientes_relinked = ReplicacaoD1Cliente.objects.count()
        report.segmentos_deleted = ReplicacaoD1Segmento.objects.filter(
            nome__iregex=r"^(HIGH|LOW|MID)$"
        ).count()
        report.categorias_deleted = ReplicacaoD1Categoria.objects.filter(ativo=False).count()
        return report

    with transaction.atomic():
        _apply()

    return report
