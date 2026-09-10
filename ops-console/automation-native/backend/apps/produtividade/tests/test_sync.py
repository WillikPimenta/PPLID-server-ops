# -*- coding: utf-8 -*-
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from django.test import TestCase

from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.excel_reader import read_productivity_excel
from apps.produtividade.services.sync import sync_productivity_to_db
from apps.workforce.models import Agent


class ExcelReaderTests(unittest.TestCase):
    def test_reads_legacy_columns(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "relatorio_produtividade_detalhado_2026-06-17.xlsx"
            df = pd.DataFrame(
                [
                    {
                        "Matrícula": "C92928A",
                        "Etapa": "Análise Visual",
                        "Soma de tempo de análise em segundos": 3600,
                        "Meta da etapa": 7200,
                        "Data e Hora": datetime(2026, 6, 17, 10, 0, 0),
                    }
                ]
            )
            df.to_excel(path, index=False)
            rows = read_productivity_excel(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].matricula_norm, "c92928a")
            self.assertEqual(rows[0].analysis_seconds, 3600)

    def test_reads_brflow_columns(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "relatorio_produtividade_detalhado_2026-06-17.xlsx"
            df = pd.DataFrame(
                [
                    {
                        "Matrícula": "c97960a",
                        "Data de Análise": datetime(2026, 6, 17),
                        "Hora": 14,
                        "Workflow": "Reclassificação",
                        "Etapa": "Reclassificação",
                        "Tempo Total": 2693,
                        "Total de Análise": 146,
                        "Media_Tempo_por_Analise": 18,
                        "Meta": 900,
                        "Percentual": 16.22,
                    }
                ]
            )
            df.to_excel(path, index=False)
            rows = read_productivity_excel(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].matricula_norm, "c97960a")
            self.assertEqual(rows[0].analysis_seconds, 2693)
            self.assertEqual(rows[0].analysis_count, 146)
            self.assertEqual(rows[0].stage_goal, Decimal("900"))
            self.assertEqual(rows[0].recorded_at.hour, 14)


class SyncTests(TestCase):
    def test_sync_truncates_and_reloads(self):
        Agent.objects.create(full_name="Test Agent", user_lan_id="c92928a")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "relatorio_produtividade_detalhado_2026-06-17.xlsx"
            df = pd.DataFrame(
                [
                    {
                        "Matrícula": "c92928a",
                        "Data de Análise": datetime(2026, 6, 17),
                        "Hora": 9,
                        "Etapa": "Análise Visual",
                        "Tempo Total": 1800,
                        "Total de Análise": 90,
                        "Meta": 360,
                    },
                    {
                        "Matrícula": "c92928a",
                        "Data de Análise": datetime(2026, 6, 17),
                        "Hora": 10,
                        "Etapa": "Análise Visual",
                        "Tempo Total": 900,
                        "Total de Análise": 45,
                        "Meta": 360,
                    },
                ]
            )
            df.to_excel(path, index=False)

            success, _, count = sync_productivity_to_db(path=path, force=True)
            self.assertTrue(success)
            self.assertEqual(count, 2)
            record = ProductivityRecord.objects.filter(analysis_count=90).first()
            assert record is not None
            self.assertEqual(record.agent_name, "Test Agent")

            df2 = pd.DataFrame(
                [
                    {
                        "Matrícula": "c92928a",
                        "Data de Análise": datetime(2026, 6, 17),
                        "Hora": 11,
                        "Etapa": "Sobreposição",
                        "Tempo Total": 100,
                        "Total de Análise": 10,
                        "Meta": 200,
                    }
                ]
            )
            df2.to_excel(path, index=False)
            sync_productivity_to_db(path=path, force=True)
            self.assertEqual(ProductivityRecord.objects.count(), 1)
            self.assertEqual(ProductivityRecord.objects.first().etapa, "Sobreposição")

    def test_sync_overrides_excel_meta_with_meta_etapa(self):
        from apps.dimensoes_processos.models import DimEtapa, MetaEtapa

        Agent.objects.create(full_name="Test Agent", user_lan_id="c92928a")
        etapa = DimEtapa.objects.create(id_etapa=501, nome="Análise Visual")
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=etapa,
            meta_dia=Decimal("555"),
            servico=None,
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "relatorio_produtividade_detalhado_2026-06-17.xlsx"
            df = pd.DataFrame(
                [
                    {
                        "Matrícula": "c92928a",
                        "Data de Análise": datetime(2026, 6, 17),
                        "Hora": 10,
                        "Etapa": "Análise Visual",
                        "Tempo Total": 100,
                        "Total de Análise": 10,
                        "Meta": 360,
                    }
                ]
            )
            df.to_excel(path, index=False)
            sync_productivity_to_db(path=path, force=True)
            record = ProductivityRecord.objects.get()
            self.assertEqual(record.stage_goal, Decimal("555"))

    def test_sync_null_stage_goal_without_megazord(self):
        Agent.objects.create(full_name="Test Agent", user_lan_id="c92928a")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "relatorio_produtividade_detalhado_2026-06-17.xlsx"
            df = pd.DataFrame(
                [
                    {
                        "Matrícula": "c92928a",
                        "Data de Análise": datetime(2026, 6, 17),
                        "Hora": 10,
                        "Etapa": "Etapa Sem Cadastro",
                        "Tempo Total": 100,
                        "Total de Análise": 10,
                        "Meta": 360,
                    }
                ]
            )
            df.to_excel(path, index=False)
            sync_productivity_to_db(path=path, force=True)
            record = ProductivityRecord.objects.get()
            self.assertIsNone(record.stage_goal)

    def test_sync_uses_historical_meta_etapa(self):
        from apps.dimensoes_processos.models import DimEtapa, MetaEtapa

        Agent.objects.create(full_name="Test Agent", user_lan_id="c92928a")
        etapa = DimEtapa.objects.create(id_etapa=502, nome="Reclassificação")
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=date(2026, 5, 31),
            etapa=etapa,
            meta_dia=Decimal("111"),
            servico=None,
        )
        MetaEtapa.objects.create(
            data_inicio=date(2026, 6, 1),
            data_fim=None,
            etapa=etapa,
            meta_dia=Decimal("999"),
            servico=None,
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "relatorio_produtividade_detalhado_2026-03-10.xlsx"
            df = pd.DataFrame(
                [
                    {
                        "Matrícula": "c92928a",
                        "Data de Análise": datetime(2026, 3, 10),
                        "Hora": 10,
                        "Etapa": "Reclassificação",
                        "Tempo Total": 50,
                        "Total de Análise": 5,
                        "Meta": 50,
                    }
                ]
            )
            df.to_excel(path, index=False)
            sync_productivity_to_db(path=path, force=True)
            record = ProductivityRecord.objects.get()
            self.assertEqual(record.stage_goal, Decimal("111"))
