# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand, CommandError

from apps.dimensoes_processos.services.derivacao_etapa.purge import purge_derivacao_etapa


class Command(BaseCommand):
    help = "Apaga toda a base derivacao_etapa (diária, scans, uploads e aliases)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Obrigatório para executar a exclusão.",
        )
        parser.add_argument(
            "--keep-aliases",
            action="store_true",
            help="Preserva dim_nome_alias (associações manuais).",
        )
        parser.add_argument(
            "--keep-capacity-snapshots",
            action="store_true",
            help="Preserva snapshots materializados do Capacity.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Use --confirm para apagar a base derivacao_etapa.")

        counts = purge_derivacao_etapa(
            include_aliases=not options["keep_aliases"],
            include_capacity_snapshots=not options["keep_capacity_snapshots"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Base derivacao_etapa removida: "
                + ", ".join(f"{key}={value}" for key, value in counts.items())
            )
        )
