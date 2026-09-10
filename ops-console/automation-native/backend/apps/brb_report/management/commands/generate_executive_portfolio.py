# -*- coding: utf-8 -*-
"""Gera HTML executivo multi-cliente para CS (e-mail / anexo)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.brb_report.services.executive_portfolio import build_executive_portfolio
from report_brb.brb_render_executive import write_executive_portfolio_html


class Command(BaseCommand):
    help = (
        "Gera portfolio_executivo.html com KPIs consolidados (EO). "
        "Use --inicio/--fim para definir o recorte; ideal para anexar no e-mail ao CS."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="",
            help="Pasta de destino (padrao: ~/Downloads/report).",
        )
        parser.add_argument(
            "--output",
            default="",
            help="Arquivo HTML de saida (sobrescreve --output-dir).",
        )
        parser.add_argument(
            "--inicio",
            default="",
            help="Data inicial YYYY-MM-DD ou DD/MM/YYYY.",
        )
        parser.add_argument(
            "--fim",
            default="",
            help="Data final YYYY-MM-DD ou DD/MM/YYYY (padrao: hoje).",
        )
        parser.add_argument(
            "--mtd",
            action="store_true",
            help="Atalho: mes corrente (dia 1 ate hoje).",
        )
        parser.add_argument(
            "--ytd",
            action="store_true",
            help="Atalho: ano corrente (01/01 ate hoje). Ignorado se --inicio informado.",
        )
        parser.add_argument(
            "--client",
            action="append",
            dest="clients",
            default=None,
            help="Slug de cliente (repita para limitar o universo).",
        )
        parser.add_argument(
            "--top",
            type=int,
            default=10,
            help="Quantidade no ranking destacado (padrao: 10).",
        )
        parser.add_argument(
            "--workbook",
            default="",
            help="Planilha .xlsx com aba Contestacao para complementar contestados ausentes na EO.",
        )

    def handle(self, *args, **options):
        today = date.today()
        inicio = self._resolve_inicio(options, today)
        fim = self._parse_date(options["fim"]) or today
        if inicio > fim:
            raise CommandError("A data inicial nao pode ser posterior a data final.")

        out_path = self._resolve_output_path(options, inicio, fim)
        workbook_path = Path(options["workbook"]) if options.get("workbook") else None
        if workbook_path:
            if not workbook_path.is_file():
                raise CommandError(f"Planilha nao encontrada: {workbook_path}")
            from report_brb.brb_loaders import validate_portfolio_contestacao_workbook

            try:
                validate_portfolio_contestacao_workbook(workbook_path)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"Montando portfolio executivo ({inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')})..."
        )
        payload = build_executive_portfolio(
            inicio=inicio,
            fim=fim,
            client_slugs=options["clients"],
            top_n=max(1, int(options["top"])),
            workbook_path=workbook_path,
        )
        write_executive_portfolio_html(payload, out_path)

        totals = payload["totals"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Salvo: {out_path}\n"
                f"  Recorte: {payload.get('periodo')}\n"
                f"  Clientes: {totals['clientes_ok']} | "
                f"Confirmadas: {totals['falhas_confirmadas']} | "
                f"Achados: {totals['achados_fg']}\n"
                f"  Anexe o HTML no e-mail ao CS ou abra no navegador antes de enviar."
            )
        )

    def _resolve_inicio(self, options, today: date) -> date:
        explicit = self._parse_date(options["inicio"])
        if explicit:
            return explicit
        if options["mtd"]:
            return date(today.year, today.month, 1)
        if options["ytd"] or not options["inicio"]:
            return date(today.year, 1, 1)
        return date(today.year, 1, 1)

    def _resolve_output_path(self, options, inicio: date, fim: date) -> Path:
        if options["output"]:
            return Path(options["output"])
        out_dir = (
            Path(options["output_dir"])
            if options["output_dir"]
            else Path.home() / "Downloads" / "report"
        )
        suffix = f"_{inicio.isoformat()}_{fim.isoformat()}"
        return out_dir / f"portfolio_executivo{suffix}.html"

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        text = (raw or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise CommandError(f"Data invalida: {raw!r}. Use YYYY-MM-DD ou DD/MM/YYYY.")
