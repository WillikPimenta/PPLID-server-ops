# -*- coding: utf-8 -*-
from pathlib import Path
import tempfile
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.produtividade.models import CASE_ETAPA, CASE_STAGE_GOAL
from apps.produtividade_case.services.hxh_builder import (
    aggregate_consolidado,
    aggregate_prod_hora,
    build_case_parsed_rows,
)
from apps.produtividade_case.services.excel_reader import _repair_mojibake
from apps.produtividade_case.services.source_path import extract_periodo_mes


class ExtractPeriodoMesTests(SimpleTestCase):
    def test_from_folder(self):
        path = Path(r"C:\relatorios\mai-2026\prod_hora_2026-05-15.xlsx")
        self.assertEqual(extract_periodo_mes(path), "mai-2026")

    def test_repairs_only_valid_utf8_as_latin1_mojibake(self):
        self.assertEqual(_repair_mojibake("DocumentaÃ§Ã£o"), "Documentação")
        self.assertEqual(_repair_mojibake("SÃO PAULO"), "SÃO PAULO")


class HxHBuilderTests(SimpleTestCase):
    def _dir(self) -> Path:
        base = Path(tempfile.mkdtemp()) / "mai-2026"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def test_build_merges_hora_count_and_consolidado_seconds(self):
        import pandas as pd

        d = self._dir()
        cons = d / "Relatorio_Produtividade_Consolidado_01-15mai.xlsx"
        hora = d / "prod_hora_2026-05-15_14-00.xlsx"

        df_c = pd.DataFrame(
            [
                {
                    "Protocolo Destino": "p1",
                    "Matricula Destino": "c92928a",
                    "Data Conclusao Destino": "15/05/2026",
                    "Hora Conclusao Destino": "10:30:00",
                    "Tempo de Analise": "00:05:00",
                },
                {
                    "Protocolo Destino": "p2",
                    "Matricula Destino": "c92928a",
                    "Data Conclusao Destino": "15/05/2026",
                    "Hora Conclusao Destino": "10:45:00",
                    "Tempo de Analise": "00:03:00",
                },
            ]
        )
        with pd.ExcelWriter(cons, engine="openpyxl") as w:
            df_c.to_excel(w, index=False, sheet_name="Produtividade")

        df_h = pd.DataFrame({"10": [5], "TOTAL AGENTE": [5]}, index=["c92928a"])
        with pd.ExcelWriter(hora, engine="openpyxl") as w:
            df_h.to_excel(w)

        rows = build_case_parsed_rows(consolidado_path=cons, hora_path=hora)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.matricula_norm, "c92928a")
        self.assertEqual(r.etapa, CASE_ETAPA)
        self.assertEqual(r.stage_goal, Decimal(CASE_STAGE_GOAL))
        self.assertEqual(r.analysis_count, 5)  # from prod_hora
        self.assertEqual(r.analysis_seconds, 480)  # 5+3 min
        self.assertEqual(r.recorded_at.hour, 10)

    def test_aggregate_helpers(self):
        import pandas as pd

        d = self._dir()
        cons = d / "Relatorio_Produtividade_Consolidado_01-15mai.xlsx"
        df_c = pd.DataFrame(
            [
                {
                    "Protocolo Destino": "p1",
                    "Matricula Destino": "X",
                    "Data Conclusao Destino": "15/05/2026",
                    "Hora Conclusao Destino": "11:00:00",
                    "Tempo de Analise": "00:01:00",
                }
            ]
        )
        with pd.ExcelWriter(cons, engine="openpyxl") as w:
            df_c.to_excel(w, index=False, sheet_name="Produtividade")
        buckets = aggregate_consolidado(cons)
        self.assertEqual(buckets[("x", date(2026, 5, 15), 11)]["seconds"], 60)

        hora = d / "prod_hora_2026-05-15_12-00.xlsx"
        df_h = pd.DataFrame({"11": [2], "TOTAL AGENTE": [2]}, index=["X"])
        with pd.ExcelWriter(hora, engine="openpyxl") as w:
            df_h.to_excel(w)
        counts = aggregate_prod_hora(hora, date(2026, 5, 15))
        self.assertEqual(counts[("x", date(2026, 5, 15), 11)], 2)
