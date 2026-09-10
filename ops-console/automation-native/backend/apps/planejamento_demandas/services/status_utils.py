"""Classificação de status Jira (aberta, concluída, cancelada)."""

from __future__ import annotations

from django.conf import settings

from apps.planejamento_demandas.models import JiraDemanda

OPEN = JiraDemanda.STATUS_KIND_OPEN
DONE = JiraDemanda.STATUS_KIND_DONE
CANCELLED = JiraDemanda.STATUS_KIND_CANCELLED


def _split_statuses(raw: str) -> frozenset[str]:
    return frozenset(s.strip().lower() for s in str(raw or "").split(",") if s.strip())


def done_statuses() -> frozenset[str]:
    return _split_statuses(getattr(settings, "JIRA_STATUS_CONCLUIDO", ""))


def cancelled_statuses() -> frozenset[str]:
    return _split_statuses(
        getattr(
            settings,
            "JIRA_DEMANDAS_STATUS_CANCELADO",
            "Cancelado,Cancelada,Cancelled,Canceled,Rejeitado,Rejeitada,Rejected,"
            "Descartado,Descartada,Arquivado,Arquivada",
        )
    )


def inactive_open_statuses() -> frozenset[str]:
    return _split_statuses(
        getattr(
            settings,
            "JIRA_DEMANDAS_STATUS_INATIVO",
            "Pausado,Paused,On Hold,Em pausa,Aguardando informações,Aguardando,"
            "Waiting,Waiting for customer,Waiting for support,Blocked,Bloqueado",
        )
    )


def exclude_inactive_open(qs):
    inactive = inactive_open_statuses()
    if not inactive:
        return qs
    from django.db.models.functions import Lower

    return qs.annotate(_status_lower=Lower("status_name")).exclude(_status_lower__in=inactive)


def apply_active_open(qs):
    return exclude_inactive_open(qs.filter(status_kind=OPEN))


def apply_inactive_open(qs):
    inactive = inactive_open_statuses()
    if not inactive:
        return qs.none()
    from django.db.models.functions import Lower

    return qs.filter(status_kind=OPEN).annotate(_status_lower=Lower("status_name")).filter(
        _status_lower__in=inactive
    )


def classify_status_name(status_name: str) -> str:
    name = (status_name or "").strip().lower()
    if not name:
        return OPEN
    if name in cancelled_statuses():
        return CANCELLED
    if name in done_statuses():
        return DONE
    return OPEN


def is_status_open(status_name: str) -> bool:
    return classify_status_name(status_name) == OPEN


def status_kind_label(kind: str) -> str:
    return dict(JiraDemanda.STATUS_KIND_CHOICES).get(kind, kind)


def jql_status_not_terminal() -> str:
    """Cláusula JQL para issues não terminais (abertas / em andamento).

    Usa ``statusCategory`` em vez de listar nomes de status — evita erro JQL quando
    labels como Cancelada/Concluido não existem no workflow do projeto.
    Override via ``JIRA_DEMANDAS_JQL_OPEN_CLAUSE`` (ex.: ``resolution is EMPTY``).
    """
    custom = str(getattr(settings, "JIRA_DEMANDAS_JQL_OPEN_CLAUSE", "") or "").strip()
    if custom:
        return custom
    return "statusCategory != Done"
