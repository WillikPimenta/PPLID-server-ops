from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    PortalNotification,
    PortalNotificationEventLog,
    PortalNotificationRule,
    PortalNotificationRuleVersion,
)


DEFAULT_RULES_PATH = Path(__file__).with_name("default_rules.json")

INTEGRATED_RULE_DEFAULTS = {
    "matrix.n026": {"recipient_types": ["participants"], "target_url": "/operacao/trocas"},
    "matrix.n027": {"recipient_types": ["participants"], "target_url": "/operacao/trocas"},
    "matrix.n028": {"recipient_types": ["participants"], "target_url": "/operacao/trocas"},
    "matrix.n029": {"recipient_types": ["participants"], "target_url": "/operacao/trocas"},
    "matrix.n030": {"recipient_types": ["participants"], "target_url": "/operacao/trocas"},
    "matrix.n035": {"recipient_types": ["participants"], "target_url": "/operacao/suporte-operacional"},
    "matrix.n036": {"recipient_types": ["participants"], "target_url": "/operacao/suporte-operacional"},
    "matrix.n038": {"recipient_types": ["participants"], "target_url": "/operacao/suporte-operacional"},
}


def active_notifications_for(user, *, now=None):
    now = now or timezone.now()
    return PortalNotification.objects.filter(recipient=user, expires_at__gt=now)


def purge_expired_notifications(*, now=None) -> int:
    now = now or timezone.now()
    deleted, _ = PortalNotification.objects.filter(expires_at__lte=now).delete()
    return deleted


def _safe_target_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("A rota da notificação deve ser interna ao portal.")
    return value[:500]


def create_notification(
    *,
    recipient,
    title: str,
    message: str = "",
    kind: str = PortalNotification.Kind.INFO,
    target_url: str = "",
    source_type: str = "",
    source_id: str = "",
    dedupe_key: str = "",
) -> PortalNotification:
    title = (title or "").strip()
    if not title:
        raise ValueError("O título da notificação é obrigatório.")
    if kind not in PortalNotification.Kind.values:
        raise ValueError("Tipo de notificação inválido.")

    normalized_dedupe = (dedupe_key or "").strip()[:220] or None
    values = {
        "title": title[:160],
        "message": (message or "").strip(),
        "kind": kind,
        "target_url": _safe_target_url(target_url),
        "source_type": (source_type or "").strip()[:80],
        "source_id": str(source_id or "").strip()[:100],
    }
    if normalized_dedupe:
        try:
            notification, _ = PortalNotification.objects.get_or_create(
                recipient=recipient,
                dedupe_key=normalized_dedupe,
                defaults=values,
            )
            return notification
        except IntegrityError:
            return PortalNotification.objects.get(
                recipient=recipient,
                dedupe_key=normalized_dedupe,
            )
    return PortalNotification.objects.create(recipient=recipient, **values)


def notify_users(*, recipients: Iterable, exclude_user=None, **notification_data) -> list[PortalNotification]:
    created: list[PortalNotification] = []
    seen_ids = set()
    for recipient in recipients:
        if not recipient or not getattr(recipient, "is_active", False):
            continue
        recipient_id = getattr(recipient, "pk", None)
        if not recipient_id or recipient_id in seen_ids:
            continue
        if exclude_user is not None and recipient_id == getattr(exclude_user, "pk", None):
            continue
        seen_ids.add(recipient_id)
        created.append(create_notification(recipient=recipient, **notification_data))
    return created


def users_for_lan_ids(lan_ids: Iterable[str]):
    normalized = sorted({str(value or "").strip().lower() for value in lan_ids if str(value or "").strip()})
    if not normalized:
        return []
    User = get_user_model()
    username_filter = Q()
    for lan_id in normalized:
        username_filter |= Q(username__iexact=lan_id)
    return list(User.objects.filter(username_filter, is_active=True))


RULE_SNAPSHOT_FIELDS = (
    "event_key", "recipient_types", "escalation_recipient_types", "channels",
    "priority", "frequency", "frequency_window_minutes", "notification_title",
    "notification_message", "target_url", "action_description", "notes",
)


def rule_snapshot(rule: PortalNotificationRule) -> dict:
    return {field: getattr(rule, field) for field in RULE_SNAPSHOT_FIELDS}


def _resolved_recipients(rule, recipients_by_type: dict[str, Iterable]) -> list:
    selected = []
    seen = set()
    for recipient_type in [*rule.recipient_types, *rule.escalation_recipient_types]:
        for user in recipients_by_type.get(recipient_type, []):
            user_id = getattr(user, "pk", None)
            if user_id and user_id not in seen and getattr(user, "is_active", False):
                seen.add(user_id)
                selected.append(user)
    return selected


