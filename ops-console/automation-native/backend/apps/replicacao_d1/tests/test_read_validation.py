# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from django.test import TestCase, override_settings

from apps.replicacao_d1.models import ReplicacaoD1Protocolo, ReplicacaoD1Replicado
from apps.replicacao_d1.services.excel_reader import read_replicacao_d1_excel
from apps.replicacao_d1.services.read_result import should_replace_partition
from apps.replicacao_d1.services.replicados_reader import read_replicados_csv
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db
from apps.replicacao_d1.services.sync_replicados import sync_replicados_to_db
from apps.replicacao_d1.tests.test_replicados_sync import _write_replicados_csv
from apps.replicacao_d1.tests.test_sync import _write_fixture_excel


class ReadValidationTests(TestCase):
    def setUp(self):
        from django.conf import settings

        self.dir = Path(settings.BASE_DIR) / "tmp" / "replicacao_d1_read_validation"
        self.dir.mkdir(parents=True, exist_ok=True)

    def test_plano_counts_invalid_empty_and_duplicate_rows(self):
        path = self.dir / "replicacao_aud_d1_relatorio_20260717_093000.xlsx"
        df_resumo = pd.DataFrame(
            [
                {
                    "Workflow": "WF G",
                    "Workflow D1": "wf_g_d1",
                    "Status BRFlow": "SALVO_OK",
                    "RunId": "20260717_093000",
                    "Data Referencia D1": "16/07/2026",
                    "Data Execucao": "17/07/2026 12:00",
                }
            ]
        )
        df_plano = pd.DataFrame(
            [
                {"Protocolo": "P001", "WorkflowConfig": "WF G", "Hora": 10},
                {"Protocolo": "", "WorkflowConfig": "WF G"},
                {"Protocolo": "P001", "WorkflowConfig": "WF G", "Hora": 11},
                {"Protocolo": "0001", "WorkflowConfig": "WF G", "Hora": 9},
                {"Protocolo": "1", "WorkflowConfig": "WF G", "Hora": 8},
                {"Protocolo": "P002", "WorkflowConfig": "", "Hora": 12},
                {"Protocolo": "P003", "WorkflowConfig": "WF G", "Hora": 25},
            ]
        )
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df_plano.to_excel(writer, sheet_name="Plano", index=False)
            df_resumo.to_excel(writer, sheet_name="Resumo", index=False)

        parsed = read_replicacao_d1_excel(path)
        stats = parsed.read_stats
        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats.source_rows, 7)
        self.assertEqual(stats.valid_rows, 2)
        self.assertEqual(stats.rejected_rows, 3)
        self.assertEqual(stats.duplicate_rows, 2)
        self.assertTrue(stats.accounting_ok)
        self.assertEqual(len(parsed.protocolos), 2)

    def test_replicados_counts_and_normalized_dedup(self):
        csv_path = self.dir / "brflow-replicadosd1-tratado_20260716.csv"
        csv_path.write_text(
            "Cliente Destino;Protocolo Origem;Workflow Origem\n"
            "GAQ;P001;WF\n"
            "GAQ;;WF\n"
            "GAQ;P001;WF\n"
            "GAQ;0002;WF\n"
            "GAQ;2;WF\n",
            encoding="utf-8-sig",
        )
        report_date, result = read_replicados_csv(csv_path)
        self.assertEqual(report_date, date(2026, 7, 16))
        self.assertEqual(result.source_rows, 5)
        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.rejected_rows, 1)
        self.assertEqual(result.duplicate_rows, 2)
        self.assertTrue(result.accounting_ok)

    def test_empty_csv_does_not_wipe_existing_replicados_partition(self):
        csv_path = _write_replicados_csv(self.dir / "brflow-replicadosd1-tratado_20260716.csv")
        ok, _, count, _ = sync_replicados_to_db(path=str(csv_path), force=True)
        self.assertTrue(ok)
        self.assertEqual(count, 2)
        self.assertEqual(ReplicacaoD1Replicado.objects.count(), 2)

        csv_path.write_text(
            "Cliente Destino;Protocolo Origem;Workflow Origem\n",
            encoding="utf-8-sig",
        )
        ok2, _, count2, _ = sync_replicados_to_db(path=str(csv_path), force=True)
        self.assertFalse(ok2)
        self.assertEqual(count2, 0)
        self.assertEqual(ReplicacaoD1Replicado.objects.count(), 2)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_empty_plano_does_not_wipe_existing_protocol_partition(self):
        excel_path = _write_fixture_excel(
            self.dir / "replicacao_aud_d1_relatorio_20260717_093000.xlsx"
        )
        ok, _, count, run_id, _ = sync_replicacao_d1_to_db(path=str(excel_path), force=True)
        self.assertTrue(ok)
        self.assertEqual(ReplicacaoD1Protocolo.objects.filter(run_id=run_id).count(), 2)
        self.assertGreater(count, 0)

        df_resumo = pd.read_excel(excel_path, sheet_name="Resumo", engine="openpyxl")
        df_dash = pd.read_excel(excel_path, sheet_name="Dashboard", engine="openpyxl")
        df_plano = pd.DataFrame(columns=["Protocolo", "WorkflowConfig", "Hora"])
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            df_plano.to_excel(writer, sheet_name="Plano", index=False)
            df_resumo.to_excel(writer, sheet_name="Resumo", index=False)
            df_dash.to_excel(writer, sheet_name="Dashboard", index=False)

        ok2, _, _, run_id2, _ = sync_replicacao_d1_to_db(path=str(excel_path), force=True)
        self.assertTrue(ok2)
        self.assertEqual(run_id2, run_id)
        self.assertEqual(ReplicacaoD1Protocolo.objects.filter(run_id=run_id).count(), 2)

    def test_should_replace_partition_rules(self):
        self.assertFalse(
            should_replace_partition(valid_rows=0, existing_count=5, structurally_valid=True)
        )
        self.assertFalse(
            should_replace_partition(valid_rows=10, existing_count=5, structurally_valid=False)
        )
        self.assertTrue(
            should_replace_partition(valid_rows=10, existing_count=5, structurally_valid=True)
        )
        self.assertTrue(
            should_replace_partition(valid_rows=0, existing_count=0, structurally_valid=True)
        )
