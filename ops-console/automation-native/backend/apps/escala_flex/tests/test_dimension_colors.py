"""Testes de vínculo de cores entre status e ocorrências."""

from django.test import TestCase

from apps.escala_flex.models import OccurrenceType, StatusType
from apps.escala_flex.services.dimension_colors import (
    find_matching_status,
    names_match_occurrence_status,
    resolve_occurrence_color,
    sync_occurrence_colors_for_status,
)


class DimensionColorLinkTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=7, name="Treinamento", color="#0566B2", active=True)
        StatusType.objects.create(
            pk=13, name="Elevate / Portais", color="#0566B2", active=True
        )
        StatusType.objects.create(
            pk=11, name="Problemas sistêmicos", color="#A86EEB", active=True
        )

    def test_exact_name_match(self):
        self.assertTrue(names_match_occurrence_status("Treinamento", "Treinamento"))

    def test_portais_match(self):
        self.assertTrue(
            names_match_occurrence_status("Acesso aos Portais", "Elevate / Portais")
        )

    def test_resolve_occurrence_color(self):
        color = resolve_occurrence_color("Treinamento")
        self.assertEqual(color, "#0566B2")

    def test_sync_occurrence_colors_for_status(self):
        occurrence, _ = OccurrenceType.objects.update_or_create(
            pk=99,
            defaults={"name": "Treinamento", "color": "", "active": True},
        )
        status = StatusType.objects.get(pk=7)
        updated = sync_occurrence_colors_for_status(status)
        occurrence.refresh_from_db()
        self.assertGreaterEqual(updated, 1)
        self.assertEqual(occurrence.color, "#0566B2")

    def test_find_matching_status(self):
        status = find_matching_status("Acesso aos Portais")
        self.assertIsNotNone(status)
        self.assertEqual(status.name, "Elevate / Portais")
