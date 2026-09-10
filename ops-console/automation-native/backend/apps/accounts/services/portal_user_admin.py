"""Serviços administrativos de contas do portal."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import OuterRef, Prefetch, Q, Subquery
from rest_framework.exceptions import ValidationError

from apps.access.constants import (
    ALL_ROLES,
    ROLE_ADM_PORTAL,
    is_role_group,
    parse_role_from_group,
    role_group_name,
)
from apps.access.resolve import ensure_role_groups_exist, load_portal_users_config
from apps.accounts.models import UserChangeHistorico
from apps.accounts.services.user_audit import log_user_change
from apps.workforce.models import AgentHistory, UserProfile
from apps.workforce.services.portal_user_sync import apply_user_password, split_full_name

User = get_user_model()


class PortalUserAdminError(ValidationError):
    pass


def roles_for_user(user) -> list[str]:
    from apps.access.services.rbac_admin import known_role_ids

    known = known_role_ids()
    roles: list[str] = []
    for group_name in user.groups.values_list("name", flat=True):
        role = parse_role_from_group(group_name)
        if role and role in known:
            roles.append(role)
    return sorted(roles)


def _serialize_roles(roles: list[str]) -> str:
    return ",".join(sorted(roles))


def _active_adm_portal_count(exclude_user_id=None) -> int:
    group_name = role_group_name(ROLE_ADM_PORTAL)
    qs = User.objects.filter(is_active=True, groups__name=group_name)
    if exclude_user_id:
        qs = qs.exclude(pk=exclude_user_id)
    return qs.distinct().count()


def _ensure_can_remove_adm_portal(user, *, new_roles: list[str] | None = None, deactivate: bool = False) -> None:
    current_roles = roles_for_user(user)
    will_have_adm = ROLE_ADM_PORTAL in (new_roles if new_roles is not None else current_roles)
    will_be_active = user.is_active and not deactivate
    if ROLE_ADM_PORTAL in current_roles and (deactivate or not will_have_adm) and will_be_active:
        if _active_adm_portal_count(exclude_user_id=user.pk) == 0:
            raise PortalUserAdminError(
                "Não é possível remover ou desativar o último administrador ativo do portal."
            )
    if deactivate and ROLE_ADM_PORTAL in current_roles:
        if _active_adm_portal_count(exclude_user_id=user.pk) == 0:
            raise PortalUserAdminError(
                "Não é possível desativar o último administrador ativo do portal."
            )


def _title_from_history(record) -> str | None:
    if not record:
        return None
    for field in ("job_title", "job_title_sector"):
        value = (getattr(record, field, None) or "").strip()
        if value:
            return value
    return None


def _resolve_job_title(agent, *, cached: str | None = None) -> str | None:
    if cached and str(cached).strip():
        return str(cached).strip()

    if agent is None:
        return None

    seen: set = set()
    ordered: list = []

    def _push(record) -> None:
        if not record or record.pk in seen:
            return
        seen.add(record.pk)
        ordered.append(record)

    prefetched = getattr(agent, "current_histories", None)
    if prefetched:
        _push(prefetched[0])

    for record in agent.history.filter(active=True, final_date__isnull=True).order_by("-start_date"):
        _push(record)

    for record in agent.history.order_by("-start_date"):
        _push(record)

    for record in ordered:
        title = _title_from_history(record)
        if title:
            return title

    return None


def _agent_info(user) -> dict:
    empty = {
        "has_agent": False,
        "agent_name": None,
        "agent_lan_id": None,
        "job_title": None,
        "agent_active": None,
        "leader_name": None,
        "leader_lan_id": None,
        "location": None,
        "team": None,
    }
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return empty
    agent = profile.agent
    cached = getattr(user, "job_title_cached", None)

    leader_name = None
    leader_lan_id = None
    location = None
    team = None
    prefetched = getattr(agent, "current_histories", None)
    hist = prefetched[0] if prefetched else None
    if hist is None:
        hist = (
            AgentHistory.objects.filter(agent=agent, active=True, final_date__isnull=True)
            .select_related("leader")
            .order_by("-start_date")
            .first()
        )
    if hist is not None:
        location = (hist.location or "").strip() or None
        team = (hist.team or "").strip() or None
        if hist.leader_id and hist.leader:
            leader_name = (hist.leader.full_name or "").strip() or None
            leader_lan_id = (hist.leader.user_lan_id or "").strip() or None

    return {
        "has_agent": True,
        "agent_name": agent.full_name,
        "agent_lan_id": agent.user_lan_id,
        "job_title": _resolve_job_title(agent, cached=cached),
        "agent_active": agent.active,
        "leader_name": leader_name,
        "leader_lan_id": leader_lan_id,
        "location": location,
        "team": team,
    }


def serialize_portal_user(user) -> dict:
    info = _agent_info(user)
    return {
        "id": str(user.pk),
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "display_name": _display_name(user),
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "last_login": user.last_login,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "roles": roles_for_user(user),
        **info,
    }


def _display_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p.strip() for p in parts if p and p.strip()).strip()
    return name or user.username


def list_portal_users(
    *,
    q: str = "",
    is_active: bool | None = None,
    role: str = "",
    has_agent: bool | None = None,
) -> list[dict]:
    current_history_qs = (
        AgentHistory.objects.filter(
            active=True,
            final_date__isnull=True,
        )
        .select_related("leader")
        .order_by("-start_date")
    )
    current_job_title = AgentHistory.objects.filter(
        agent=OuterRef("profile__agent_id"),
        active=True,
        final_date__isnull=True,
    ).exclude(Q(job_title="") | Q(job_title__isnull=True)).order_by("-start_date")
    qs = (
        User.objects.select_related("profile__agent")
        .annotate(job_title_cached=Subquery(current_job_title.values("job_title")[:1]))
        .prefetch_related(
            Prefetch(
                "profile__agent__history",
                queryset=current_history_qs,
                to_attr="current_histories",
            )
        )
        .order_by("username")
    )
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    if role:
        qs = qs.filter(groups__name=role_group_name(role))
    if has_agent is True:
        qs = qs.filter(profile__isnull=False)
    elif has_agent is False:
        qs = qs.filter(profile__isnull=True)

    return [serialize_portal_user(user) for user in qs.distinct()]


@transaction.atomic
def create_external_user(
    *,
    actor,
    username: str,
    first_name: str = "",
    last_name: str = "",
    email: str | None = None,
    is_active: bool = False,
    roles: list[str] | None = None,
) -> dict:
    username = (username or "").strip().lower()
    if not username:
        raise PortalUserAdminError({"username": "Informe a matrícula/LAN ID."})
    if User.objects.filter(username=username).exists():
        raise PortalUserAdminError({"username": "Já existe uma conta com esta matrícula."})

    if not first_name and not last_name:
        first_name, last_name = split_full_name(username)

    user = User.objects.create(
        username=username,
        email=email or None,
        first_name=first_name,
        last_name=last_name,
        is_active=is_active,
        created_by=actor,
        updated_by=actor,
    )
    apply_user_password(user, is_new=True, reset_passwords=True, preserved=False)
    user.save()

    log_user_change(
        target_user=user,
        actor=actor,
        action=UserChangeHistorico.ACTION_CREATE,
        field_name="username",
        old_value="",
        new_value=username,
    )

    if roles:
        set_user_roles(user, roles, actor=actor)

    return serialize_portal_user(user)


@transaction.atomic
def update_user(
    *,
    actor,
    user,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    is_active: bool | None = None,
    roles: list[str] | None = None,
) -> dict:
    if is_active is False and actor.pk == user.pk:
        raise PortalUserAdminError("Você não pode desativar a própria conta.")

    if is_active is not None and is_active != user.is_active:
        if not is_active:
            _ensure_can_remove_adm_portal(user, deactivate=True)
        old = str(user.is_active)
        user.is_active = is_active
        log_user_change(
            target_user=user,
            actor=actor,
            action=UserChangeHistorico.ACTION_ACTIVATE if is_active else UserChangeHistorico.ACTION_DEACTIVATE,
            field_name="is_active",
            old_value=old,
            new_value=str(is_active),
        )

    for field, value in (
        ("first_name", first_name),
        ("last_name", last_name),
        ("email", email),
    ):
        if value is None:
            continue
        old = getattr(user, field) or ""
        new = value or ""
        if old != new:
            setattr(user, field, new or (None if field == "email" else ""))
            log_user_change(
                target_user=user,
                actor=actor,
                action=UserChangeHistorico.ACTION_EDIT,
                field_name=field,
                old_value=str(old),
                new_value=str(new),
            )

    if roles is not None:
        set_user_roles(user, roles, actor=actor)

    user.updated_by = actor
    user.save()
    return serialize_portal_user(user)


@transaction.atomic
def set_user_roles(user, roles: list[str], *, actor) -> None:
    from apps.access.services.rbac_admin import known_role_ids

    invalid = set(roles) - known_role_ids()
    if invalid:
        raise PortalUserAdminError({"roles": f"Perfis inválidos: {', '.join(sorted(invalid))}"})

    new_roles = sorted(set(roles))
    old_roles = roles_for_user(user)
    if old_roles == new_roles:
        return

    if ROLE_ADM_PORTAL in old_roles and ROLE_ADM_PORTAL not in new_roles and user.is_active:
        _ensure_can_remove_adm_portal(user, new_roles=new_roles)

    ensure_role_groups_exist()
    current_groups = set(user.groups.values_list("name", flat=True))
    non_role_groups = {g for g in current_groups if not is_role_group(g)}
    target_role_groups = {role_group_name(r) for r in new_roles}
    target_names = list(non_role_groups | target_role_groups)
    user.groups.set(Group.objects.filter(name__in=target_names))

    log_user_change(
        target_user=user,
        actor=actor,
        action=UserChangeHistorico.ACTION_ROLES,
        field_name="roles",
        old_value=_serialize_roles(old_roles),
        new_value=_serialize_roles(new_roles),
    )


@transaction.atomic
def reset_password(user, *, actor) -> dict:
    apply_user_password(user, is_new=False, reset_passwords=True, preserved=False)
    user.updated_by = actor
    user.save()
    log_user_change(
        target_user=user,
        actor=actor,
        action=UserChangeHistorico.ACTION_PASSWORD_RESET,
        field_name="password",
        old_value="",
        new_value="reset",
    )
    return serialize_portal_user(user)


@transaction.atomic
def bulk_set_active(*, actor, user_ids: list, is_active: bool) -> dict:
    updated = 0
    skipped = 0
    errors: list[str] = []
    for user_id in user_ids:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            skipped += 1
            continue
        if user.is_active == is_active:
            skipped += 1
            continue
        try:
            update_user(actor=actor, user=user, is_active=is_active)
            updated += 1
        except PortalUserAdminError as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                msg = next(iter(detail.values()), str(detail))
                if isinstance(msg, list):
                    msg = msg[0]
            else:
                msg = str(detail)
            errors.append(f"{user.username}: {msg}")
            skipped += 1
    return {"updated": updated, "skipped": skipped, "errors": errors}


@transaction.atomic
def bulk_reset_password(*, actor, user_ids: list) -> dict:
    updated = 0
    skipped = 0
    for user_id in user_ids:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            skipped += 1
            continue
        reset_password(user, actor=actor)
        updated += 1
    return {"updated": updated, "skipped": skipped}


def _bulk_error_message(exc: PortalUserAdminError) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        msg = next(iter(detail.values()), str(detail))
        if isinstance(msg, list):
            msg = msg[0]
        return str(msg)
    return str(detail)


@transaction.atomic
def bulk_replicate_rbac(*, actor, source_user_id, target_user_ids: list) -> dict:
    """Copia os perfis RBAC da origem para os destinatários (substituição)."""
    try:
        source = User.objects.get(pk=source_user_id)
    except User.DoesNotExist as exc:
        raise PortalUserAdminError({"source_user_id": "Usuário de origem não encontrado."}) from exc

    source_roles = roles_for_user(source)
    source_id = str(source.pk)
    updated = 0
    skipped = 0
    errors: list[str] = []

    for user_id in target_user_ids:
        target_id = str(user_id)
        if target_id == source_id:
            skipped += 1
            continue
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            errors.append(f"{target_id}: usuário inexistente")
            skipped += 1
            continue
        if actor.pk == user.pk and ROLE_ADM_PORTAL in roles_for_user(user) and ROLE_ADM_PORTAL not in source_roles:
            errors.append(f"{user.username}: não é permitido remover o próprio acesso de administrador em massa")
            skipped += 1
            continue
        try:
            before = roles_for_user(user)
            set_user_roles(user, source_roles, actor=actor)
            after = roles_for_user(user)
            if before == after:
                skipped += 1
            else:
                updated += 1
        except PortalUserAdminError as exc:
            errors.append(f"{user.username}: {_bulk_error_message(exc)}")
            skipped += 1

    return {"updated": updated, "skipped": skipped, "errors": errors}


@transaction.atomic
def bulk_set_roles(
    *,
    actor,
    user_ids: list,
    operation: str,
    roles: list[str],
) -> dict:
    """Altera perfis RBAC em massa: replace | add | remove."""
    from apps.access.services.rbac_admin import known_role_ids

    op = (operation or "").strip().lower()
    if op not in {"replace", "add", "remove"}:
        raise PortalUserAdminError({"operation": "Operação inválida. Use replace, add ou remove."})

    requested = list(roles or [])
    invalid = set(requested) - known_role_ids()
    if invalid:
        raise PortalUserAdminError(
            {"roles": f"Perfis inválidos: {', '.join(sorted(invalid))}"}
        )

    updated = 0
    skipped = 0
    errors: list[str] = []

    for user_id in user_ids:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            errors.append(f"{user_id}: usuário inexistente")
            skipped += 1
            continue

        current = set(roles_for_user(user))
        requested_set = set(requested)
        if op == "replace":
            next_roles = sorted(requested_set)
        elif op == "add":
            next_roles = sorted(current | requested_set)
        else:
            next_roles = sorted(current - requested_set)

        if set(next_roles) == current:
            skipped += 1
            continue

        if (
            actor.pk == user.pk
            and ROLE_ADM_PORTAL in current
            and ROLE_ADM_PORTAL not in next_roles
        ):
            errors.append(
                f"{user.username}: não é permitido remover o próprio acesso de administrador em massa"
            )
            skipped += 1
            continue

        try:
            set_user_roles(user, next_roles, actor=actor)
            updated += 1
        except PortalUserAdminError as exc:
            errors.append(f"{user.username}: {_bulk_error_message(exc)}")
            skipped += 1

    return {"updated": updated, "skipped": skipped, "errors": errors}


@transaction.atomic
def deactivate_all_except_always_active(*, actor=None) -> dict:
    """One-shot: desativa todos exceto always_active do portal_users.yaml."""
    config = load_portal_users_config()
    keep = {x.strip().lower() for x in (config.get("always_active") or []) if x.strip()}
    deactivated = 0
    for user in User.objects.filter(is_active=True).iterator():
        uname = (user.username or "").lower()
        if uname in keep:
            continue
        user.is_active = False
        user.save(update_fields=["is_active"])
        log_user_change(
            target_user=user,
            actor=actor,
            action=UserChangeHistorico.ACTION_BULK,
            field_name="is_active",
            old_value="True",
            new_value="False",
        )
        deactivated += 1
    return {"deactivated": deactivated, "preserved": sorted(keep)}


def get_user_historico(user) -> list[dict]:
    from apps.accounts.services.user_audit import historico_summary

    entries = UserChangeHistorico.objects.filter(target_user=user).select_related("actor")
    result = []
    for entry in entries:
        actor_name = entry.actor.username if entry.actor else "sistema"
        result.append(
            {
                "id": str(entry.pk),
                "action": entry.action,
                "field_name": entry.field_name,
                "old_value": entry.old_value,
                "new_value": entry.new_value,
                "summary": historico_summary(entry),
                "actor_username": actor_name,
                "created_at": entry.created_at,
            }
        )
    return result
