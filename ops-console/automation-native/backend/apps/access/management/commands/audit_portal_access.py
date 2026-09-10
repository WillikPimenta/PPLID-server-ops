from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.access.resolve import resolve_user_access
from apps.escala_flex.services.permissions import get_agent_for_user

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Audita acesso RBAC efetivo de um usuário: perfis, permissões, bypass e grupos Django."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            required=True,
            help="Username (LAN ID) da conta a auditar.",
        )
        parser.add_argument(
            "--permissions",
            nargs="*",
            default=[],
            help="Permissões opcionais para checar (ex.: planejamento.headcount.view).",
        )

    def handle(self, *args, **options):
        username = options["username"].strip().lower()
        user = User.objects.filter(username__iexact=username).first()
        if not user:
            raise CommandError(f"Usuário não encontrado: {username}")

        access = resolve_user_access(user)
        agent = get_agent_for_user(user)
        groups = list(user.groups.values_list("name", flat=True).order_by("name"))

        self.stdout.write(f"Usuário: {user.username}")
        self.stdout.write(f"Ativo: {user.is_active}")
        self.stdout.write(f"is_staff: {user.is_staff}")
        self.stdout.write(f"is_superuser: {user.is_superuser}")
        self.stdout.write(f"Agent vinculado: {agent.user_lan_id if agent else '(nenhum)'}")
        self.stdout.write(f"bypass: {access.get('bypass')}")
        self.stdout.write(f"Perfis ({len(access.get('roles', []))}): {', '.join(access.get('roles', [])) or '(nenhum)'}")
        self.stdout.write(f"Escopos: {access.get('scopes')}")
        self.stdout.write(f"Grupos Django ({len(groups)}):")
        for name in groups:
            self.stdout.write(f"  - {name}")

        permissions = access.get("permissions") or []
        self.stdout.write(f"Permissões efetivas: {len(permissions)}")
        if permissions:
            for code in permissions[:30]:
                self.stdout.write(f"  - {code}")
            if len(permissions) > 30:
                self.stdout.write(f"  ... +{len(permissions) - 30} mais")

        for code in options["permissions"]:
            allowed = code in permissions or access.get("bypass")
            status = self.style.SUCCESS("OK") if allowed else self.style.ERROR("NEGADO")
            self.stdout.write(f"Checagem {code}: {status}")
