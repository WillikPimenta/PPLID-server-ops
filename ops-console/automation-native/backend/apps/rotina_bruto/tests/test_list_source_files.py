# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.rotina_bruto.models import RotinaBrutoSyncLog
from apps.rotina_bruto.services.source_path import list_source_files


class ListSourceFilesTests(unittest.TestCase):
    def test_lists_and_sorts_by_report_date(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "brflow-detalhado-bruto_20260610.parquet").write_bytes(b"a")
            (folder / "brflow-detalhado-bruto_20260618.parquet").write_bytes(b"b")
            (folder / "brflow-detalhado-bruto_20260615.parquet").write_bytes(b"c")
            (folder / "outro.txt").write_bytes(b"x")

            files = list_source_files(RotinaBrutoSyncLog.REPORT_DETALHADO, folder)
            self.assertEqual(len(files), 3)
            self.assertEqual(
                [f.report_date for f in files],
                [date(2026, 6, 10), date(2026, 6, 15), date(2026, 6, 18)],
            )

    def test_monitor_lists_tratado_parquet_only(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "brflow-monitor-tratado_20260618.csv").write_bytes(b"csv")
            parquet = folder / "brflow-monitor-tratado_20260618.parquet"
            parquet.write_bytes(b"parquet")

            files = list_source_files(RotinaBrutoSyncLog.REPORT_MONITOR, folder)
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].path, parquet)

    def test_periodic_dedupes_by_report_date_and_periodo(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "ged-irregularidade-tratado_202506_1.csv").write_bytes(b"a")
            (folder / "ged-irregularidade-tratado_202506_2.csv").write_bytes(b"b")

            files = list_source_files(RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE, folder)
            self.assertEqual(len(files), 2)
            self.assertEqual(
                sorted((f.report_date, f.periodo) for f in files),
                [(date(2025, 6, 1), 1), (date(2025, 6, 1), 2)],
            )

    def test_date_filters(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "brflow-prod-bruto_20260610.parquet").write_bytes(b"a")
            (folder / "brflow-prod-bruto_20260615.parquet").write_bytes(b"b")
            (folder / "brflow-prod-bruto_20260620.parquet").write_bytes(b"c")

            files = list_source_files(
                RotinaBrutoSyncLog.REPORT_PROD,
                folder,
                from_date=date(2026, 6, 15),
                to_date=date(2026, 6, 18),
            )
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].report_date, date(2026, 6, 15))
