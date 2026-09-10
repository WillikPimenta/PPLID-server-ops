# -*- coding: utf-8 -*-
"""Testes da chave composta protocolo + matrícula."""
from datetime import date

from django.test import SimpleTestCase, TestCase

from apps.qualidade_operacional.models import QualidadeFalha
from apps.qualidade_operacional.services.case_key import (
    build_case_key,
    build_case_key_str,
    compare_falha_priority,
    dedupe_falha_fields_in_memory,
    find_canonical_falha,
    is_case_key_eligible,
    resolve_falha_conflict,
)


class CaseKeyTests(SimpleTestCase):
    def test_build_case_key_normalizes_protocolo_e_matricula(self):
        self.assertEqual(build_case_key(" 00012345 ", "C10001A"), ("12345", "c10001a"))
        self.assertEqual(build_case_key_str("00012345", "C10001A"), "12345|c10001a")

    def test_chave_vazia_quando_protocolo_ou_matricula_ausente(self):
        self.assertEqual(build_case_key_str("", "c10001a"), "")
        self.assertEqual(build_case_key_str("123", ""), "")
        self.assertFalse(is_case_key_eligible("", "c10001a"))

    def test_chaves_distintas_para_operadores_diferentes(self):
        key_a = build_case_key_str("999", "c10001a")
        key_b = build_case_key_str("999", "c20002a")
        self.assertNotEqual(key_a, key_b)

    def test_compare_falha_priority_incoming_mais_antigo_vence(self):
        incoming = {"data": date(2026, 7, 1), "data_analise": date(2026, 7, 2)}
        existing = QualidadeFalha(data=date(2026, 8, 1), data_analise=date(2026, 8, 2))
        self.assertEqual(compare_falha_priority(incoming, existing), -1)
        self.assertEqual(resolve_falha_conflict(incoming, existing), "replace")

    def test_compare_falha_priority_existing_mais_antigo_vence(self):
        incoming = {"data": date(2026, 8, 1)}
        existing = QualidadeFalha(data=date(2026, 7, 1))
        self.assertEqual(compare_falha_priority(incoming, existing), 1)
        self.assertEqual(resolve_falha_conflict(incoming, existing), "skip")

    def test_dedupe_falha_fields_in_memory_mantem_mais_antigo(self):
        items = [
            {"protocolo": "1", "matricula": "c10001a", "data": date(2026, 8, 1)},
            {"protocolo": "1", "matricula": "c10001a", "data": date(2026, 7, 1)},
        ]
        deduped, skipped = dedupe_falha_fields_in_memory(items)
        self.assertEqual(skipped, 1)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["data"], date(2026, 7, 1))


class CaseKeyDbTests(TestCase):
    def test_find_canonical_falha_retorna_mais_antiga(self):
        QualidadeFalha.objects.create(
            protocolo="123",
            matricula="c10001a",
            case_key="123|c10001a",
            data=date(2026, 8, 1),
            source_file="b.tsv",
        )
        older = QualidadeFalha.objects.create(
            protocolo="123",
            matricula="c10001a",
            case_key="123|c10001a",
            data=date(2026, 7, 1),
            source_file="a.tsv",
        )
        canonical = find_canonical_falha(protocolo="123", matricula="c10001a")
        self.assertEqual(canonical.pk, older.pk)
