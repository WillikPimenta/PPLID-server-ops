from django.core.management.base import BaseCommand

from apps.access.models import PortalRoleDefinition
from apps.access.services.role_definitions import all_role_ids, seed_defaults_for_role


class Command(BaseCommand):
    help = (
        "Cria definições RBAC de perfis a partir de ROLE_DEFINITIONS. "
        "Só preenche defaults na criação — não sobrescreve edições da UI."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula sem gravar no banco",
        )
        parser.add_argument(
            "--sync-uncustomized",
            action="store_true",
            help="Atualiza permissões de perfis que ainda estão iguais ao padrão do código",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        sync_uncustomized = options["sync_uncustomized"]
        created = 0
        skipped = 0
        synced = 0

        for role in all_role_ids():
            defaults = seed_defaults_for_role(role)
            row = PortalRoleDefinition.objects.filter(role=role).first()

            if row is None:
                if dry_run:
                    self.stdout.write(f"[dry-run] criaria: {role}")
                    created += 1
                    continue

                PortalRoleDefinition.objects.create(**defaults)
                created += 1
                self.stdout.write(f"Criada definição: {role}")
                continue

            skipped += 1

            # Perfis não editáveis (adm_portal): sempre alinhar ao código.
            if not row.is_editable:
                new_permissions = defaults["permissions"]
                if (
                    sorted(row.permissions or []) != new_permissions
                    or row.default_scope != defaults["default_scope"]
                ):
                    if dry_run:
                        self.stdout.write(f"[dry-run] sincronizaria (não editável): {role}")
                        synced += 1
                        continue
                    row.permissions = new_permissions
                    row.default_scope = defaults["default_scope"]
                    row.save(update_fields=["permissions", "default_scope", "updated_at"])
                    synced += 1
                    self.stdout.write(f"Sincronizada definição (não editável): {role}")
                continue

            if not sync_uncustomized:
                continue

            from apps.access.services.role_definitions import is_role_customized

            if is_role_customized(row):
                continue

            new_permissions = defaults["permissions"]
            if sorted(row.permissions or []) == new_permissions:
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] sincronizaria: {role}")
                synced += 1
                continue

            row.permissions = new_permissions
            row.default_scope = defaults["default_scope"]
            row.save(update_fields=["permissions", "default_scope", "updated_at"])
            synced += 1
            self.stdout.write(f"Sincronizada definição: {role}")

        parts = [f"{created} criadas", f"{skipped} já existentes"]
        if sync_uncustomized:
            parts.append(f"{synced} sincronizadas")
        self.stdout.write(self.style.SUCCESS(f"Concluído: {', '.join(parts)}."))
