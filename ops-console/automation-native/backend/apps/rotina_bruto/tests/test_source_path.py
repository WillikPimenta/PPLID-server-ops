# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.rotina_bruto.models import RotinaBrutoSyncLog
from apps.rotina_bruto.services.source_path import (
    get_source_file,
    parse_file_meta,
    parse_report_date_from_name,
)


class SourcePathTests(unittest.TestCase):
    def test_parse_report_date_detalhado(self):
        parsed = parse_report_date_from_name(
            RotinaBrutoSyncLog.REPORT_DETALHADO,
            "brflow-detalhado-bruto_20260618.parquet",
        )
        self.assertEqual(parsed, date(2026, 6, 18))

    def test_parse_report_date_monitor_tratado(self):
        parsed = parse_report_date_from_name(
            RotinaBrutoSyncLog.REPORT_MONITOR,
            "brflow-monitor-tratado_20260618.parquet",
        )
        self.assertEqual(parsed, date(2026, 6, 18))

    def test_parse_confer_busca_yyyymm(self):
        meta = parse_file_meta(
            RotinaBrutoSyncLog.REPORT_CONFER_BUSCA,
            "confer-buscarpIrregularidade-tratado_202506.csv",
        )
        self.assertEqual(meta, (date(2025, 6, 1), None))

    def test_parse_ged_detalhado_mensal(self):
        meta = parse_file_meta(
            RotinaBrutoSyncLog.REPORT_GED_DETALHADO,
            "ged-detalhado-tratado_202506.parquet",
        )
        self.assertEqual(meta, (date(2025, 6, 1), None))

    def test_parse_ged_detalhado_quinzena_nao_e_fonte_de_sync(self):
        """Partes _1/_2 são intermediárias; só o mensal consolidado synca."""
        meta = parse_file_meta(
            RotinaBrutoSyncLog.REPORT_GED_DETALHADO,
            "ged-detalhado-tratado_202506_2.parquet",
        )
        self.assertIsNone(meta)

    def test_parse_ged_irregularidade_quinzena(self):
        meta = parse_file_meta(
            RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE,
            "ged-irregularidade-tratado_202506_1.csv",
        )
        self.assertEqual(meta, (date(2025, 6, 1), 1))

    def test_get_source_file_from_override_path(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-prod-bruto_20260617.parquet"
            path.write_bytes(b"test")
            info = get_source_file(RotinaBrutoSyncLog.REPORT_PROD, override_path=str(path))
            self.assertEqual(info.report_date, date(2026, 6, 17))
            self.assertEqual(info.path, path)

    def test_get_source_file_periodo_from_override(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ged-irregularidade-tratado_202506_2.csv"
            path.write_text("Protocolo\n1\n", encoding="cp1252")
            info = get_source_file(
                RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE,
                override_path=str(path),
            )
            self.assertEqual(info.report_date, date(2025, 6, 1))
            self.assertEqual(info.periodo, 2)
