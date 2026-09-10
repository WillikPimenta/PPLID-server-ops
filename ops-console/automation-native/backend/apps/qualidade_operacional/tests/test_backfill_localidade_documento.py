# -*- coding: utf-8 -*-
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha


class BackfillLocalidadeDocumentoTests(TestCase):
    def test_dry_run_reports_and_apply_persists(self):
        falha = QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            protocolo="F-UF",
            matricula="c90001a",
            tipo_falha="Manual",
            localidade="Brasília",
            uf="RJ",
            source_file="test",
        )
        auditado = QualidadeAuditado.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            protocolo="F-UF",
            matricula="c90001a",
            tipo_analise="Auditoria",
            source_file="test",
        )

        out = StringIO()
        call_command("backfill_localidade_documento", stdout=out)
        self.assertIn("dry-run", out.getvalue().lower())
        falha.refresh_from_db()
        auditado.refresh_from_db()
        self.assertEqual(falha.localidade_documento, "")
        self.assertEqual(auditado.localidade_documento, "")

        call_command("backfill_localidade_documento", "--apply", stdout=out)
        falha.refresh_from_db()
        auditado.refresh_from_db()
        self.assertEqual(falha.localidade_documento, "RJ")
        self.assertEqual(auditado.localidade_documento, "RJ")
