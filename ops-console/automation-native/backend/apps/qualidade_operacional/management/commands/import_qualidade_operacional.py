# -*- coding: utf-8 -*-
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.qualidade_operacional.services.importer import (
    DEFAULT_DATA_DIR,
    import_qualidade_operacional,
)


class Command(BaseCommand):
    help = (
        "Importa TSV de Qualidade Operacional de data/qualidade/: "
        "tabela_auditado_com_tipo_de_conclusao_{1,2,3}.tsv + tabela_falhas.tsv. "
        "Após alterar schema/fonte, use --mode replace."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            type=str,
            default=str(DEFAULT_DATA_DIR),
            help="Diretório com os TSV (default: <repo>/data/qualidade).",
        )
        parser.add_argument(
            "--mode",
            choices=("upsert", "replace"),
            default="upsert",
            help="upsert = recarrega por source_file; replace = truncate + load.",
        )
        parser.add_argument(
            "--only",
            choices=("auditados", "falhas"),
            default=None,
            help="Importa só uma das tabelas.",
        )

    def handle(self, *args, **options):
        data_dir = Path(options["data_dir"])
        self.stdout.write(f"Importando de {data_dir} (mode={options['mode']})…")
        stats = import_qualidade_operacional(
            data_dir,
            mode=options["mode"],
            only=options.get("only"),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"OK={stats.ok} skipped={stats.skipped} errors={stats.errors}"
            )
        )
        for name, bucket in stats.files.items():
            self.stdout.write(
                f"  {name}: ok={bucket['ok']} skipped={bucket['skipped']} "
                f"errors={bucket['errors']}"
            )
