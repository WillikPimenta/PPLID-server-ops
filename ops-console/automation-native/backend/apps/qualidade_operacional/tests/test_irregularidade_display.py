# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.qualidade_operacional.services.detail_column_filters import (
    EMPTY_LABEL,
    _column_specs,
    _display_for_row,
)
from apps.qualidade_operacional.services.irregularidade_display import (
    irregularidade_apontada_falha,
)


class IrregularidadeDisplayTests(SimpleTestCase):
    def test_prefers_des_problemas_over_generic_cenario(self):
        self.assertEqual(
            irregularidade_apontada_falha(
                des_problemas="IC - 650 - IMEI em inconformidade",
                cenario="Não sinalizada - Irregularidade / Sinalização incorreta - Irregularidade",
            ),
            "IC - 650 - IMEI em inconformidade",
        )

    def test_falls_back_to_cenario_when_des_problemas_empty(self):
        self.assertEqual(
            irregularidade_apontada_falha(
                des_problemas="",
                cenario="Reclassificação Incorreta",
            ),
            "Reclassificação Incorreta",
        )


class DetailColumnCenarioDisplayTests(SimpleTestCase):
    def test_cenario_column_uses_irregularidade_apontada(self):
        row = type(
            "Row",
            (),
            {
                "des_problemas": "IC - 445 - assinatura diverge",
                "cenario": "Não sinalizada - Irregularidade / Sinalização incorreta - Irregularidade",
            },
        )()
        spec = _column_specs("falhas")["cenario"]
        label = _display_for_row(row, spec, agents={}, clientes={}, workflows={})
        self.assertEqual(label, "IC - 445 - assinatura diverge")

    def test_cenario_column_empty_fallback(self):
        row = type("Row", (), {"des_problemas": "", "cenario": ""})()
        spec = _column_specs("falhas")["cenario"]
        label = _display_for_row(row, spec, agents={}, clientes={}, workflows={})
        self.assertEqual(label, EMPTY_LABEL)
