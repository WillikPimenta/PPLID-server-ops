# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.replicacao_d1.normalization import normalize_protocolo


class NormalizeProtocoloTests(SimpleTestCase):
    def test_strip_and_lowercase_non_numeric(self):
        self.assertEqual(normalize_protocolo("  ABC123  "), "abc123")

    def test_remove_leading_zeros(self):
        self.assertEqual(normalize_protocolo("00012345"), "12345")
        self.assertEqual(normalize_protocolo(12345), "12345")

    def test_empty_values(self):
        self.assertEqual(normalize_protocolo(""), "")
        self.assertEqual(normalize_protocolo(None), "")
