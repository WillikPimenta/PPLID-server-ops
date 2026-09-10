# -*- coding: utf-8 -*-
"""Gera Quality Pulse para todos os clientes e salva HTML na pasta local."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.brb_report.services.report_service import generate_reports_batch


class Command(BaseCommand):
    help = (
        "Gera relatórios (Quality Pulse) para todos os clientes ativos com dados na EO "
        "(enabled + auditados/falhas) e copia os HTML para ~/Downloads/report."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="",
            help="Pasta de destino (padrão: Downloads/report do usuário).",
        )
        parser.add_argument(
            "--inicio",
            default="",
            help="Data inicial YYYY-MM-DD (padrão: 01/01 do ano corrente).",
        )
        parser.add_argument(
            "--fim",
            default="",
            help="Data final YYYY-MM-DD (padrão: hoje).",
        )
        parser.add_argument(
            "--client",
            action="append",
            dest="clients",
            default=None,
            help="Slug de cliente (repita para vários). Omita para todos com EO.",
        )
        parser.add_argument(
            "--supplement",
            default="",
            help="Caminho opcional do Excel suplemento (NA/Treinamentos).",
        )
        parser.add_argument(
            "--html-only",
            action="store_true",
            help="Copia somente os HTML (Quality Pulse / dashboard).",
        )
        parser.add_argument(
            "--with-excel",
            action="store_true",
            help="Inclui também Excel cliente na pasta de saída.",
        )
        parser.add_argument(
            "--portfolio",
            action="store_true",
            help="Ao final, gera portfolio_executivo.html (top 10 + todos os clientes).",
        )

    def handle(self, *args, **options):
        today = date.today()
        inicio = self._parse_date(options["inicio"]) or date(today.year, 1, 1)
        fim = self._parse_date(options["fim"]) or today
        out_dir = Path(options["output_dir"]) if options["output_dir"] else Path.home() / "Downloads" / "report"
        supplement = Path(options["supplement"]) if options["supplement"] else None
        html_only = bool(options["html_only"])
        include_excel = bool(options["with_excel"]) and not html_only

        from apps.brb_report.services.client_catalog import list_scheduled_report_client_slugs

        targets = options["clients"] or list_scheduled_report_client_slugs(enabled_only=True)
        self.stdout.write(
            f"Gerando {len(targets)} cliente(s) ativo(s) "
            f"({inicio.isoformat()} a {fim.isoformat()}) -> {out_dir}"
        )

        results = generate_reports_batch(
            client_slugs=options["clients"],
            workbook_path=supplement if supplement and supplement.is_file() else None,
            inicio=inicio,
            fim=fim,
            enrich=False,
            include_pulse=True,
            include_excel_cliente=include_excel,
            include_excel_interno=False,
            include_dashboard=False,
            output_dir=out_dir,
            html_only=html_only,
        )

        ok = sum(1 for row in results if row.get("ok"))
        fail = len(results) - ok
        self.stdout.write(self.style.SUCCESS(f"Concluído: {ok} ok, {fail} falha(s)."))
        for row in results:
            slug = row.get("client_slug", "?")
            if row.get("ok"):
                exported = row.get("exported") or {}
                pulse = exported.get("quality_pulse") or row.get("artifacts", {}).get("quality_pulse")
                self.stdout.write(f"  OK  {slug}: {pulse or row.get('storage_key')}")
            else:
                self.stdout.write(self.style.ERROR(f"  FAIL {slug}: {row.get('error')}"))

        if options["portfolio"]:
            from apps.brb_report.services.executive_portfolio import build_executive_portfolio
            from report_brb.brb_render_executive import write_executive_portfolio_html

            self.stdout.write("Gerando portfolio executivo...")
            portfolio = build_executive_portfolio(inicio=inicio, fim=fim, client_slugs=targets)
            portfolio_path = out_dir / "portfolio_executivo.html"
            write_executive_portfolio_html(portfolio, portfolio_path)
            self.stdout.write(self.style.SUCCESS(f"Portfolio: {portfolio_path}"))

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        text = (raw or "").strip()
        if not text:
            return None
        return datetime.strptime(text, "%Y-%m-%d").date()
