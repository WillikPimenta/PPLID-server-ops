# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.brb_report.models import ReportNaDemanda, ReportNaFalha, ReportTreinamento
from apps.brb_report.services.supplement_db_loaders import load_supplement_from_db
from apps.brb_report.services.supplement_import import import_supplement_workbook
from apps.dimensoes_processos.models import DimCliente

User = get_user_model()
FIXTURE = Path(__file__).resolve().parents[3] / "report_brb" / "tests" / "fixtures" / "BRB_Report_atualizado.xlsx"


@override_settings(BRB_REPORT_USE_EO_DB=True)
class SupplementImportTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=35, nome="BRB BANCO DE BRASILIA")

    def test_import_upsert_no_duplicate(self):
        if not FIXTURE.is_file():
            self.skipTest(f"Fixture ausente: {FIXTURE}")
        stats1 = import_supplement_workbook(FIXTURE, source_filename="t1.xlsx")
        count_demanda = ReportNaDemanda.objects.filter(id_cliente=35).count()
        count_falha = ReportNaFalha.objects.filter(id_cliente=35).count()
        self.assertGreater(stats1["upserted"], 0)
        self.assertGreater(stats1["na_demanda"], 0)
        self.assertGreater(count_demanda, 0)

        stats2 = import_supplement_workbook(FIXTURE, source_filename="t2.xlsx")
        self.assertGreater(stats2["upserted"], 0)
        self.assertEqual(ReportNaDemanda.objects.filter(id_cliente=35).count(), count_demanda)
        self.assertEqual(ReportNaFalha.objects.filter(id_cliente=35).count(), count_falha)

    def test_load_from_db_after_import(self):
        if not FIXTURE.is_file():
            self.skipTest(f"Fixture ausente: {FIXTURE}")
        import_supplement_workbook(FIXTURE, source_filename="t.xlsx")
        frames = load_supplement_from_db("brb", inicio=date(2026, 1, 1), fim=date(2026, 12, 31))
        self.assertIsNotNone(frames)
        self.assertGreater(len(frames["na_demandas"]), 0)
        self.assertTrue(ReportTreinamento.objects.filter(id_cliente=35).exists())
