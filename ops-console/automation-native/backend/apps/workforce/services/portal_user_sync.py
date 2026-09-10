"""Provisionamento idempotente de contas User a partir de Agent."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.access.resolve import load_portal_users_config
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


@dataclass
class ProvisionReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    profiles_linked: int = 0
    email_conflicts: list[str] = field(default_factory=list)
    deactivated: int = 0

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "profiles_linked": self.profiles_linked,
            "email_conflicts": self.email_conflicts,
            "deactivated": self.deactivated,
        }


def split_full_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    email = str(value).strip()
    return email or None


def resolve_unique_email(email: str | None, username: str, used_emails: set[str]) -> str | None:
    if not email:
        return None
    if email in used_emails:
        return None
    existing = (
        User.objects.filter(email=email)
        .exclude(username=username)
        .exists()
    )
    if existing:
        return None
    return email


def select_agents_for_provision(config: dict | None = None) -> list[Agent]:
    config = config or load_portal_users_config()
    always = set(config.get("always_provision") or [])
    qs = Agent.objects.all().order_by("user_lan_id")
    if config.get("active_only", True):
        agents = list(qs.filter(active=True))
        always_agents = list(qs.filter(user_lan_id__in=always).exclude(active=True))
        by_lan = {a.user_lan_id.lower(): a for a in agents + always_agents}
        return list(by_lan.values())
    return list(qs)


def link_user_to_agent(user, agent: Agent) -> bool:
    """
    Garante vínculo UserProfile entre conta e colaborador.
    Idempotente — realoca perfil existente quando snapshot/import deixou
    agent já vinculado a outro User (comum após restore_db_snapshot).
    """
    user_profile = UserProfile.objects.filter(user=user).first()
    agent_profile = UserProfile.objects.filter(agent=agent).first()

    if user_profile and agent_profile:
        if user_profile.pk == agent_profile.pk:
            return False
        if user_profile.agent_id != agent.id:
            user_profile.delete()
        if agent_profile.user_id != user.id:
            agent_profile.user = user
            agent_profile.save(update_fields=["user"])
            return True
        return False

    if user_profile:
        if user_profile.agent_id == agent.id:
            return False
        user_profile.agent = agent
        user_profile.save(update_fields=["agent"])
        return True

    if agent_profile:
        if agent_profile.user_id == user.id:
            return False
        agent_profile.user = user
        agent_profile.save(update_fields=["user"])
        return True

    UserProfile.objects.create(user=user, agent=agent)
    return True


def resolve_initial_is_active(username: str, config: dict) -> bool:
  """Novas contas sobem inativas, exceto LAN IDs em always_active."""
  always_active = {x.strip().lower() for x in (config.get("always_active") or []) if x.strip()}
  return username.lower() in always_active


def apply_user_password(user, *, is_new: bool, reset_passwords: bool, preserved: bool) -> None:
    if preserved and not is_new and not reset_passwords:
        return
    if not is_new and not reset_passwords:
        return
    default_password = getattr(settings, "PORTAL_SEED_DEFAULT_PASSWORD", "") or ""
    if default_password:
        user.set_password(default_password)
        user.must_change_password = True
    else:
        user.set_unusable_password()
        user.must_change_password = False


def provision_portal_users(
    *,
    dry_run: bool = False,
    reset_passwords: bool = False,
    config: dict | None = None,
) -> dict:
    """
    Cria/atualiza User + UserProfile para agentes elegíveis.
    Idempotente — seguro para rodar em todos os ambientes após import_base_xlsx.
    """
    config = config or load_portal_users_config()
    preserve = set(config.get("preserve_usernames") or [])
    report = ProvisionReport()

    agents = select_agents_for_provision(config)
    if dry_run:
        report.created = len(agents)
        return report.as_dict()

    used_emails: set[str] = set(
        User.objects.exclude(email__isnull=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )

    with transaction.atomic():
        for agent in agents:
            username = agent.user_lan_id.strip().lower()
            if not username:
                report.skipped += 1
                continue

            preserved = username in preserve
            first_name, last_name = split_full_name(agent.full_name)
            raw_email = normalize_email(agent.email)
            email = resolve_unique_email(raw_email, username, used_emails)
            if raw_email and email is None:
                report.email_conflicts.append(username)

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_active": resolve_initial_is_active(username, config),
                    "must_change_password": False,
                },
            )

            if created:
                apply_user_password(
                    user,
                    is_new=True,
                    reset_passwords=reset_passwords,
                    preserved=preserved,
                )
                user.save()
                report.created += 1
            else:
                if not preserved:
                    user.first_name = first_name
                    user.last_name = last_name
                    if email is not None:
                        user.email = email
                    apply_user_password(
                        user,
                        is_new=False,
                        reset_passwords=reset_passwords,
                        preserved=preserved,
                    )
                    user.save()
                    report.updated += 1
                else:
                    report.skipped += 1

            if email:
                used_emails.add(email)

            if link_user_to_agent(user, agent):
                report.profiles_linked += 1

        if config.get("deactivate_orphans"):
            provisioned_lans = {a.user_lan_id.lower() for a in agents}
            for user in User.objects.filter(is_active=True).iterator():
                uname = (user.username or "").lower()
                if uname in preserve:
                    continue
                if uname not in provisioned_lans:
                    user.is_active = False
                    user.save(update_fields=["is_active"])
                    report.deactivated += 1

    return report.as_dict()