def simulate_notification_rule(rule, *, recipients_by_type=None) -> dict:
    recipients = _resolved_recipients(rule, recipients_by_type or {})
    return {
        "recipient_count": len(recipients),
        "recipients": [
            {
                "id": str(user.pk),
                "name": user.get_full_name() or user.get_username(),
                "username": user.get_username(),
            }
            for user in recipients
        ],
        "channels": rule.channels,
        "warnings": [
            message
            for condition, message in (
                (not rule.recipient_types, "Nenhum destinatário principal configurado."),
                (not recipients, "O contexto informado não resolveu destinatários."),
                (not rule.channels, "Nenhum canal configurado."),
                (any(channel != "portal" for channel in rule.channels), "Há canais ainda não suportados."),
                (rule.integration_status != PortalNotificationRule.IntegrationStatus.INTEGRATED, "O evento ainda não está marcado como integrado."),
            )
            if condition
        ],
    }


@transaction.atomic
def publish_notification_rule(rule, *, user) -> PortalNotificationRule:
    rule.version += 1
    rule.lifecycle = PortalNotificationRule.Lifecycle.PUBLISHED
    rule.enabled = True
    rule.decision = PortalNotificationRule.Decision.APPROVED
    rule.published_at = timezone.now()
    rule.published_by = user
    rule.updated_by = user
    rule.save()
    PortalNotificationRuleVersion.objects.create(
        rule=rule,
        version=rule.version,
        snapshot=rule_snapshot(rule),
        published_by=user,
    )
    return rule


