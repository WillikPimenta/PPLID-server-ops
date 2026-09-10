from django.core.management.base import BaseCommand

from apps.access.models import PortalRoutePolicy
from apps.access.portal_route_registry import PORTAL_ROUTE_BY_NAME, SYSTEM_ROUTE_NAMES
from apps.access.services.route_policies import definition_to_defaults


class Command(BaseCommand):
    help = (
        "Cria políticas RBAC de rotas do frontend a partir do catálogo canônico. "
        "Só preenche defaults na criação — não sobrescreve edições existentes. "
        "Use --fix-open para preencher rotas com permissions_any vazio."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula sem gravar no banco",
        )
        parser.add_argument(
            "--fix-open",
            action="store_true",
            help="Atualiza políticas existentes com permissions_any vazio (exceto rotas de sistema)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        fix_open = options["fix_open"]
        created = 0
        skipped = 0
        fixed = 0

        for definition in PORTAL_ROUTE_BY_NAME.values():
            defaults = definition_to_defaults(definition)
            route_name = defaults.pop("route_name")
            policy = PortalRoutePolicy.objects.filter(route_name=route_name).first()

            if policy is None:
                if dry_run:
                    self.stdout.write(f"[dry-run] criaria: {route_name}")
                    created += 1
                    continue

                PortalRoutePolicy.objects.create(route_name=route_name, **defaults)
                created += 1
                self.stdout.write(f"Criada política: {route_name}")
                continue

            skipped += 1

            if not fix_open or route_name in SYSTEM_ROUTE_NAMES:
                continue

            registry_defaults = definition_to_defaults(definition)
            current_any = list(policy.permissions_any or [])
            if current_any:
                continue

            new_any = registry_defaults["permissions_any"]
            new_all = registry_defaults["permissions_all"]
            if dry_run:
                self.stdout.write(f"[dry-run] corrigiria: {route_name} → {new_any}")
                fixed += 1
                continue

            policy.permissions_any = new_any
            policy.permissions_all = new_all
            policy.save(update_fields=["permissions_any", "permissions_all", "updated_at"])
            fixed += 1
            self.stdout.write(f"Corrigida política aberta: {route_name}")

        parts = [f"{created} criadas", f"{skipped} já existentes"]
        if fix_open:
            parts.append(f"{fixed} corrigidas")
        self.stdout.write(self.style.SUCCESS(f"Concluído: {', '.join(parts)}."))
