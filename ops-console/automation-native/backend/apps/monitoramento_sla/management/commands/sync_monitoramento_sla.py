# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand

from apps.monitoramento_sla.services.sync_incremental import sync_monitoramento_sla


class Command(BaseCommand):
    help = "Recalcula fatos de Monitoramento SLA útil a partir de rotina_detalhado_bruto."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)

    def handle(self, *args, **options):
        run = sync_monitoramento_sla(days=options["days"])
        self.stdout.write(
            self.style.SUCCESS(
                f"status={run.status} detalhe={run.rows_detalhe} consolidado={run.rows_consolidado}"
            )
        )
