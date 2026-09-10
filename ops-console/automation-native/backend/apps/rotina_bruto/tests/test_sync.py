# -*- coding: utf-8 -*-
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import pandas as pd
from django.test import TestCase

from apps.rotina_bruto.models import (
    RotinaBrutoSyncLog,
    RotinaConferBuscaRecord,
    RotinaDetalhadoBrutoAlerta,
    RotinaDetalhadoBrutoRecord,
    RotinaGedDetalhadoTratadoRecord,
    RotinaGedIrregularidadeTratadoRecord,
    RotinaMonitorTratadoRecord,
)
from apps.rotina_bruto.services.monitor_tratado_sync import sync_monitor_tratado_to_db
from apps.rotina_bruto.services.parquet_reader import (
    build_detalhado_records,
    build_ged_detalhado_records,
    build_ged_irregularidade_records,
    build_prod_records,
    iter_detalhado_chunks,
    iter_parquet_dataframe_batches,
)
from apps.rotina_bruto.services.pii import hash_cpf
from apps.rotina_bruto.services.source_path import SourceFileInfo
from apps.rotina_bruto.services.sync import sync_detalhado_bruto_to_db
from apps.rotina_bruto.services.sync_runner import run_sync_with_audit
from apps.rotina_bruto.services.tratado_sync import (
    sync_confer_busca_to_db,
    sync_ged_detalhado_to_db,
    sync_ged_irregularidade_to_db,
)


class DetalhadoReaderTests(unittest.TestCase):
    def test_build_detalhado_records_maps_expected_columns(self):
        df = pd.DataFrame(
            [
                {
                    "Protocolo": "123",
                    "Cliente": "Cliente A",
                    "Workflow": "WF",
                    "CPF": "12345678900",
                    "Data de Cadastro": "2026-06-18",
                    "Data de Conclusão": "2026-06-18",
                    "Status do Registro": "Concluído",
                    "Resultado": "OK",
                    "Nível Hierárquico": "N1",
                    "matrícula": "c92928a",
                    "Data da Primeira Conclusão": "2026-06-17",
                    "Data de Análise": "2026-06-18",
                    "Alertas": "Nenhum",
                }
            ]
        )
        records, alerta_texts = build_detalhado_records(df, date(2026, 6, 18))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].protocolo, 123)
        self.assertEqual(records[0].matricula, "c92928a")
        self.assertNotIn(
            "alertas",
            {f.name for f in RotinaDetalhadoBrutoRecord._meta.local_fields},
        )
        self.assertEqual(alerta_texts[0], "Nenhum")
        self.assertEqual(records[0].data_analise, date(2026, 6, 18))
        self.assertEqual(records[0].cpf, hash_cpf("12345678900"))
        self.assertNotEqual(records[0].cpf, "12345678900")

    def test_iter_detalhado_chunks_parses_br_dates_and_alertas(self):
        df = pd.DataFrame(
            [
                {
                    "Protocolo": "10",
                    "Cliente": "A",
                    "Workflow": "W",
                    "CPF": "11122233344",
                    "Data de Cadastro": "18/06/2026",
                    "Data de Conclusão": "19/06/2026",
                    "Status do Registro": "OK",
                    "Resultado": "R",
                    "Nível Hierárquico": "N1",
                    "matrícula": "m1",
                    "Data da Primeira Conclusão": "17/06/2026",
                    "Data de Análise": "18/06/2026",
                    "Alertas": "alerta-1",
                },
                {
                    "Protocolo": "20",
                    "Cliente": "B",
                    "Workflow": "W",
                    "CPF": "",
                    "Data de Cadastro": "",
                    "Alertas": "",
                },
            ]
        )
        chunks = list(iter_detalhado_chunks(df, date(2026, 6, 18), chunk_size=1))
        self.assertEqual(len(chunks), 2)
        first_records, first_alertas = chunks[0]
        self.assertEqual(len(first_records), 1)
        self.assertEqual(first_records[0].protocolo, 10)
        self.assertEqual(first_records[0].data_cadastro, date(2026, 6, 18))
        self.assertEqual(first_records[0].data_conclusao, date(2026, 6, 19))
        self.assertEqual(first_alertas[0], "alerta-1")
        second_records, second_alertas = chunks[1]
        self.assertIsNone(second_records[0].data_cadastro)
        self.assertEqual(second_alertas[0], "")


