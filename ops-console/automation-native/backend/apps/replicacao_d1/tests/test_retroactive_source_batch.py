# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone

from apps.replicacao_d1.models import ReplicacaoD1Cliente, ReplicacaoD1FonteLote, ReplicacaoD1Workflow
from apps.replicacao_d1.services.source_batch import (
    source_rows_by_day_range_from_rotina,
    source_rows_range_from_rotina,
)
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord


class RetroactiveSourceBatchTests(TestCase):
    def setUp(self):
        self.cliente = ReplicacaoD1Cliente.objects.create(nome="Cliente Retro", ativo=True)
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Retro",
            nome_d1="WF Retro D1",
            nome_selenium="WF Retro Sel",
            cliente=self.cliente,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        tz = timezone.get_current_timezone()
        RotinaDetalhadoBrutoRecord.objects.bulk_create([
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 7),
                protocolo=100,
                workflow="WF Retro D1",
                data_analise=datetime(2026, 8, 7, 9, 0, tzinfo=tz),
                matricula="A12345C",
            ),
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 8),
                protocolo=100,
                workflow="WF Retro D1",
                data_analise=datetime(2026, 8, 8, 10, 0, tzinfo=tz),
                matricula="A12345C",
            ),
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 8),
                protocolo=200,
                workflow="WF Outro",
                data_analise=datetime(2026, 8, 8, 11, 0, tzinfo=tz),
                matricula="robot",
            ),
        ])

    def test_range_dedup_keeps_latest_record(self):
        rows = source_rows_range_from_rotina(date(2026, 8, 7), date(2026, 8, 8))
        self.assertEqual(len(rows), 2)
        proto100 = next(row for row in rows if row["protocolo"] == "100")
        self.assertEqual(proto100["data_analise"].date(), date(2026, 8, 8))

    def test_range_filters_by_cliente_chaves(self):
        rows = source_rows_range_from_rotina(
            date(2026, 8, 7),
            date(2026, 8, 8),
            cliente_chaves={self.cliente.chave_normalizada},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["protocolo"], "100")

    def test_invalid_range_raises(self):
        with self.assertRaises(ValueError):
            source_rows_range_from_rotina(date(2026, 8, 9), date(2026, 8, 7))

    def test_same_protocol_on_different_days_preserved_via_day_loader(self):
        from apps.replicacao_d1.services.source_batch import source_rows_for_day_from_rotina

        rows_d7 = source_rows_for_day_from_rotina(date(2026, 8, 7))
        rows_d8 = source_rows_for_day_from_rotina(date(2026, 8, 8))
        proto100_d7 = [row for row in rows_d7 if row["protocolo"] == "100"]
        proto100_d8 = [row for row in rows_d8 if row["protocolo"] == "100"]
        self.assertEqual(len(proto100_d7), 1)
        self.assertEqual(len(proto100_d8), 1)
        self.assertEqual(proto100_d7[0]["data_analise"].date(), date(2026, 8, 7))
        self.assertEqual(proto100_d8[0]["data_analise"].date(), date(2026, 8, 8))

    def test_grouped_range_preserves_same_protocol_on_different_days_in_one_query(self):
        with self.assertNumQueries(1):
            rows_by_day = source_rows_by_day_range_from_rotina(
                date(2026, 8, 7),
                date(2026, 8, 8),
            )

        self.assertEqual([row["protocolo"] for row in rows_by_day[date(2026, 8, 7)]], ["100"])
        self.assertEqual(
            {row["protocolo"] for row in rows_by_day[date(2026, 8, 8)]},
            {"100", "200"},
        )

    def test_grouped_range_filters_workflow_without_query_per_day(self):
        with self.assertNumQueries(1):
            rows_by_day = source_rows_by_day_range_from_rotina(
                date(2026, 8, 7),
                date(2026, 8, 8),
                workflow_names={"WF Retro D1"},
            )

        self.assertEqual(set(rows_by_day), {date(2026, 8, 7), date(2026, 8, 8)})
        self.assertTrue(all(len(rows) == 1 for rows in rows_by_day.values()))

    def test_range_filters_by_workflow_with_unicode_normalization(self):
        tz = timezone.get_current_timezone()
        RotinaDetalhadoBrutoRecord.objects.create(
            report_date=date(2026, 8, 9),
            protocolo=300,
            workflow="AMX \u2013 PRE VENDA",
            data_analise=datetime(2026, 8, 9, 8, 0, tzinfo=tz),
            matricula="A12345C",
        )
        rows = source_rows_range_from_rotina(
            date(2026, 8, 9),
            date(2026, 8, 9),
            workflow_names={"AMX - PRE VENDA"},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["protocolo"], "300")


class SourceBatchDayHelperTests(TestCase):
    def setUp(self):
        tz = timezone.get_current_timezone()
        RotinaDetalhadoBrutoRecord.objects.bulk_create([
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 10),
                protocolo=300,
                workflow="WF Dia",
                data_analise=datetime(2026, 8, 10, 8, 0, tzinfo=tz),
                matricula="A12345C",
            ),
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 11),
                protocolo=400,
                workflow="WF Outro",
                data_analise=datetime(2026, 8, 11, 9, 0, tzinfo=tz),
                matricula="robot",
            ),
        ])

    def test_rotina_day_has_records_empty_day(self):
        from apps.replicacao_d1.services.source_batch import rotina_day_has_records

        self.assertFalse(rotina_day_has_records(date(2026, 8, 9)))
        self.assertTrue(rotina_day_has_records(date(2026, 8, 10)))

    def test_rotina_day_has_records_filters_workflow(self):
        from apps.replicacao_d1.services.source_batch import rotina_day_has_records

        self.assertTrue(rotina_day_has_records(date(2026, 8, 10), workflow_names={"WF Dia"}))
        self.assertFalse(rotina_day_has_records(date(2026, 8, 10), workflow_names={"WF Ausente"}))

    def test_source_rows_for_day_from_rotina(self):
        from apps.replicacao_d1.services.source_batch import source_rows_for_day_from_rotina

        rows = source_rows_for_day_from_rotina(date(2026, 8, 10))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["protocolo"], "300")

    def test_source_batch_from_parquet_rows_creates_auditavel_lote(self):
        from apps.replicacao_d1.services.source_batch import source_batch_from_parquet_rows

        report_date = date(2026, 8, 12)
        rows = [
            {
                "Protocolo": "501",
                "Workflow": "WF Parquet",
                "Data de Análise": "12/08/2026 10:00:00",
                "Matrícula": "A12345C",
            }
        ]
        result = source_batch_from_parquet_rows(report_date, rows)
        self.assertTrue(result.created)
        self.assertEqual(result.batch.report_date, report_date)
        self.assertEqual(result.batch.status, ReplicacaoD1FonteLote.STATUS_READY)
        self.assertEqual(result.batch.registros.count(), 1)
        self.assertEqual(result.batch.registros.first().protocolo, "501")
