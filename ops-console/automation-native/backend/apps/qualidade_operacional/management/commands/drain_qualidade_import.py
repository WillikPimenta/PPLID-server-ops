# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand

from apps.qualidade_operacional.services.retroactive_import import drain_qualidade_imports


class Command(BaseCommand):
    help = "Processa filas de carga retroativa (validação e importação) da Qualidade Operacional."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=3,
            help="Máximo de lotes processados neste ciclo.",
        )

    def handle(self, *args, **options):
        processed = drain_qualidade_imports(max_jobs=max(1, int(options["max_jobs"])))
        self.stdout.write(self.style.SUCCESS(f"drain_qualidade_import: {processed} lote(s)"))
