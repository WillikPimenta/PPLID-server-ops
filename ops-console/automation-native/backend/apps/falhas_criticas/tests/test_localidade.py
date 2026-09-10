# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.falhas_criticas.services.localidade import (
    canonicalize_localidade,
    db_values_for_canonical,
)
from apps.falhas_criticas.services.narrative import build_comparativo_narrative


class LocalidadeCanonicalizeTests(SimpleTestCase):
    def test_bsb_aliases(self):
        self.assertEqual(canonicalize_localidade("BSB"), "Brasília")
        self.assertEqual(canonicalize_localidade("brasilia"), "Brasília")

    def test_sc_aliases(self):
        self.assertEqual(canonicalize_localidade("Sao Carlos"), "São Carlos")
        self.assertEqual(canonicalize_localidade("sanca"), "São Carlos")

    def test_unknown_keeps_original(self):
        self.assertEqual(canonicalize_localidade("Campinas"), "Campinas")

    def test_db_values_include_aliases(self):
        values = db_values_for_canonical("São Carlos")
        self.assertIn("São Carlos", values)
        self.assertIn("Sao Carlos", values)


class ComparativoNarrativeTests(SimpleTestCase):
    def test_sc_zero_gets_explicit_message(self):
        bsb = {"falhas": {"total": 8}, "suporte": {}}
        sc = {"falhas": {"total": 0}, "suporte": {}}
        out = build_comparativo_narrative(bsb, sc, [])
        self.assertIn("sem registros", out["veredito"].lower())
