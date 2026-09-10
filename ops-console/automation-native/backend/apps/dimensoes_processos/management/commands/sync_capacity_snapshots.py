from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.dimensoes_processos.models import CapacityDailySnapshot
from apps.dimensoes_processos.services.capacity import (
    CAPACITY_PERIOD_METRIC_VERSION,
    calculate_capacity_period_day,
)
from apps.dimensoes_processos.services.capacity_hourly_profiles import (
    materialize_capacity_hourly_profiles,
)
from apps.dimensoes_processos.services.capacity_fingerprint import (
    current_capacity_source_fingerprint,
)

class Command(BaseCommand):
    help = "Pre-calcula snapshots diarios usados pela visao de Capacity por periodo."

    def add_arguments(self, parser):
        parser.add_argument("--date-from", required=True)
        parser.add_argument("--date-to", required=True)
        parser.add_argument("--ttl-minutes", type=int, default=1440)
        parser.add_argument(
            "--scenario",
            choices=("planejamento", "operacao"),
            default="planejamento",
            help="Cenario materializado; operacao pode ser sincronizada por faixa incremental.",
        )
        parser.add_argument(
            "--source-fingerprint",
            default=None,
            help="Versao opaca das fontes upstream; se omitida, sera derivada do resultado.",
        )

    def handle(self, *args, **options):
        try:
            date_from = date.fromisoformat(options["date_from"])
            date_to = date.fromisoformat(options["date_to"])
        except (TypeError, ValueError) as exc:
            raise CommandError("Datas invalidas; use AAAA-MM-DD.") from exc
        if date_from > date_to:
            raise CommandError("date-from nao pode ser posterior a date-to.")
        day_count = (date_to - date_from).days + 1
        if day_count > 366:
            raise CommandError("O sync aceita no maximo 366 dias por execucao.")
        ttl_minutes = options["ttl_minutes"]
        if ttl_minutes < 1:
            raise CommandError("ttl-minutes deve ser positivo.")
        scenario_id = options["scenario"]
        requested_fingerprint = options.get("source_fingerprint")
        if requested_fingerprint is not None:
            requested_fingerprint = requested_fingerprint.strip()
            if not requested_fingerprint:
                raise CommandError("source-fingerprint nao pode ser vazio.")
            if len(requested_fingerprint) > 64:
                raise CommandError("source-fingerprint deve ter no maximo 64 caracteres.")

        generated_at = timezone.now()
        valid_until = generated_at + timedelta(minutes=ttl_minutes)
        generation_id = uuid4()
        profile_cache: dict = {}
        quarterly_cache: dict = {}
        calculated: list[tuple[date, dict]] = []

        # O custo histórico fica no sync, nunca na abertura da tela. O perfil
        # materializado também é invalidado naturalmente após uma nova carga
        # de monitoramento ao reexecutar este comando.
        hourly_profile_result = materialize_capacity_hourly_profiles(date_to)
        if not hourly_profile_result["available"]:
            self.stdout.write(
                self.style.WARNING(
                    "Perfil horário trimestral indisponível; fallbacks uniformes serão explícitos."
                )
            )

        source_fingerprint = requested_fingerprint or current_capacity_source_fingerprint(
            date_from,
            date_to,
            scenario_id=scenario_id,
        )

        # Toda a computacao termina antes da publicacao. Uma falha nao toca os
        # snapshots atualmente servidos.
        for offset in range(day_count):
            on_date = date_from + timedelta(days=offset)
            calculated.append(
                (
                    on_date,
                    calculate_capacity_period_day(
                        on_date,
                        hourly_profile_cache=profile_cache,
                        quarterly_volume_cache=quarterly_cache,
                        include_quarterly=True,
                        scenario_id=scenario_id,
                    ),
                )
            )

        for on_date, payload in calculated:
            payload_metric_version = (payload.get("series") or {}).get("metric_version")
            if payload_metric_version != CAPACITY_PERIOD_METRIC_VERSION:
                raise CommandError(
                    "Snapshot nao publicado: versao da metrica divergente em "
                    f"{on_date.isoformat()} (esperada={CAPACITY_PERIOD_METRIC_VERSION}, "
                    f"recebida={payload_metric_version!r})."
                )

        # A faixa inteira e publicada numa unica transacao e com um generation
        # id comum. Reexecutar substitui deterministicamente as mesmas datas.
        with transaction.atomic():
            for on_date, payload in calculated:
                CapacityDailySnapshot.objects.update_or_create(
                    calculation_date=on_date,
                    scenario_id=scenario_id,
                    metric_version=CAPACITY_PERIOD_METRIC_VERSION,
                    source_fingerprint=source_fingerprint,
                    defaults={
                        "payload": payload,
                        "generated_at": generated_at,
                        "valid_until": valid_until,
                        "generation_id": generation_id,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"{day_count} snapshot(s) publicados; scenario={scenario_id}; "
                f"metric_version={CAPACITY_PERIOD_METRIC_VERSION}; "
                f"source_fingerprint={source_fingerprint}; generation_id={generation_id}; "
                f"valid_until={valid_until.isoformat()}"
            )
        )
