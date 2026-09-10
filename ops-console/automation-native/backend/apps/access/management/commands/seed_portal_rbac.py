from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access.constants import (
    ALL_ROLES,
    LEGACY_FALHAS_GROUPS,
    is_role_group,
    role_group_name,
)
from apps.access.inference import infer_roles_for_agent
from apps.access.resolve import ensure_role_groups_exist, roles_from_django_groups
from apps.access.roles import validate_role_definitions
from apps.escala_flex.services.permissions import get_agent_for_user
from apps.workforce.models import Agent, UserProfile
from apps.workforce.services.portal_user_sync import provision_portal_users

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Cria grupos role:* e grupos legados Falhas. "
        "Perfis são atribuídos pela UI (Configurações → Usuários). "
        "Use --infer-assignments apenas para bootstrap inicial a partir de AgentHistory."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula sem gravar no banco",
        )
        parser.add_argument(
            "--skip-assignments",
            action="store_true",
            help="Só cria grupos, sem atribuir usuários (comportamento padrão)",
        )
        parser.add_argument(
            "--only-groups",
            action="store_true",
            help="Alias para --skip-assignments",
        )
        parser.add_argument(
            "--infer-assignments",
            action="store_true",
            help="Bootstrap opcional: infere perfis a partir de AgentHistory (não sobrescreve yaml/admin LAN)",
        )
        parser.add_argument(
            "--provision-users",
            action="store_true",
            help="Provisiona contas User a partir dos agentes ativos antes de atribuir perfis",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        infer_assignments = options["infer_assignments"]
        skip_assignments = (
            options["skip_assignments"]
            or options["only_groups"]
            or not infer_assignments
        )
        provision_users = options["provision_users"]

        validate_role_definitions()
        self.stdout.write("Registry e roles validados.")

        if provision_users and not dry_run:
            report = provision_portal_users()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Contas provisionadas: {report['created']} criadas, {report['updated']} atualizadas."
                )
            )
        elif provision_users and dry_run:
            report = provision_portal_users(dry_run=True)
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Provisionaria ~{report['created']} contas User."
                )
            )

        if dry_run:
            self._dry_run_groups()
            if infer_assignments:
                self._dry_run_assignments()
            return

        with transaction.atomic():
            stats = ensure_role_groups_exist()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Grupos role:*: {stats['total']} total, {stats['created']} criados."
                )
            )
            for legacy_name in LEGACY_FALHAS_GROUPS:
                _, created = Group.objects.get_or_create(name=legacy_name)
                if created:
                    self.stdout.write(f"Grupo legado criado: {legacy_name}")

            if skip_assignments:
                self.stdout.write(
                    "Atribuições ignoradas — use Configurações → Usuários ou --infer-assignments."
                )
                return

            report = self._assign_inferred_roles()
            self._print_report(report)

    def _dry_run_groups(self):
        from django.contrib.auth.models import Group

        existing = set(Group.objects.filter(name__startswith="role:").values_list("name", flat=True))
        missing = [role_group_name(r) for r in ALL_ROLES if role_group_name(r) not in existing]
        self.stdout.write(f"[dry-run] Grupos role:* faltantes: {len(missing)}")
        for name in missing:
            self.stdout.write(f"  criaria: {name}")

    def _dry_run_assignments(self):
        report = self._compute_inferred_assignments()
        self._print_report(report, prefix="[dry-run] ")

    def _resolve_agent_for_user(self, user) -> Agent | None:
        agent = get_agent_for_user(user)
        if not agent:
            profile = UserProfile.objects.filter(user=user).select_related("agent").first()
            agent = profile.agent if profile else None
        if not agent:
            lan = (user.username or "").lower()
            agent = Agent.objects.filter(user_lan_id__iexact=lan, active=True).first()
        return agent

    def _compute_inferred_assignments(self) -> dict:
        role_counts: dict[str, int] = {r: 0 for r in ALL_ROLES}
        no_profile: list[str] = []
        users_updated = 0

        for user in User.objects.iterator():
            agent = self._resolve_agent_for_user(user)
            if not agent:
                continue

            roles = set(roles_from_django_groups(user))
            roles |= infer_roles_for_agent(agent)

            if not roles:
                no_profile.append(agent.user_lan_id.lower())
                continue

            for role in roles:
                role_counts[role] = role_counts.get(role, 0) + 1
            users_updated += 1

        return {
            "role_counts": role_counts,
            "no_profile": no_profile,
            "users_updated": users_updated,
        }

    def _assign_inferred_roles(self) -> dict:
        role_groups = {
            role: Group.objects.get(name=role_group_name(role)) for role in ALL_ROLES
        }
        role_counts: dict[str, int] = {r: 0 for r in ALL_ROLES}
        no_profile: list[str] = []
        users_updated = 0

        for user in User.objects.iterator():
            agent = self._resolve_agent_for_user(user)
            if not agent:
                continue

            roles = infer_roles_for_agent(agent)

            if not roles:
                no_profile.append(agent.user_lan_id.lower())
                continue

            current_groups = set(user.groups.values_list("name", flat=True))
            non_role_groups = {g for g in current_groups if not is_role_group(g)}
            target_role_groups = {role_group_name(r) for r in roles}

            user.groups.set(
                list(
                    Group.objects.filter(
                        name__in=list(non_role_groups | target_role_groups)
                    )
                )
            )

            for role in roles:
                role_counts[role] = role_counts.get(role, 0) + 1
            users_updated += 1

        return {
            "role_counts": role_counts,
            "no_profile": no_profile,
            "users_updated": users_updated,
        }

    def _print_report(self, report: dict, prefix: str = "") -> None:
        self.stdout.write(self.style.SUCCESS(f"{prefix}Usuários com perfil atribuído: {report['users_updated']}"))
        self.stdout.write(f"{prefix}Contagem por perfil:")
        for role, count in sorted(report["role_counts"].items()):
            if count:
                self.stdout.write(f"  {role}: {count}")
        orphans = report["no_profile"]
        if orphans:
            self.stdout.write(
                self.style.WARNING(
                    f"{prefix}Agentes sem perfil inferido ({len(orphans)}): "
                    + ", ".join(orphans[:10])
                    + ("..." if len(orphans) > 10 else "")
                )
            )
