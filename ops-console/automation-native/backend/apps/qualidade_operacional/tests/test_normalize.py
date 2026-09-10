# -*- coding: utf-8 -*-
from datetime import date

from django.test import SimpleTestCase

from apps.qualidade_operacional.services.normalize import (
    NIVEL_DIFICULDADE_NAO_INFORMADO,
    clean_text,
    nivel_dificuldade_efetivo,
    normalize_matricula,
    normalize_tipo_conclusao,
    parse_date_br,
    parse_int,
)


class NormalizeTests(SimpleTestCase):
    def test_nivel_dificuldade_confer_has_precedence(self):
        self.assertEqual(
            nivel_dificuldade_efetivo("Difícil Confer", "Fácil Original"),
            "Difícil Confer",
        )

    def test_nivel_dificuldade_falls_back_to_original(self):
        self.assertEqual(
            nivel_dificuldade_efetivo("", "Fácil Original"),
            "Fácil Original",
        )

    def test_nivel_dificuldade_missing_is_explicit(self):
        self.assertEqual(
            nivel_dificuldade_efetivo(None, None),
            NIVEL_DIFICULDADE_NAO_INFORMADO,
        )

    def test_clean_sentinels(self):
        self.assertEqual(clean_text("Não se aplica"), "")
        self.assertEqual(clean_text("-"), "")
        self.assertEqual(clean_text("  ok  "), "ok")

    def test_parse_date_br(self):
        self.assertEqual(parse_date_br("03/05/2024"), date(2024, 5, 3))
        self.assertEqual(parse_date_br("2024-05-03"), date(2024, 5, 3))
        self.assertIsNone(parse_date_br(""))

    def test_parse_int(self):
        self.assertEqual(parse_int("83"), 83)
        self.assertIsNone(parse_int(""))
        self.assertIsNone(parse_int("Não se aplica"))

    def test_normalize_matricula(self):
        self.assertEqual(normalize_matricula("C92629A"), "c92629a")
        self.assertEqual(normalize_matricula("Não se aplica"), "")

    def test_normalize_tipo_conclusao(self):
        self.assertEqual(normalize_tipo_conclusao("MANUAL"), "Manual")
        self.assertEqual(normalize_tipo_conclusao("AUTOMATICO"), "Automático")
        self.assertEqual(normalize_tipo_conclusao("AUTOMÁTICO"), "Automático")
        self.assertEqual(normalize_tipo_conclusao("MAPEAMENTO"), "Automático")
        self.assertEqual(normalize_tipo_conclusao("Mapeamento"), "Automático")
        self.assertEqual(normalize_tipo_conclusao("Sistema"), "Automático")
        self.assertEqual(normalize_tipo_conclusao("SISTEMA"), "Automático")
        self.assertEqual(normalize_tipo_conclusao("Processual"), "Processual")
        self.assertEqual(normalize_tipo_conclusao("Não se aplica"), "Manual")
        self.assertEqual(normalize_tipo_conclusao("#N/D"), "Manual")
        self.assertEqual(normalize_tipo_conclusao(""), "Manual")
        self.assertEqual(normalize_tipo_conclusao("Biometria"), "Manual")
        self.assertEqual(normalize_tipo_conclusao("Regra De Negócio"), "Manual")