class SyncReplacePartitionTests(TestCase):
    def test_sync_twice_replaces_partition_not_duplicates(self):
        report_date = date(2026, 6, 18)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-detalhado-bruto_20260618.parquet"
            df1 = pd.DataFrame(
                [
                    {"Protocolo": "A1", "Cliente": "C1", "Workflow": "W1", "CPF": "1"},
                    {"Protocolo": "A2", "Cliente": "C2", "Workflow": "W2", "CPF": "2"},
                ]
            )
            df1.to_parquet(path, index=False)

            source = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_DETALHADO,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
            )
            count1 = sync_detalhado_bruto_to_db(source)
            self.assertEqual(count1, 2)
            self.assertEqual(RotinaDetalhadoBrutoRecord.objects.count(), 2)
            self.assertEqual(RotinaDetalhadoBrutoAlerta.objects.count(), 0)

            df2 = pd.DataFrame(
                [{"Protocolo": "999", "Cliente": "C3", "Workflow": "W3", "CPF": "3"}]
            )
            df2.to_parquet(path, index=False)
            source2 = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_DETALHADO,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
            )
            count2 = sync_detalhado_bruto_to_db(source2)
            self.assertEqual(count2, 1)
            self.assertEqual(RotinaDetalhadoBrutoRecord.objects.count(), 1)
            self.assertEqual(RotinaDetalhadoBrutoRecord.objects.first().protocolo, 999)

    def test_sync_stores_alertas_in_satellite_table(self):
        report_date = date(2026, 6, 19)
        long_alerta = "ALERTA " + ("x" * 5000)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-detalhado-bruto_20260619.parquet"
            df = pd.DataFrame(
                [
                    {
                        "Protocolo": "10",
                        "Cliente": "C",
                        "Workflow": "W",
                        "CPF": "1",
                        "Alertas": long_alerta,
                    },
                    {
                        "Protocolo": "11",
                        "Cliente": "C2",
                        "Workflow": "W2",
                        "CPF": "2",
                        "Alertas": "",
                    },
                ]
            )
            df.to_parquet(path, index=False)
            source = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_DETALHADO,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
            )
            self.assertEqual(sync_detalhado_bruto_to_db(source), 2)
            self.assertEqual(RotinaDetalhadoBrutoRecord.objects.count(), 2)
            self.assertEqual(RotinaDetalhadoBrutoAlerta.objects.count(), 1)
            record = RotinaDetalhadoBrutoRecord.objects.get(protocolo=10)
            self.assertEqual(record.alerta.alertas, long_alerta)
            self.assertFalse(
                RotinaDetalhadoBrutoAlerta.objects.filter(record__protocolo=11).exists()
            )

            # Replace partition cascades alertas
            df2 = pd.DataFrame(
                [{"Protocolo": "20", "Cliente": "C", "Workflow": "W", "CPF": "3", "Alertas": "novo"}]
            )
            df2.to_parquet(path, index=False)
            source2 = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_DETALHADO,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
            )
            self.assertEqual(sync_detalhado_bruto_to_db(source2), 1)
            self.assertEqual(RotinaDetalhadoBrutoRecord.objects.count(), 1)
            self.assertEqual(RotinaDetalhadoBrutoAlerta.objects.count(), 1)
            self.assertEqual(
                RotinaDetalhadoBrutoRecord.objects.get().alerta.alertas, "novo"
            )

    def test_run_sync_with_audit_force(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-detalhado-bruto_20260618.parquet"
            df = pd.DataFrame([{"Protocolo": "X1", "Cliente": "C", "Workflow": "W", "CPF": "9"}])
            df.to_parquet(path, index=False)

            success, log, skipped = run_sync_with_audit(
                report_type=RotinaBrutoSyncLog.REPORT_DETALHADO,
                path=str(path),
                force=True,
            )
            self.assertTrue(success)
            self.assertFalse(skipped)
            self.assertEqual(log.row_count, 1)
            self.assertEqual(RotinaDetalhadoBrutoRecord.objects.count(), 1)


class MonitorTratadoSyncTests(TestCase):
    def test_sync_monitor_tratado_replaces_partition(self):
        report_date = date(2026, 6, 18)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-monitor-tratado_20260618.parquet"
            df = pd.DataFrame(
                [
                    {
                        "Data": datetime(2026, 6, 18).date(),
                        "Hora": 10,
                        "Usuário": "c92928a",
                        "Data do Evento": datetime(2026, 6, 18, 10, 0, 0),
                        "Evento": "Autenticação com sucesso",
                        "Data segundo evento": datetime(2026, 6, 18, 12, 0, 0),
                        "Segundo evento": "Logout",
                    }
                ]
            )
            df.to_parquet(path, index=False)

            source = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_MONITOR,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
            )
            count = sync_monitor_tratado_to_db(source)
            self.assertEqual(count, 1)
            record = RotinaMonitorTratadoRecord.objects.get()
            self.assertEqual(record.report_date, report_date)
            self.assertEqual(record.matricula_usuario, "c92928a")
            self.assertEqual(record.segundo_evento, "Logout")
            self.assertEqual(record.hora, 10)


