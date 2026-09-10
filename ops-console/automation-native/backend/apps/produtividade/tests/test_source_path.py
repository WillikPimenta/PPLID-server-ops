# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.produtividade.services.source_path import (
    get_latest_source_file,
    list_source_files,
)


class SourcePathTests(unittest.TestCase):
    def test_picks_latest_file_by_date_in_name(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "relatorio_produtividade_detalhado_2026-06-10.xlsx").write_bytes(b"a")
            (folder / "relatorio_produtividade_detalhado_2026-06-17.xlsx").write_bytes(b"b")
            (folder / "relatorio_produtividade_detalhado_2026-06-15.xlsx").write_bytes(b"c")

            latest = get_latest_source_file(folder)
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.name, "relatorio_produtividade_detalhado_2026-06-17.xlsx")
            self.assertEqual(latest.file_date, date(2026, 6, 17))

    def test_list_source_files_ignores_other_files(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "relatorio_produtividade_detalhado_2026-06-10.xlsx").write_bytes(b"a")
            (folder / "outro_arquivo.xlsx").write_bytes(b"x")
            files = list_source_files(folder)
            self.assertEqual(len(files), 1)
