# -*- coding: utf-8 -*-
"""Orquestra carga inicial: dimensões → FY26 parquet → sync produtivo (se bruto existir)."""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.dimensoes_processos.models import DimCliente, ProjecaoSla
from apps.dimensoes_processos.services.importer import DEFAULT_XLSX, import_identificacao_processos
from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilDetalhe, SlaUtilSyncRun
from apps.monitoramento_sla.services.import_consolidado_parquet import (
    import_consolidado_parquet,
    kpi_from_db,
)
from apps.monitoramento_sla.services.sync_incremental import sync_monitoramento_sla
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord


def _default_fy26_path() -> Path:
    return Path(__file__).resolve().parents[5] / "monitoramento_sla" / "FY26.parquet"


class Command(BaseCommand):
    help = (
        "Bootstrap Monitoramento SLA: importa dim/projeção, FY26.parquet (consolidado) "
        "e sync produtivo se rotina_detalhado_bruto tiver dados."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--parquet",
            default="",
            help="Caminho do consolidado parquet (default: monitoramento_sla/FY26.parquet)",
        )
        parser.add_argument(
            "--clear-dim",
            action="store_true",
            help="Apaga dimensões antes do import (cuidado: pode falhar por FK em produtividade).",
        )
        parser.add_argument(
            "--skip-dim",
            action="store_true",
            help="Não roda import_identificacao_processos (dim/proj já carregadas).",
        )
        parser.add_argument(
            "--skip-parquet",
            action="store_true",
            help="Não importa FY26.parquet.",
        )
        parser.add_argument(
            "--skip-sync",
            action="store_true",
            help="Não roda sync_monitoramento_sla.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Janela do sync produtivo (default: 90).",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Não truncar sla_util_consolidado antes do import parquet.",
        )
        parser.add_argument(
            "--dim-path",
            default="",
            help=f"Caminho do xlsx de dimensões (default: {DEFAULT_XLSX}).",
        )

    def handle(self, *args, **options):
        if not options["skip_dim"]:
            dim_path = options["dim_path"] or str(DEFAULT_XLSX)
            if not Path(dim_path).is_file():
                raise CommandError(f"Workbook de dimensões não encontrado: {dim_path}")
            self.stdout.write(f"Importando dimensões/projeção: {dim_path}")
            counts = import_identificacao_processos(dim_path, clear=options["clear_dim"])
            for key, value in counts.items():
                self.stdout.write(f"  {key}: {value}")
        else:
            self.stdout.write(
                f"Dimensões (skip): clientes={DimCliente.objects.count()} "
                f"projecao_sla={ProjecaoSla.objects.count()}"
            )

        if not options["skip_parquet"]:
            raw = (options.get("parquet") or "").strip()
            parquet_path = Path(raw) if raw else _default_fy26_path()
            if not parquet_path.is_file():
                raise CommandError(f"Parquet não encontrado: {parquet_path}")
            self.stdout.write(f"Importando consolidado: {parquet_path}")
            run, report = import_consolidado_parquet(
                parquet_path,
                replace=not options["keep"],
            )
            style = self.style.SUCCESS if report.get("kpis_match") else self.style.WARNING
            self.stdout.write(style(run.message))
            self.stdout.write(f"  KPIs match: {report.get('kpis_match')}")

        if not options["skip_sync"]:
            bruto_count = RotinaDetalhadoBrutoRecord.objects.count()
            if bruto_count == 0:
                self.stdout.write(
                    self.style.WARNING(
                        "rotina_detalhado_bruto_record vazio — sync produtivo ignorado."
                    )
                )
            else:
                self.stdout.write(
                    f"Sync produtivo ({options['days']} dias) a partir de {bruto_count} registros bruto…"
                )
                run = sync_monitoramento_sla(days=options["days"])
                if run.status == run.STATUS_OK:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"sync status={run.status} detalhe={run.rows_detalhe} "
                            f"consolidado={run.rows_consolidado}"
                        )
                    )
                else:
                    raise CommandError(f"Sync falhou: {run.message}")

        cons = SlaUtilConsolidado.objects.count()
        det = SlaUtilDetalhe.objects.count()
        abertos = SlaUtilDetalhe.objects.filter(em_aberto=True).count()
        last = SlaUtilSyncRun.objects.order_by("-started_at").first()
        self.stdout.write(
            self.style.SUCCESS(
                f"Bootstrap concluído — consolidado={cons} detalhe={det} abertos={abertos}"
            )
        )
        if last:
            self.stdout.write(f"  último run: id={last.pk} status={last.status}")
        if cons:
            self.stdout.write(f"  KPIs DB: {kpi_from_db()}")
