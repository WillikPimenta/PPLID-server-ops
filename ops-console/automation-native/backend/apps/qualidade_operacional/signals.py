# -*- coding: utf-8 -*-
"""Signals: invalidação de cache EO + projeção Intranet → fatos."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeFiltroOpcao,
)
from apps.qualidade_operacional.services.performance_cache import (
    bump_quality_cache_version,
)
from apps.workforce.models import AgentHistory

logger = logging.getLogger(__name__)

# Evita recursão quando a própria sync grava QualidadeAuditado/Falha.
_SYNCING = False
_DEFERRED_SOURCE_IDS: ContextVar[list[int] | None] = ContextVar(
    "quality_deferred_source_ids", default=None
)


@contextmanager
def quality_projection_sync_guard():
    """Evita sinais e invalidações duplicados durante a projeção."""
    global _SYNCING
    previous = _SYNCING
    _SYNCING = True
    try:
        yield
    finally:
        _SYNCING = previous


@contextmanager
def defer_quality_projection_syncs():
    """Agrupa projeções criadas na mesma operação em jobs duráveis."""
    source_ids: list[int] = []
    token = _DEFERRED_SOURCE_IDS.set(source_ids)
    try:
        yield
    finally:
        _DEFERRED_SOURCE_IDS.reset(token)
        if source_ids:
            _schedule_intranet_sync_ids(source_ids)


def _upsert_option(dimensao: str, valor: str | None) -> None:
    clean = str(valor or "").strip()
    if clean:
        QualidadeFiltroOpcao.objects.get_or_create(dimensao=dimensao, valor=clean)


@receiver(post_save, sender=QualidadeAuditado)
def auditado_saved(sender, instance: QualidadeAuditado, **kwargs) -> None:
    del sender, kwargs
    if _SYNCING:
        return
    _upsert_option("tipo_analise", instance.tipo_analise)
    _upsert_option("etapa", instance.etapa)
    _upsert_option("localidade", instance.localidade_documento)
    bump_quality_cache_version()


@receiver(post_save, sender=QualidadeFalha)
def falha_saved(sender, instance: QualidadeFalha, **kwargs) -> None:
    del sender, kwargs
    if _SYNCING:
        return
    _upsert_option("tipo_falha", instance.tipo_falha)
    _upsert_option("localidade", instance.localidade_documento)
    _upsert_option("etapa", instance.etapa)
    bump_quality_cache_version()


@receiver(post_save, sender=AgentHistory)
@receiver(post_delete, sender=AgentHistory)
def quality_scope_changed(**kwargs) -> None:
    del kwargs
    bump_quality_cache_version()


def _schedule_intranet_sync(source_id: int) -> None:
    _schedule_intranet_sync_ids([source_id])


def _schedule_intranet_sync_ids(source_ids: list[int]) -> None:
    unique_ids = list(dict.fromkeys(int(source_id) for source_id in source_ids))
    from django.conf import settings
    from apps.qualidade_operacional.services.source_config import effective_source_active

    if not effective_source_active():
        return

    if getattr(settings, "QUALIDADE_PROJECTION_ASYNC_ENABLED", True):
        from apps.common.bot_db_sync_lanes import LANE_MID
        from apps.common.bot_db_sync_queue import (
            enqueue_bot_db_sync,
            spawn_drain_worker,
        )
        from apps.common.models import BotDbSyncJob

        for offset in range(0, len(unique_ids), 75):
            enqueue_bot_db_sync(
                domain=BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION,
                source_path=",".join(
                    str(value) for value in unique_ids[offset : offset + 75]
                ),
                force=True,
                spawn=False,
            )
        transaction.on_commit(lambda: spawn_drain_worker(lane=LANE_MID))
        return

    def _run() -> None:
        try:
            from apps.auditoria.models import AuditoriaFalhaCadastro
            from apps.qualidade_operacional.services.intranet_source import sync_one

            sources = list(
                AuditoriaFalhaCadastro.objects.select_related(
                    "atividade", "auditor_ref", "auditor_responsavel", "analise_origem"
                )
                .filter(pk__in=unique_ids)
                .order_by("pk")
            )
            if not sources:
                return
            with quality_projection_sync_guard():
                for source in sources:
                    sync_one(source, bump_cache=False)
            bump_quality_cache_version()
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao sincronizar EO a partir da Intranet ids=%s", unique_ids)

    transaction.on_commit(_run)


def _schedule_intranet_delete(source_id: int) -> None:
    def _run() -> None:
        global _SYNCING
        from apps.qualidade_operacional.services.intranet_source import (
            remove_projection_for_source_id,
        )

        try:
            _SYNCING = True
            try:
                remove_projection_for_source_id(source_id, bump_cache=True)
            finally:
                _SYNCING = False
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao remover projeção EO da Intranet id=%s", source_id)

    transaction.on_commit(_run)


def connect_auditoria_signals() -> None:
    from apps.auditoria.models import AuditoriaFalhaCadastro
    from apps.qualidade_operacional.models import QualidadeIntranetProjection
    from apps.qualidade_operacional.services.intranet_source import _delete_projection_facts

    @receiver(
        post_save,
        sender=AuditoriaFalhaCadastro,
        dispatch_uid="qo_intranet_post_save",
        weak=False,
    )
    def auditoria_falha_saved(sender, instance, **kwargs) -> None:
        del sender, kwargs
        deferred = _DEFERRED_SOURCE_IDS.get()
        if deferred is not None:
            deferred.append(instance.pk)
            return
        _schedule_intranet_sync(instance.pk)

    @receiver(
        pre_delete,
        sender=AuditoriaFalhaCadastro,
        dispatch_uid="qo_intranet_pre_delete",
        weak=False,
    )
    def auditoria_falha_deleting(sender, instance, **kwargs) -> None:
        del sender, kwargs
        global _SYNCING
        projection = QualidadeIntranetProjection.objects.filter(source_id=instance.pk).first()
        if projection is None:
            return
        _SYNCING = True
        try:
            _delete_projection_facts(projection)
            # A ponte é removida por CASCADE com a origem; fatos já foram apagados.
            QualidadeIntranetProjection.objects.filter(pk=projection.pk).delete()
        finally:
            _SYNCING = False

        transaction.on_commit(bump_quality_cache_version)
