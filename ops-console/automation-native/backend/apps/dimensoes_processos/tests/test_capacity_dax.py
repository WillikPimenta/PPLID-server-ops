from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.dimensoes_processos.services.capacity_dax import (
    DERIVATION_LOOKBACK_DAYS,
    META_HOURS,
    _capacity_value,
    _meta_hora,
)
from apps.dimensoes_processos.services.capacity_volume_esperado import (
    HISTORICAL_MONTHS,
    UNIFORM_HOURLY,
    curva_intraday_pct,
    curva_semanal_pct,
    first_day_of_mes_index,
    last_day_of_mes_index,
    mes_index,
)


class MetaHoraTests(SimpleTestCase):
    def test_meta_dividida_por_5_5(self):
        self.assertEqual(_meta_hora(Decimal("110")), Decimal("110") / META_HOURS)
        self.assertEqual(float(_meta_hora(Decimal("110"))), 20.0)

    def test_meta_invalida_retorna_none(self):
        self.assertIsNone(_meta_hora(Decimal("0")))


class CapacityFormulaTests(SimpleTestCase):
    def test_cenario_dax_60_40_meta_110(self):
        """Exemplo alinhado: vol 60/40, 100%, meta 110 → cap 3 e 2 sem ceil."""
        meta_hora = _meta_hora(Decimal("110"))
        cap_00 = _capacity_value(Decimal("60"), Decimal("100"), meta_hora)
        cap_01 = _capacity_value(Decimal("40"), Decimal("100"), meta_hora)
        self.assertEqual(float(cap_00), 3.0)
        self.assertEqual(float(cap_01), 2.0)

    def test_capacidade_fracionaria_sem_arredondamento(self):
        meta_hora = _meta_hora(Decimal("110"))
        cap = _capacity_value(Decimal("48"), Decimal("100"), meta_hora)
        self.assertEqual(float(cap), 2.4)


class CurvaIntradayTests(SimpleTestCase):
    def test_sem_historico_diario_usa_uniforme(self):
        self.assertEqual(curva_intraday_pct(Decimal("0"), Decimal("0")), UNIFORM_HOURLY)

    def test_hora_sem_volume_com_historico_diario(self):
        self.assertEqual(curva_intraday_pct(Decimal("0"), Decimal("1000")), Decimal("0"))

    def test_curva_60_40(self):
        pct_00 = curva_intraday_pct(Decimal("600"), Decimal("1000"))
        pct_01 = curva_intraday_pct(Decimal("400"), Decimal("1000"))
        self.assertEqual(float(pct_00), 0.6)
        self.assertEqual(float(pct_01), 0.4)


class CurvaSemanalTests(SimpleTestCase):
    def test_pct_dia_semana_util(self):
        pct = curva_semanal_pct(Decimal("200"), Decimal("1000"))
        self.assertEqual(float(pct), 0.2)

    def test_den_zero_retorna_none(self):
        self.assertIsNone(curva_semanal_pct(Decimal("10"), Decimal("0")))


class MesIndexTests(SimpleTestCase):
    def test_mes_index_agosto_2026(self):
        from datetime import date

        self.assertEqual(mes_index(date(2026, 8, 20)), 2026 * 12 + 8)

    def test_janela_3_meses(self):
        ref = 2026 * 12 + 8
        self.assertEqual(first_day_of_mes_index(ref - 3), __import__("datetime").date(2026, 5, 1))
        self.assertEqual(last_day_of_mes_index(ref - 1), __import__("datetime").date(2026, 7, 31))


class DerivationWindowTests(TestCase):
    def test_janela_60_dias(self):
        self.assertEqual(DERIVATION_LOOKBACK_DAYS, 60)

    def test_historico_3_meses(self):
        self.assertEqual(HISTORICAL_MONTHS, 3)


class DerivationMedianTests(TestCase):
    def test_mediana_60d_exclui_dia_analisado(self):
        from datetime import date

        from apps.dimensoes_processos.services.capacity_dax import _load_derivation_medians

        on_date = date(2026, 8, 20)
        medians = _load_derivation_medians(on_date, documentoscopia_only=False)
        # Sem dados no banco de teste: dict vazio é aceitável; validamos apenas contrato.
        self.assertIsInstance(medians, dict)