class _SafeTemplateContext(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def emit_notification_event(
    *, event_key: str, recipients_by_type: dict[str, Iterable], context: dict | None = None,
    exclude_user=None, fallback_recipients=(), fallback_title: str = "",
    fallback_message: str = "", fallback_kind: str = PortalNotification.Kind.INFO,
    fallback_target_url: str = "", source_type: str = "", source_id: str = "",
    dedupe_key: str = "",
) -> list[PortalNotification]:
    rule = PortalNotificationRule.objects.filter(
        event_key=event_key,
        enabled=True,
        version__gt=0,
    ).exclude(
        lifecycle__in=[PortalNotificationRule.Lifecycle.SUSPENDED, PortalNotificationRule.Lifecycle.DISCARDED]
    ).first()
    if not rule:
        created = notify_users(
            recipients=fallback_recipients,
            exclude_user=exclude_user,
            title=fallback_title,
            message=fallback_message,
            kind=fallback_kind,
            target_url=fallback_target_url,
            source_type=source_type,
            source_id=source_id,
            dedupe_key=dedupe_key,
        )
        PortalNotificationEventLog.objects.create(
            event_key=event_key, source_type=source_type, source_id=source_id,
            outcome="fallback", recipient_count=len(created),
            detail="Regra publicada não encontrada; comportamento anterior preservado.",
        )
        return created

    published_version = rule.versions.first()
    effective_rule = SimpleNamespace(**published_version.snapshot) if published_version else rule
    if "portal" not in effective_rule.channels:
        PortalNotificationEventLog.objects.create(
            event_key=event_key, rule=rule, source_type=source_type, source_id=source_id,
            outcome="skipped", detail="Canal Portal não configurado.",
        )
        return []
    recipients = _resolved_recipients(effective_rule, recipients_by_type)
    if effective_rule.frequency == PortalNotificationRule.Frequency.RATE_LIMITED:
        since = timezone.now() - timedelta(minutes=effective_rule.frequency_window_minutes or 1)
        if PortalNotificationEventLog.objects.filter(
            rule=rule, source_type=source_type, source_id=source_id,
            outcome="sent", created_at__gte=since,
        ).exists():
            PortalNotificationEventLog.objects.create(
                event_key=event_key, rule=rule, source_type=source_type, source_id=source_id,
                outcome="rate_limited", detail="Envio bloqueado pela janela configurada.",
            )
            return []
    effective_dedupe_key = dedupe_key
    if effective_rule.frequency == PortalNotificationRule.Frequency.DAILY_DIGEST:
        effective_dedupe_key = f"{event_key}:daily:{timezone.localdate().isoformat()}"
    elif effective_rule.frequency == PortalNotificationRule.Frequency.WEEKLY_DIGEST:
        year, week, _ = timezone.localdate().isocalendar()
        effective_dedupe_key = f"{event_key}:weekly:{year}-{week}"
    template_context = _SafeTemplateContext(context or {})
    kind = {
        PortalNotificationRule.Priority.WARNING: PortalNotification.Kind.WARNING,
        PortalNotificationRule.Priority.CRITICAL: PortalNotification.Kind.ERROR,
    }.get(effective_rule.priority, PortalNotification.Kind.INFO)
    created = notify_users(
        recipients=recipients, exclude_user=exclude_user,
        title=(effective_rule.notification_title or fallback_title).format_map(template_context),
        message=(effective_rule.notification_message or fallback_message).format_map(template_context),
        kind=kind, target_url=effective_rule.target_url or fallback_target_url,
        source_type=source_type, source_id=source_id, dedupe_key=effective_dedupe_key,
    )
    PortalNotificationEventLog.objects.create(
        event_key=event_key, rule=rule, source_type=source_type, source_id=source_id,
        outcome="sent", recipient_count=len(created),
    )
    return created


def load_default_rule_catalog() -> list[dict]:
    payload = json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    rows = payload.get("events") or []
    keys = [str(row.get("event_key") or "").strip() for row in rows]
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError("O catálogo de notificações possui chaves vazias ou duplicadas.")
    return rows


def sync_notification_rule_catalog() -> int:
    """Insere eventos novos sem sobrescrever ajustes realizados na configuração."""
    created = 0
    for row in load_default_rule_catalog():
        event_key = str(row["event_key"]).strip()
        defaults = {
            field: row.get(field, "")
            for field in (
                "section",
                "functionality",
                "route",
                "event_label",
                "trigger_description",
                "primary_recipient_rule",
                "escalation_recipient_rule",
                "channel",
                "priority",
                "action_description",
                "current_coverage",
                "recommendation",
                "frequency_rule",
                "decision",
                "notes",
            )
        }
        defaults["enabled"] = bool(row.get("enabled", True))
        defaults["catalog_managed"] = True
        _, was_created = PortalNotificationRule.objects.get_or_create(
            event_key=event_key,
            defaults=defaults,
        )
        created += int(was_created)

    from apps.access.portal_route_registry import PORTAL_ROUTE_DEFINITIONS

    covered_routes = set(
        PortalNotificationRule.objects.exclude(route="").values_list("route", flat=True)
    )
    for route_definition in PORTAL_ROUTE_DEFINITIONS:
        if route_definition.path in covered_routes:
            continue
        _, was_created = PortalNotificationRule.objects.get_or_create(
            event_key=f"route.{route_definition.route_name}.notification-review"[:160],
            defaults={
                "section": route_definition.section.replace("_", " ").title(),
                "functionality": route_definition.label,
                "route": route_definition.path,
                "event_label": "Avaliar notificações desta funcionalidade",
                "trigger_description": "Funcionalidade registrada no catálogo de rotas do portal.",
                "primary_recipient_rule": "A definir",
                "escalation_recipient_rule": "A definir",
                "channel": "Portal",
                "priority": PortalNotificationRule.Priority.INFO,
                "action_description": "Definir se a funcionalidade possui eventos notificáveis.",
                "current_coverage": "Cadastro automático de nova funcionalidade",
                "recommendation": PortalNotificationRule.Recommendation.POLICY_REQUIRED,
                "frequency_rule": "A definir",
                "decision": PortalNotificationRule.Decision.PENDING,
                "enabled": False,
                "catalog_managed": True,
            },
        )
        if was_created:
            covered_routes.add(route_definition.path)
        created += int(was_created)
    for event_key, operational_defaults in INTEGRATED_RULE_DEFAULTS.items():
        rule = PortalNotificationRule.objects.filter(event_key=event_key, version=0).first()
        if not rule or rule.lifecycle != PortalNotificationRule.Lifecycle.UNCONFIGURED:
            continue
        rule.lifecycle = PortalNotificationRule.Lifecycle.DRAFT
        rule.integration_status = PortalNotificationRule.IntegrationStatus.INTEGRATED
        rule.channels = ["portal"]
        rule.frequency = PortalNotificationRule.Frequency.PER_OCCURRENCE
        rule.notification_title = rule.event_label
        for field, value in operational_defaults.items():
            setattr(rule, field, value)
        rule.save(update_fields=[
            "lifecycle", "integration_status", "channels", "frequency",
            "notification_title", "recipient_types", "target_url", "updated_at",
        ])
    return created
