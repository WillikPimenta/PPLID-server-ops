from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone

from django.test import SimpleTestCase

from apps.auditoria.services.reinspecao_sla import (
    add_business_days,
    as_of_for_falha,
    brasilia_date,
    build_sla_kpis_from_falhas,
    business_day_offset,
    data_final_sla,
    format_brasilia_datetime,
    is_business_day,
    is_national_holiday,
    sla_business_days_elapsed,
    sla_farol,
    sla_panel_reference_dates,
)


class ReinspecaoSlaTests(SimpleTestCase):
    def test_example_contestacao_28_jul_2026(self):
        start = date(2026, 7, 28)
        self.assertEqual(data_final_sla(start), date(2026, 7, 30))
        self.assertEqual(sla_farol(start, as_of=date(2026, 7, 28)), "d")
        self.assertEqual(sla_farol(start, as_of=date(2026, 7, 29)), "d1")
        self.assertEqual(sla_farol(start, as_of=date(2026, 7, 30)), "d2")
        self.assertEqual(sla_farol(start, as_of=date(2026, 7, 31)), "estourado")

    def test_skips_weekend(self):
        # Sexta 24/07/2026 → D+2 = terça 28/07 (pula sáb/dom)
        start = date(2026, 7, 24)
        self.assertEqual(add_business_days(start, 2), date(2026, 7, 28))
        self.assertFalse(is_business_day(date(2026, 7, 25)))
        self.assertFalse(is_business_day(date(2026, 7, 26)))

    def test_skips_national_holiday_not_state(self):
        # 09/07/2026 é feriado estadual SP — deve contar como útil
        self.assertTrue(is_business_day(date(2026, 7, 9)))
        # 07/09/2026 Independência — nacional
        self.assertTrue(is_national_holiday(date(2026, 9, 7)))
        self.assertFalse(is_business_day(date(2026, 9, 7)))
        start = date(2026, 9, 4)  # sexta
        # D+1 = seg 07? não, 07 é feriado → ter 08; D+2 = qua 09
        self.assertEqual(add_business_days(start, 1), date(2026, 9, 8))
        self.assertEqual(add_business_days(start, 2), date(2026, 9, 9))

    def test_business_day_offset(self):
        start = date(2026, 7, 28)
        self.assertEqual(business_day_offset(start, date(2026, 7, 28)), 0)
        self.assertEqual(business_day_offset(start, date(2026, 7, 30)), 2)

    def test_uses_brasilia_day_and_ignores_receipt_time(self):
        # 11/08 02:30 UTC ainda é 10/08 em Brasília.
        received = datetime(2026, 8, 11, 2, 30, tzinfo=dt_timezone.utc)
        same_brasilia_day = datetime(2026, 8, 11, 2, 59, tzinfo=dt_timezone.utc)
        next_brasilia_day = datetime(2026, 8, 11, 3, 1, tzinfo=dt_timezone.utc)

        self.assertEqual(brasilia_date(received), date(2026, 8, 10))
        self.assertEqual(sla_business_days_elapsed(received, as_of=same_brasilia_day), 0)
        self.assertEqual(sla_farol(received, as_of=same_brasilia_day), "d")
        self.assertEqual(sla_business_days_elapsed(received, as_of=next_brasilia_day), 1)
        self.assertEqual(sla_farol(received, as_of=next_brasilia_day), "d1")

    def test_elapsed_days_continue_after_deadline(self):
        start = date(2026, 8, 10)
        self.assertEqual(sla_business_days_elapsed(start, as_of=date(2026, 8, 12)), 2)
        self.assertEqual(sla_business_days_elapsed(start, as_of=date(2026, 8, 13)), 3)
        self.assertEqual(sla_farol(start, as_of=date(2026, 8, 13)), "estourado")

    def test_format_brasilia_datetime(self):
        value = datetime(2026, 8, 19, 14, 28, 5, tzinfo=dt_timezone.utc)
        self.assertEqual(
            format_brasilia_datetime(value),
            "19/08/2026 11:28:05",
        )

    def test_sla_panel_reference_dates_forward_business_days(self):
        ref = date(2026, 9, 2)  # quarta
        dates = sla_panel_reference_dates(as_of=ref)
        self.assertEqual(dates["d"], "2026-09-02")
        self.assertEqual(dates["d1"], "2026-09-03")
        self.assertEqual(dates["d2"], "2026-09-04")
        self.assertEqual(dates["estourado"], "")

    def test_sla_panel_reference_dates_skip_weekend(self):
        ref = date(2026, 9, 4)  # sexta
        dates = sla_panel_reference_dates(as_of=ref)
        self.assertEqual(dates["d"], "2026-09-04")
        self.assertEqual(dates["d1"], "2026-09-08")  # seg (07/09 feriado)
        self.assertEqual(dates["d2"], "2026-09-09")

    def test_kpis_can_use_compliance_analysis_date(self):
        class Fake:
            id = 1
            protocolo = "COMP-1"
            data_contestacao = None
            data_analise = date(2026, 8, 10)
            analise_status = "nao_atribuido"
            data_resposta = None
            status = ""

        kpis = build_sla_kpis_from_falhas(
            [Fake()],
            now=datetime(2026, 8, 11, 12, tzinfo=dt_timezone.utc),
            data_origem_field="data_analise",
        )

        self.assertEqual(kpis["em_monitoramento"], 1)
        self.assertEqual(kpis["d1"], 1)

    def test_farol_frozen_after_response(self):
        class Fake:
            data_contestacao = date(2026, 7, 28)
            analise_status = "concluido"
            status = "Improcedente"
            data_resposta = date(2026, 7, 29)
            analise_concluida_em = None

        # Respondido em D+1: farol permanece D+1 mesmo dias depois.
        as_of = as_of_for_falha(Fake(), now=None)
        # as_of_for_falha uses data_resposta; force via sla_farol with that date
        self.assertEqual(sla_farol(Fake.data_contestacao, as_of=date(2026, 7, 29)), "d1")
        self.assertEqual(as_of, date(2026, 7, 29))
        # Se usasse "hoje" futuro, viraria estourado — congelado não.
        self.assertNotEqual(sla_farol(Fake.data_contestacao, as_of=date(2026, 8, 10)), "d1")
        self.assertEqual(
            sla_farol(Fake.data_contestacao, as_of=as_of_for_falha(Fake())),
            "d1",
        )
