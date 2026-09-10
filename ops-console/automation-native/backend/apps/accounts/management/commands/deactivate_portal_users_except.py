from django.core.management.base import BaseCommand

from apps.accounts.services.portal_user_admin import deactivate_all_except_always_active


class Command(BaseCommand):
    help = (
        "Desativa todas as contas User ativas, exceto LAN IDs em always_active "
        "(portal_users.yaml). Registra histórico de auditoria."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra quantas contas seriam desativadas sem gravar",
        )

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        from apps.access.resolve import load_portal_users_config

        User = get_user_model()
        config = load_portal_users_config()
        keep = {x.strip().lower() for x in (config.get("always_active") or []) if x.strip()}

        if options["dry_run"]:
            count = 0
            for user in User.objects.filter(is_active=True).iterator():
                if (user.username or "").lower() not in keep:
                    count += 1
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Desativaria {count} contas. Preservadas: {', '.join(sorted(keep)) or '(nenhuma)'}"
                )
            )
            return

        report = deactivate_all_except_always_active(actor=None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Desativadas: {report['deactivated']}. "
                f"Preservadas: {', '.join(report['preserved']) or '(nenhuma)'}"
            )
        )
