from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.dimensoes_processos.services.capacity_hourly_profiles import (
    materialize_capacity_hourly_profiles,
)


class Command(BaseCommand):
    help = "Materializa perfis horários confiáveis por workflow/cliente e dia da semana."

    def add_arguments(self, parser):
        parser.add_argument("--reference-date", required=True)

    def handle(self, *args, **options):
        try:
            reference_date = date.fromisoformat(options["reference_date"])
        except (TypeError, ValueError) as exc:
            raise CommandError("Data inválida; use AAAA-MM-DD.") from exc
        result = materialize_capacity_hourly_profiles(reference_date)
        if not result["available"]:
            raise CommandError("Nenhum trimestre-calendário completo disponível.")
        self.stdout.write(
            self.style.SUCCESS(
                f"{result['created']} perfil(is) publicados para "
                f"{result['quarter_from']} a {result['quarter_to']}."
            )
        )
