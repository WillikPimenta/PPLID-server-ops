from django.core.management.base import BaseCommand, CommandError

from apps.dimensoes_processos.services.importer import DEFAULT_XLSX, import_identificacao_processos


class Command(BaseCommand):
    help = (
        "Importa dimensões e regras do workbook Identificação dos Processos "
        "(Clientes, Workflow, NH, Etapas, Metas, SLA, Equipes, etc.)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(DEFAULT_XLSX),
            help=f"Caminho do .xlsx (default: {DEFAULT_XLSX})",
        )
        parser.add_argument(
            "--mode",
            choices=["sync", "replace"],
            default="replace",
            help="sync = upsert sem apagar; replace = apaga e recarrega (legado).",
        )
        parser.add_argument(
            "--scope",
            choices=["cadastros", "projecao_sla", "full"],
            default="full",
            help="Escopo: cadastros | projecao_sla | full.",
        )
        parser.add_argument(
            "--no-clear",
            action="store_true",
            help="Deprecated: equivalente a --mode sync.",
        )

    def handle(self, *args, **options):
        path = options["path"]
        mode = "sync" if options["no_clear"] else options["mode"]
        scope = options["scope"]
        try:
            summary = import_identificacao_processos(path, mode=mode, import_scope=scope)
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(f"Falha no import: {exc}") from exc

        entities = summary.get("entities") or {}
        for key, stats in entities.items():
            if isinstance(stats, dict):
                self.stdout.write(
                    f"  {key}: created={stats.get('created', 0)} "
                    f"updated={stats.get('updated', 0)} skipped={stats.get('skipped', 0)}"
                )
            else:
                self.stdout.write(f"  {key}: {stats}")
        if summary.get("warnings"):
            self.stdout.write(self.style.WARNING(f"  warnings: {len(summary['warnings'])}"))
        if summary.get("capacity_snapshots_invalidated") is not None:
            self.stdout.write(
                f"  capacity_snapshots_invalidated={summary.get('capacity_snapshots_invalidated', 0)}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Import Identificação dos Processos concluído (mode={mode}, scope={scope})."
            )
        )
