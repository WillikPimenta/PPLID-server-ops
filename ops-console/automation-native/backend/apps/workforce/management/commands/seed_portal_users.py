from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.workforce.services.portal_user_sync import provision_portal_users


class Command(BaseCommand):
    help = (
        "Cria contas User + UserProfile a partir dos agentes ativos (matrículas/LAN ID) "
        "e atribui perfis RBAC (idempotente, replicável em todos os ambientes)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula provisionamento sem gravar no banco",
        )
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Redefine senha das contas (exceto preserve_usernames)",
        )
        parser.add_argument(
            "--skip-rbac",
            action="store_true",
            help="Só provisiona contas, sem rodar seed_portal_rbac",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        reset_passwords = options["reset_passwords"]
        skip_rbac = options["skip_rbac"]

        if dry_run:
            report = provision_portal_users(dry_run=True)
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Provisionaria ~{report['created']} contas a partir dos agentes elegíveis."
                )
            )
            if not skip_rbac:
                call_command("seed_portal_rbac", dry_run=True)
            return

        report = provision_portal_users(reset_passwords=reset_passwords)
        self._print_provision_report(report)

        if skip_rbac:
            self.stdout.write("Atribuição RBAC ignorada (--skip-rbac).")
            return

        self.stdout.write("Atribuindo perfis RBAC...")
        call_command("seed_portal_rbac")

    def _print_provision_report(self, report: dict) -> None:
        self.stdout.write(self.style.SUCCESS(f"Contas criadas: {report['created']}"))
        self.stdout.write(f"Contas atualizadas: {report['updated']}")
        self.stdout.write(f"Contas preservadas (skip): {report['skipped']}")
        self.stdout.write(f"Perfis vinculados: {report['profiles_linked']}")
        if report.get("deactivated"):
            self.stdout.write(f"Contas desativadas (órfãs): {report['deactivated']}")
        conflicts = report.get("email_conflicts") or []
        if conflicts:
            self.stdout.write(
                self.style.WARNING(
                    f"Conflitos de e-mail ({len(conflicts)}): "
                    + ", ".join(conflicts[:10])
                    + ("..." if len(conflicts) > 10 else "")
                )
            )
