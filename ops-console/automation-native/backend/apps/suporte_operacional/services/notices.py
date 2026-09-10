"""Consultas do mural de recados operacionais."""

from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone

from apps.suporte_operacional.models import OperationalSupportNotice

def mural_notices_queryset(*, include_all: bool = False) -> QuerySet[OperationalSupportNotice]:
    """Recados visíveis no mural.

    Por padrão: somente ativos.
    ``include_all=True``: gestão (Capacitação) — todos os recados.
    """
    qs = OperationalSupportNotice.objects.select_related("created_by")
    if include_all:
        return qs
    return qs.filter(active=True)


def set_notice_active(notice: OperationalSupportNotice, *, active: bool) -> None:
    notice.active = active
    if active:
        notice.inactive_at = None
    elif notice.inactive_at is None:
        notice.inactive_at = timezone.now()
    notice.save(update_fields=["active", "inactive_at", "updated_at"])
