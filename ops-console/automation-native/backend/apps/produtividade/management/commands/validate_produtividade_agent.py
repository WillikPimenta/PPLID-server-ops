# -*- coding: utf-8 -*-
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.produtividade.services.validate_agent import (
    format_validation_report,
    validate_agent_metrics,
    validation_report_json,
)


class Command(BaseCommand):
    help = (
        "Reconcilia métricas de produtividade por agente "
        "(grupos shift, TMA, impacto, ritmo, monitor)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "matricula",
            type=str,
            help="Matrícula do agente (ex.: c14054q)",
        )
        parser.add_argument(
            "--date",
            type=str,
            help="Dia único ISO (ex.: 2026-06-30)",
        )
        parser.add_argument(
            "--start-date",
            type=str,
            help="Início do período ISO",
        )
        parser.add_argument(
            "--end-date",
            type=str,
            help="Fim do período ISO",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Saída em JSON",
        )

    def handle(self, *args, **options):
        matricula = options["matricula"]
        day = self._parse_date(options.get("date"), "date")
        start = self._parse_date(options.get("start_date"), "start-date")
        end = self._parse_date(options.get("end_date"), "end-date")

        if day and (start or end):
            raise CommandError("Use --date OU --start-date/--end-date, não ambos.")

        report = validate_agent_metrics(
            matricula,
            day=day,
            start_date=start,
            end_date=end,
        )

        if options.get("json"):
            self.stdout.write(validation_report_json(report))
        else:
            self.stdout.write(format_validation_report(report))

        if report.get("error"):
            raise CommandError(report["error"])

    @staticmethod
    def _parse_date(value: str | None, flag: str):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError(f"--{flag} inválido: use AAAA-MM-DD") from exc
