# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.suporte_claro.models import SuporteClaroRegistro


class Command(BaseCommand):
    help = (
        "Ajusta N demandas sem chamado externo para received_at de hoje "
        "(aparecem em Pendentes de formalização Jira)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=4,
            help="Quantidade de registros a ajustar (padrão: 4).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = max(1, options["count"])
        today = timezone.localdate()
        base_dt = timezone.make_aware(datetime.combine(today, datetime.min.time()))

        candidates = list(
            SuporteClaroRegistro.objects.filter(chamado_sistema="")
            .order_by("-received_at", "-id")[:count]
        )

        if len(candidates) < count:
            self.stdout.write(
                self.style.WARNING(
                    f"Apenas {len(candidates)} registro(s) sem chamado encontrado(s); "
                    f"ajustando o que houver."
                )
            )

        if not candidates:
            self.stdout.write(
                self.style.ERROR(
                    "Nenhum registro elegível. Crie dados com: "
                    "python manage.py seed_suporte_claro_demo"
                )
            )
            return

        updated = []
        for index, registro in enumerate(candidates):
            registro.received_at = base_dt + timedelta(hours=9, minutes=15 * index)
            registro.save(update_fields=["received_at"])
            updated.append(registro.protocolo)

        self.stdout.write(
            self.style.SUCCESS(
                f"Ajustados {len(updated)} registro(s) para {today.isoformat()}: "
                + ", ".join(updated)
            )
        )