class TratadosSyncTests(TestCase):
    def test_sync_confer_busca_replaces_by_periodo(self):
        report_date = date(2025, 6, 1)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "confer-buscarpIrregularidade-tratado_202506.csv"
            df1 = pd.DataFrame(
                [
                    {
                        "Protocolo": "100",
                        "Status": "OK",
                        "Etapa": "Reclassificação",
                    }
                ]
            )
            df1.to_csv(path, sep=";", index=False, encoding="cp1252")

            source = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_CONFER_BUSCA,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
                periodo=None,
            )
            self.assertEqual(sync_confer_busca_to_db(source), 1)
            self.assertEqual(RotinaConferBuscaRecord.objects.count(), 1)

            df2 = pd.DataFrame(
                [
                    {"Protocolo": "200", "Status": "OK", "Etapa": "Reclassificação"},
                    {"Protocolo": "201", "Status": "OK", "Etapa": "Reclassificação"},
                ]
            )
            df2.to_csv(path, sep=";", index=False, encoding="cp1252")
            source2 = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_CONFER_BUSCA,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
                periodo=None,
            )
            self.assertEqual(sync_confer_busca_to_db(source2), 2)
            self.assertEqual(RotinaConferBuscaRecord.objects.count(), 2)
            self.assertEqual(
                set(RotinaConferBuscaRecord.objects.values_list("protocolo", flat=True)),
                {200, 201},
            )

    def test_sync_ged_irregularidade_hashes_cpf_and_replaces_periodo(self):
        report_date = date(2025, 6, 1)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ged-irregularidade-tratado_202506_1.csv"
            df = pd.DataFrame(
                [
                    {
                        "Protocolo": "999",
                        "CPF": "12345678900",
                        "Status Contrato": "Ativo",
                    }
                ]
            )
            df.to_csv(path, sep=";", index=False, encoding="cp1252")

            source = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
                periodo=1,
            )
            self.assertEqual(sync_ged_irregularidade_to_db(source), 1)
            record = RotinaGedIrregularidadeTratadoRecord.objects.get()
            self.assertEqual(record.periodo, 1)
            self.assertEqual(record.cpf, hash_cpf("12345678900"))
            self.assertNotEqual(record.cpf, "12345678900")

    def test_build_ged_irregularidade_records(self):
        df = pd.DataFrame([{"Protocolo": "1", "CPF": "11122233344"}])
        records = build_ged_irregularidade_records(df, date(2025, 6, 1), periodo=2)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].periodo, 2)
        self.assertEqual(records[0].cpf, hash_cpf("11122233344"))

    def test_build_ged_irregularidade_accepts_long_msisdn_list(self):
        # CSV real pode trazer vários MSISDNs concatenados (>64 chars).
        long_msisdn = ", ".join(f"6199{i:07d}" for i in range(12))
        self.assertGreater(len(long_msisdn), 64)
        df = pd.DataFrame(
            [{"Protocolo": "1", "CPF": "11122233344", "MSISDN": long_msisdn}]
        )
        records = build_ged_irregularidade_records(df, date(2025, 6, 1), periodo=1)
        self.assertEqual(records[0].msisdn, long_msisdn)
        records[0].report_date = date(2025, 6, 1)
        records[0].save()
        saved = RotinaGedIrregularidadeTratadoRecord.objects.get()
        self.assertEqual(saved.msisdn, long_msisdn)

    def test_build_prod_records_parses_dates_vectorized(self):
        df = pd.DataFrame(
            [
                {
                    "desMatricula": "c92928a",
                    "datAnalise": "18/06/2026",
                    "numTempoAnalise": "90",
                    "nomCliente": "Cli",
                    "nomWorkflow": "WF",
                    "nomEtapa": "Etapa",
                    "colExtra": "x",
                }
            ]
        )
        records = build_prod_records(df, date(2026, 6, 18))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].dat_analise, date(2026, 6, 18))
        self.assertEqual(records[0].num_tempo_analise, 90)
        self.assertEqual(records[0].extra.get("colExtra"), "x")

    def test_build_ged_detalhado_records_parses_br_dates(self):
        df = pd.DataFrame(
            [
                {
                    "Protocolo": "42",
                    "Data do Recebimento": "01/06/2025",
                    "Data da Venda": "2025-05-20",
                    "Data do Batimento": "",
                    "Tipo de Serviço Primário": "Fibra",
                }
            ]
        )
        records = build_ged_detalhado_records(df, date(2025, 6, 1), periodo=1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].protocolo, 42)
        self.assertEqual(records[0].data_recebimento, date(2025, 6, 1))
        self.assertEqual(records[0].data_venda, date(2025, 5, 20))
        self.assertIsNone(records[0].data_batimento)
        self.assertEqual(records[0].tipo_servico_primario, "Fibra")

    def test_iter_parquet_dataframe_batches_splits_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.parquet"
            df = pd.DataFrame(
                [{"Protocolo": str(i), "Tipo de Serviço Primário": "Fibra"} for i in range(5)]
            )
            df.to_parquet(path, index=False)
            batches = list(iter_parquet_dataframe_batches(path, batch_rows=2))
            self.assertEqual(len(batches), 3)
            self.assertEqual(sum(len(b) for b in batches), 5)


