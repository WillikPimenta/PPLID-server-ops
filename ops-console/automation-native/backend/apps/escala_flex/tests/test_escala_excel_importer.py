"""Testes do importador de escala Excel."""

from datetime import date
from io import BytesIO
from unittest.mock import patch

import pandas as pd
from django.test import TestCase

from apps.escala_flex.models import Escala, Schedule
from apps.escala_flex.services.escala_excel_importer import (
    EscalaExcelImporter,
    cell_to_dia_escala,
    import_escala_excel,
)
from apps.workforce.models import Agent


def build_sample_xlsx(agents: list[tuple[str, str]]) -> bytes:
    """Gera xlsx mínimo no formato __TESTE26."""
    dates = ["01/06/2026", "02/06/2026", "03/06/2026", "04/06/2026", "05/06/2026"]
    rows = []
    for i, (lan, name) in enumerate(agents):
        row = {
            "BLOCO": i + 1,
            "COLABORADOR": name,
            "MATRÍCULA": lan,
            "LIDERANCA": "Lider Teste",
            "HORÁRIO": "08:00 - 14:00",
            "ATIVIDADE": "Conferência",
            "UF": "Brasília",
            "EQUIPE": "CONFER",
        }
        for j, d in enumerate(dates):
            if j == 1:
                row[d] = "FOLGA"
            elif j == 2:
                row[d] = "11:00 - 17:00"
            else:
                row[d] = ""
        rows.append(row)

    df = pd.DataFrame(rows)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="__TESTE26", index=False)
    return buf.getvalue()


class EscalaExcelImporterTests(TestCase):
    def setUp(self):
        self.leader = Agent.objects.create(
            user_lan_id="lider001",
            full_name="Lider Teste",
            active=True,
        )
        self.agents = []
        for lan, name in [
            ("agent001", "Agente Um"),
            ("agent002", "Agente Dois"),
            ("agent003", "Agente Tres"),
        ]:
            self.agents.append(
                Agent.objects.create(
                    user_lan_id=lan,
                    full_name=name,
                    active=True,
                )
            )

    def test_cell_to_dia_escala(self):
        self.assertEqual(cell_to_dia_escala("FOLGA"), "FOLGA")
        self.assertEqual(cell_to_dia_escala("11:00 - 17:00"), "11:00 - 17:00")
        self.assertEqual(cell_to_dia_escala(""), "")

    def test_map_to_schedule(self):
        importer = EscalaExcelImporter()
        self.assertEqual(
            importer.map_to_schedule("08:00 - 14:00", "11:00 - 17:00"),
            ("11:00 - 17:00", True),
        )
        self.assertEqual(
            importer.map_to_schedule("08:00 - 14:00", "FOLGA"),
            ("", False),
        )
        self.assertEqual(
            importer.map_to_schedule("08:00 - 14:00", ""),
            ("08:00 - 14:00", True),
        )

    def test_unpivot_produces_agent_day_rows(self):
        content = build_sample_xlsx(
            [("agent001", "Agente Um"), ("agent002", "Agente Dois")]
        )
        importer = EscalaExcelImporter()
        df = pd.read_excel(BytesIO(content), sheet_name="__TESTE26")
        rows = importer.unpivot_sheet(df, "__TESTE26")
        self.assertEqual(len(rows), 10)
        folga_rows = [r for r in rows if r["dia_escala"] == "FOLGA"]
        self.assertEqual(len(folga_rows), 2)

    @patch("apps.escala_flex.services.escala_excel_importer.ScheduleTodayService.build_for_date")
    def test_import_creates_escala_and_schedule(self, mock_rebuild):
        content = build_sample_xlsx(
            [("agent001", "Agente Um"), ("agent002", "Agente Dois"), ("agent003", "Agente Tres")]
        )
        result = import_escala_excel(
            BytesIO(content), filename="test.xlsx", rebuild_async=False
        )
        self.assertIn("__TESTE26", result.sheets_processed)
        self.assertEqual(result.rows_upserted, 15)
        self.assertEqual(Escala.objects.count(), 15)
        self.assertEqual(Schedule.objects.count(), 15)

        entry = Escala.objects.get(agent=self.agents[0], data=date(2026, 6, 2))
        self.assertEqual(entry.dia_escala, "FOLGA")
        self.assertEqual(entry.horario, "08:00 - 14:00")

        schedule = Schedule.objects.get(agent=self.agents[0], date=date(2026, 6, 2))
        self.assertFalse(schedule.work_day)
        self.assertEqual(schedule.work_schedule, "")

        schedule_work = Schedule.objects.get(agent=self.agents[0], date=date(2026, 6, 3))
        self.assertEqual(schedule_work.work_schedule, "11:00 - 17:00")
        self.assertTrue(schedule_work.work_day)

        self.assertTrue(mock_rebuild.called)
        self.assertEqual(len(result.dates_rebuilt), 5)

    def test_unknown_matricula_reported_as_error(self):
        content = build_sample_xlsx([("unknown99", "Desconhecido")])
        result = import_escala_excel(BytesIO(content), filename="test.xlsx")
        self.assertEqual(result.rows_upserted, 0)
        self.assertEqual(len(result.errors), 5)
        self.assertEqual(Escala.objects.count(), 0)

    def test_import_dedupes_duplicate_agent_date_in_chunk(self):
        """Duas linhas do mesmo agente na aba geram last-wins sem erro de bulk upsert."""
        dates = ["01/06/2026", "02/06/2026"]
        rows = [
            {
                "BLOCO": 1,
                "COLABORADOR": "Agente Um",
                "MATRÍCULA": "agent001",
                "LIDERANCA": "0",
                "HORÁRIO": "08:00 - 14:00",
                "ATIVIDADE": "Conferência",
                "UF": "Brasília",
                "EQUIPE": "CONFER",
                "01/06/2026": "",
                "02/06/2026": "FOLGA",
            },
            {
                "BLOCO": 2,
                "COLABORADOR": "Agente Um",
                "MATRÍCULA": "agent001",
                "LIDERANCA": "0",
                "HORÁRIO": "08:00 - 14:00",
                "ATIVIDADE": "Conferência",
                "UF": "Brasília",
                "EQUIPE": "CONFER",
                "01/06/2026": "11:00 - 17:00",
                "02/06/2026": "FOLGA",
            },
        ]
        df = pd.DataFrame(rows)
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="__TESTE26", index=False)

        result = import_escala_excel(
            BytesIO(buf.getvalue()), filename="test.xlsx", rebuild_async=False
        )
        self.assertIn("__TESTE26", result.sheets_processed)
        self.assertEqual(result.rows_upserted, 2)
        self.assertEqual(Escala.objects.count(), 2)
        self.assertTrue(
            any("duplicada" in w.get("message", "").lower() for w in result.warnings)
        )

        entry_jun1 = Escala.objects.get(agent=self.agents[0], data=date(2026, 6, 1))
        self.assertEqual(entry_jun1.dia_escala, "11:00 - 17:00")

        entry_jun2 = Escala.objects.get(agent=self.agents[0], data=date(2026, 6, 2))
        self.assertEqual(entry_jun2.dia_escala, "FOLGA")
