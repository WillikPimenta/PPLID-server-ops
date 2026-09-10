# -*- coding: utf-8 -*-
from datetime import date

from django.test import TestCase

from apps.brb_report.services.client_catalog import list_report_clients
from apps.dimensoes_processos.models import DimCliente
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.criticidade import (
    CLIENTE_CLARO_FORMALIZACAO_ID,
    CLIENTE_GAQ_ID,
)


class ClientCatalogListTests(TestCase):
    def test_excludes_claro_formalizacao_by_id(self):
        DimCliente.objects.create(
            id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID,
            nome="CLARO - FORMALIZAÇÃO",
        )
        QualidadeAuditado.objects.create(
            id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID,
            protocolo="F1",
            data=date(2026, 1, 1),
            source_file="test",
        )
        ids = {row["id_cliente"] for row in list_report_clients()}
        self.assertNotIn(CLIENTE_CLARO_FORMALIZACAO_ID, ids)

    def test_excludes_claro_formalizacao_by_dimcliente_nome(self):
        DimCliente.objects.create(id_cliente=83, nome="CLARO - FORMALIZAÇÃO")
        QualidadeAuditado.objects.create(
            id_cliente=83,
            protocolo="F83",
            data=date(2026, 1, 1),
            source_file="test",
        )
        ids = {row["id_cliente"] for row in list_report_clients()}
        self.assertNotIn(83, ids)

    def test_excludes_gaq_by_id(self):
        DimCliente.objects.create(id_cliente=CLIENTE_GAQ_ID, nome="GAQ")
        QualidadeAuditado.objects.create(
            id_cliente=CLIENTE_GAQ_ID,
            protocolo="G1",
            data=date(2026, 1, 1),
            source_file="test",
        )
        ids = {row["id_cliente"] for row in list_report_clients()}
        self.assertNotIn(CLIENTE_GAQ_ID, ids)

    def test_excludes_gaq_by_dimcliente_nome(self):
        DimCliente.objects.create(id_cliente=88, nome="GAQ")
        QualidadeAuditado.objects.create(
            id_cliente=88,
            protocolo="G88",
            data=date(2026, 1, 1),
            source_file="test",
        )
        ids = {row["id_cliente"] for row in list_report_clients()}
        self.assertNotIn(88, ids)

    def test_includes_dimcliente_and_eo_volume(self):
        DimCliente.objects.create(id_cliente=6, nome="BANCO BRADESCO S.A.")
        DimCliente.objects.create(id_cliente=77, nome="COOPERATIVA CENTRAL LTDA")
        QualidadeAuditado.objects.create(
            id_cliente=6,
            protocolo="A6",
            data=date(2026, 1, 2),
            source_file="test",
        )
        QualidadeFalha.objects.create(
            id_cliente=77,
            protocolo="F77",
            data=date(2026, 1, 3),
            source_file="test",
        )
        by_id = {row["id_cliente"]: row for row in list_report_clients()}
        self.assertIn(6, by_id)
        self.assertIn(77, by_id)
        self.assertGreater(by_id[6]["eo_auditados"], 0)
        self.assertGreater(by_id[77]["eo_falhas"], 0)

    def test_includes_dimcliente_without_eo_volume(self):
        DimCliente.objects.create(id_cliente=999, nome="BANCO TESTE SA")
        ids = {row["id_cliente"] for row in list_report_clients()}
        self.assertIn(999, ids)

    def test_excludes_eo_orphan_without_dimcliente(self):
        QualidadeAuditado.objects.create(
            id_cliente=83,
            protocolo="X83",
            data=date(2026, 1, 4),
            source_file="test",
        )
        ids = {row["id_cliente"] for row in list_report_clients()}
        self.assertNotIn(83, ids)

    def test_excludes_placeholder_dimcliente_name(self):
        DimCliente.objects.create(id_cliente=34, nome="Cliente 34")
        QualidadeAuditado.objects.create(
            id_cliente=34,
            protocolo="P34",
            data=date(2026, 1, 5),
            source_file="test",
        )
        ids = {row["id_cliente"] for row in list_report_clients()}
        self.assertNotIn(34, ids)
