import json

from django.core.management.base import BaseCommand, CommandError

from apps.produtividade_case.services.reconciliation import reconcile_case_manager


class Command(BaseCommand):
    help = "Reconcilia snapshots do Case Manager sem alterar dados e sem emitir PII."

    def add_arguments(self, parser):
        parser.add_argument("--periodo-mes", default=None)
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, **options):
        result = reconcile_case_manager(periodo_mes=options["periodo_mes"])
        output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        self.stdout.write(output)
        if options["strict"] and not result["ok"]:
            raise CommandError("Reconciliação do Case Manager encontrou divergências.")
