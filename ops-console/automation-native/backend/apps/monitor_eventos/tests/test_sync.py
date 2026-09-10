# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from django.test import TestCase
from django.utils import timezone

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.monitor_eventos.services.parquet_reader import read_monitor_parquet
from apps.monitor_eventos.services.sync import sync_monitor_eventos_to_db


class MonitorParquetReaderTests(unittest.TestCase):
    def test_reads_tratado_schema(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor-eventos-tratado_2026-06-18.parquet"
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
            rows = read_monitor_parquet(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].data, datetime(2026, 6, 18).date())
            self.assertEqual(rows[0].hora, 10)
            self.assertEqual(rows[0].matricula_usuario, "c92928a")
            self.assertEqual(rows[0].evento, "Autenticação com sucesso")
            self.assertEqual(rows[0].segundo_evento, "Logout")
            self.assertIsNotNone(rows[0].data_segundo_evento)

    def test_reads_mixed_second_event_datetime_precision(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor-eventos-tratado_2026-08-28.parquet"
            df = pd.DataFrame(
                [
                    {
                        "Data": "2026-08-28",
                        "Hora": 10,
                        "Usuário": "c21908q",
                        "Data do Evento": "2026-08-28 10:55:45",
                        "Evento": "Autenticação com sucesso",
                        "Data segundo evento": "2026-08-28 11:08:59",
                        "Segundo evento": "Logout",
                    },
                    {
                        "Data": "2026-08-28",
                        "Hora": 10,
                        "Usuário": "c92928a",
                        "Data do Evento": "2026-08-28 10:57:00.123456",
                        "Evento": "Autenticação com sucesso",
                        "Data segundo evento": "2026-08-28 11:17:16.509807",
                        "Segundo evento": "Logout",
                    },
                ]
            )
            df.to_parquet(path, index=False)

            rows = read_monitor_parquet(path)

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].data_segundo_evento.second, 59)
            self.assertEqual(rows[1].data_segundo_evento.microsecond, 509807)


class MonitorSyncTests(TestCase):
    def test_sync_truncates_and_reloads(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor-eventos-tratado_2026-06-18.parquet"
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
            MonitorEventoRecord.objects.create(
                data=datetime(2026, 1, 1).date(),
                hora=0,
                matricula_usuario="old",
                data_evento=timezone.make_aware(datetime(2026, 1, 1, 0, 0, 0)),
                evento="Old",
            )
            ok, _, count = sync_monitor_eventos_to_db(path=path, force=True)
            self.assertTrue(ok)
            self.assertEqual(count, 1)
            self.assertEqual(MonitorEventoRecord.objects.count(), 1)
            record = MonitorEventoRecord.objects.get()
            self.assertEqual(record.matricula_usuario, "c92928a")
            self.assertEqual(record.hora, 10)
            self.assertEqual(record.segundo_evento, "Logout")
            self.assertIsNotNone(record.data_segundo_evento)
