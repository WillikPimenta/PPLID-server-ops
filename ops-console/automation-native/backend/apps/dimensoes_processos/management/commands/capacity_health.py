from __future__ import annotations

import json
from datetime import date, timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.dimensoes_processos.services.capacity_observability import (
    inspect_snapshot_health,
    observe_capacity_operation,
)


class Command(BaseCommand):
    help = (
        "Verifica snapshots do Capacity e, opcionalmente, atualiza a faixa antes "
        "do vencimento."
    )

    def add_arguments(self, parser):
        parser.add_argument("--date-from")
        parser.add_argument("--date-to")
        parser.add_argument("--days", type=int, default=31)
        parser.add_argument("--refresh-before-minutes", type=int, default=120)
        parser.add_argument("--refresh-if-due", action="store_true")
        parser.add_argument("--ttl-minutes", type=int, default=1440)
        parser.add_argument("--count-queries", action="store_true")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--fail-on-unhealthy", action="store_true")
        parser.add_argument(
            "--scenario",
            choices=("planejamento", "operacao"),
            default="planejamento",
        )

    @staticmethod
    def _parse_date(value: str | None, *, label: str) -> date | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(f"{label} invalida; use AAAA-MM-DD.") from exc

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1 or days > 366:
            raise CommandError("days deve estar entre 1 e 366.")
        if options["refresh_before_minutes"] < 0:
            raise CommandError("refresh-before-minutes nao pode ser negativo.")
        if options["ttl_minutes"] < 1:
            raise CommandError("ttl-minutes deve ser positivo.")

        date_from = self._parse_date(options["date_from"], label="date-from")
        date_to = self._parse_date(options["date_to"], label="date-to")
        if date_from is None:
            date_from = timezone.localdate()
        if date_to is None:
            date_to = date_from + timedelta(days=days - 1)
        if date_from > date_to:
            raise CommandError("date-from nao pode ser posterior a date-to.")
        day_count = (date_to - date_from).days + 1
        if day_count > 366:
            raise CommandError("A faixa aceita no maximo 366 dias.")

        health, health_telemetry = self._inspect(
            date_from,
            date_to,
            options=options,
            operation="snapshot_health_before_refresh",
        )
        refreshed = False
        refresh_telemetry = None

        if health["refresh_required"] and options["refresh_if_due"]:
            refreshed = True
            with observe_capacity_operation(
                "snapshot_refresh",
                scenario=options["scenario"],
                days=day_count,
                count_queries=options["count_queries"],
                extra={"snapshot_state": health["state"]},
            ) as refresh_observation:
                call_command(
                    "sync_capacity_snapshots",
                    date_from=date_from.isoformat(),
                    date_to=date_to.isoformat(),
                    ttl_minutes=options["ttl_minutes"],
                    scenario=options["scenario"],
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
            refresh_telemetry = refresh_observation.as_event()
            health, health_telemetry = self._inspect(
                date_from,
                date_to,
                options=options,
                operation="snapshot_health_after_refresh",
            )

        result = {
            "refreshed": refreshed,
            "telemetry": {
                "health": health_telemetry,
                "refresh": refresh_telemetry,
            },
            **health,
        }
        if options["json"]:
            self.stdout.write(
                json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
            )
        else:
            counts = result["counts"]
            self.stdout.write(
                "Capacity snapshots: "
                f"state={result['state']}; days={result['days']}; "
                f"fresh={counts['fresh']}; expiring={counts['expiring']}; "
                f"expired={counts['expired']}; incompatible={counts['incompatible']}; "
                f"missing={counts['missing']}; refreshed={str(refreshed).lower()}"
            )

        if options["fail_on_unhealthy"] and result["state"] != "fresh":
            raise CommandError(f"Snapshots Capacity nao saudaveis: {result['state']}")

    @staticmethod
    def _inspect(date_from, date_to, *, options, operation):
        day_count = (date_to - date_from).days + 1
        with observe_capacity_operation(
            operation,
            scenario=options["scenario"],
            days=day_count,
            count_queries=options["count_queries"],
        ) as observation:
            health = inspect_snapshot_health(
                date_from,
                date_to,
                refresh_before_minutes=options["refresh_before_minutes"],
                scenario_id=options["scenario"],
            )
            observation.snapshot_state = health["state"]
        return health, observation.as_event()
