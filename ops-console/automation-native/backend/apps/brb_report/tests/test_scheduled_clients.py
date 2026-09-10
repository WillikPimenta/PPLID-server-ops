# -*- coding: utf-8 -*-
from datetime import date

from django.test import TestCase

from apps.brb_report.services.client_catalog import list_scheduled_report_client_slugs
from apps.dimensoes_processos.models import DimCliente
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha


class ScheduledClientSlugsTests(TestCase):
    def test_includes_dynamic_eo_client_without_registry(self):
        DimCliente.objects.create(id_cliente=501, nome="BANCO EXEMPLO SA")
        QualidadeAuditado.objects.create(
            id_cliente=501,
            protocolo="X1",
            data=date(2026, 1, 1),
            source_file="test",
        )
        slugs = list_scheduled_report_client_slugs()
        self.assertIn("banco-exemplo-sa", slugs)

    def test_excludes_client_without_eo_data(self):
        DimCliente.objects.create(id_cliente=502, nome="VAZIO LTDA")
        slugs = list_scheduled_report_client_slugs()
        self.assertNotIn("vazio-ltda", slugs)

    def test_registry_client_with_eo_included(self):
        DimCliente.objects.create(id_cliente=35, nome="BRB BANCO DE BRASILIA")
        QualidadeFalha.objects.create(
            id_cliente=35,
            protocolo="F1",
            data=date(2026, 1, 2),
            source_file="test",
        )
        slugs = list_scheduled_report_client_slugs()
        self.assertIn("brb", slugs)

    def test_disabled_registry_client_excluded(self):
        DimCliente.objects.create(id_cliente=35, nome="BRB BANCO DE BRASILIA")
        QualidadeFalha.objects.create(
            id_cliente=35,
            protocolo="F2",
            data=date(2026, 1, 3),
            source_file="test",
        )
        from report_brb import client_registry

        original = client_registry.CLIENTS["brb"]["enabled"]
        try:
            client_registry.CLIENTS["brb"]["enabled"] = False
            slugs = list_scheduled_report_client_slugs(enabled_only=True)
            self.assertNotIn("brb", slugs)
        finally:
            client_registry.CLIENTS["brb"]["enabled"] = original
