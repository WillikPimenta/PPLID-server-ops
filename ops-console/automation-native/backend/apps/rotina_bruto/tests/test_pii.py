# -*- coding: utf-8 -*-
import unittest

from django.test import override_settings

from apps.rotina_bruto.services.pii import hash_cpf, normalize_cpf


class CpfHashTests(unittest.TestCase):
    def test_normalize_cpf_strips_formatting(self):
        self.assertEqual(normalize_cpf("123.456.789-00"), "12345678900")

    def test_empty_cpf_returns_empty_hash(self):
        self.assertEqual(hash_cpf(""), "")
        self.assertEqual(hash_cpf("123"), "")

    @override_settings(SECRET_KEY="test-secret-key")
    def test_hash_is_deterministic_and_not_plaintext(self):
        hashed = hash_cpf("123.456.789-00")
        self.assertEqual(len(hashed), 64)
        self.assertNotIn("123", hashed)
        self.assertEqual(hashed, hash_cpf("12345678900"))

    @override_settings(SECRET_KEY="test-secret-key")
    def test_different_secret_produces_different_hash(self):
        with override_settings(SECRET_KEY="other-key"):
            other = hash_cpf("12345678900")
        with override_settings(SECRET_KEY="test-secret-key"):
            original = hash_cpf("12345678900")
        self.assertNotEqual(original, other)