class GedDetalhadoSyncTests(TestCase):
    def test_sync_ged_detalhado_batches_replace_partition(self):
        report_date = date(2025, 6, 1)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ged-detalhado-tratado_202506.parquet"
            df = pd.DataFrame(
                [
                    {
                        "Protocolo": "10",
                        "Data do Recebimento": "01/06/2025",
                        "Tipo de Serviço Primário": "Fibra",
                    },
                    {
                        "Protocolo": "11",
                        "Data do Recebimento": "02/06/2025",
                        "Tipo de Serviço Primário": "Fibra",
                    },
                    {
                        "Protocolo": "12",
                        "Data do Recebimento": "03/06/2025",
                        "Tipo de Serviço Primário": "Fibra",
                    },
                ]
            )
            df.to_parquet(path, index=False)
            source = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_GED_DETALHADO,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
                periodo=None,
            )
            with mock.patch(
                "apps.common.bot_db_sync_runtime.get_bot_db_sync_runtime_config"
            ) as mock_cfg:
                mock_cfg.return_value = mock.Mock(chunk_size=2, batch_size=2)
                self.assertEqual(sync_ged_detalhado_to_db(source), 3)
            self.assertEqual(RotinaGedDetalhadoTratadoRecord.objects.count(), 3)

            df2 = pd.DataFrame(
                [
                    {
                        "Protocolo": "99",
                        "Data do Recebimento": "01/06/2025",
                        "Tipo de Serviço Primário": "TV",
                    }
                ]
            )
            df2.to_parquet(path, index=False)
            source2 = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_GED_DETALHADO,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
                periodo=None,
            )
            self.assertEqual(sync_ged_detalhado_to_db(source2), 1)
            self.assertEqual(RotinaGedDetalhadoTratadoRecord.objects.count(), 1)
            self.assertEqual(
                RotinaGedDetalhadoTratadoRecord.objects.get().protocolo, 99
            )

    def test_sync_ged_detalhado_empty_does_not_delete(self):
        report_date = date(2025, 7, 1)
        RotinaGedDetalhadoTratadoRecord.objects.create(
            report_date=report_date,
            periodo=None,
            protocolo=1,
            tipo_servico_primario="keep",
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ged-detalhado-tratado_202507.parquet"
            pd.DataFrame(
                columns=["Protocolo", "Tipo de Serviço Primário"]
            ).to_parquet(path, index=False)
            source = SourceFileInfo(
                path=path,
                report_type=RotinaBrutoSyncLog.REPORT_GED_DETALHADO,
                report_date=report_date,
                mtime=path.stat().st_mtime,
                size=path.stat().st_size,
                periodo=None,
            )
            self.assertEqual(sync_ged_detalhado_to_db(source), 0)
            self.assertEqual(RotinaGedDetalhadoTratadoRecord.objects.count(), 1)

    def test_run_sync_with_audit_confer_busca_force(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "confer-buscarpIrregularidade-tratado_202506.csv"
            df = pd.DataFrame([{"Protocolo": "42", "Status": "OK"}])
            df.to_csv(path, sep=";", index=False, encoding="cp1252")

            success, log, skipped = run_sync_with_audit(
                report_type=RotinaBrutoSyncLog.REPORT_CONFER_BUSCA,
                path=str(path),
                force=True,
            )
            self.assertTrue(success)
            self.assertFalse(skipped)
            self.assertEqual(log.row_count, 1)
            self.assertEqual(RotinaConferBuscaRecord.objects.count(), 1)
