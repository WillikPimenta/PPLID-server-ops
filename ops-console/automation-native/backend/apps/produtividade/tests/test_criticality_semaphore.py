# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.produtividade.services.criticality import (
    CRITICALITY_METHODOLOGY_VERSION,
    classify_criticality,
    criticality_label,
    criticality_methodology_payload,
)
from apps.produtividade.services.semaphore import meta_performance_tone, meta_tone_label


class CriticalityTests(SimpleTestCase):
    def test_border_values(self):
        self.assertEqual(classify_criticality(0), "critical")
        self.assertEqual(classify_criticality(50), "critical")
        self.assertEqual(classify_criticality(50.01), "high")
        self.assertEqual(classify_criticality(60), "high")
        self.assertEqual(classify_criticality(60.01), "medium")
        self.assertEqual(classify_criticality(70), "medium")
        self.assertEqual(classify_criticality(70.01), "low")
        self.assertEqual(classify_criticality(90), "low")
        self.assertEqual(classify_criticality(90.01), "expected")
        self.assertEqual(classify_criticality(None), "unclassifiable")

    def test_labels(self):
        self.assertEqual(criticality_label("critical"), "Crítico")
        self.assertEqual(criticality_label("high"), "Alto")
        self.assertEqual(criticality_label("medium"), "Médio")
        self.assertEqual(criticality_label("low"), "Baixo")
        self.assertEqual(criticality_label("expected"), "Dentro do esperado")
        self.assertEqual(criticality_label("unclassifiable"), "Não classificável")
        self.assertEqual(criticality_label("moderate"), "Alto")  # legado

    def test_methodology_payload(self):
        payload = criticality_methodology_payload()
        self.assertEqual(payload["methodology_version"], CRITICALITY_METHODOLOGY_VERSION)
        self.assertEqual(len(payload["bands"]), 5)


class SemaphoreTests(SimpleTestCase):
    def test_higher_is_better(self):
        self.assertEqual(meta_performance_tone(92, 92), "ok")
        self.assertEqual(meta_performance_tone(87.4, 92), "near")
        self.assertEqual(meta_performance_tone(87.3, 92), "below")
        self.assertEqual(meta_performance_tone(None, 92), "neutral")
        self.assertEqual(meta_performance_tone(92, None), "neutral")

    def test_lower_is_better(self):
        self.assertEqual(meta_performance_tone(10, 12, higher_is_better=False), "ok")
        self.assertEqual(meta_performance_tone(12.5, 12, higher_is_better=False), "near")
        self.assertEqual(meta_performance_tone(14, 12, higher_is_better=False), "below")

    def test_labels(self):
        self.assertEqual(meta_tone_label("ok"), "Na meta")
        self.assertEqual(meta_tone_label("neutral"), "Meta não identificada")
