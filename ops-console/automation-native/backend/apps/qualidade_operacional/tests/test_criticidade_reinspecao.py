# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.qualidade_operacional.services.criticidade import (
    criticidade_label,
    is_reinspecao_tipo_registro,
)


class CriticidadeReinspecaoTests(SimpleTestCase):
    def test_reinspecao_forca_procedimento_mesmo_com_categoria_critica(self):
        self.assertTrue(is_reinspecao_tipo_registro("reinspecao"))
        self.assertEqual(
            criticidade_label("Crítica", tipo_registro="reinspecao"),
            "Procedimento",
        )
        self.assertEqual(
            criticidade_label("", tipo_registro="reinspecao"),
            "Procedimento",
        )

    def test_nao_reinspecao_mantem_classificacao(self):
        self.assertEqual(criticidade_label("Crítica"), "Crítica")
        self.assertEqual(criticidade_label(""), "Não informada")
