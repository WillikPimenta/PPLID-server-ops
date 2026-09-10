# -*- coding: utf-8 -*-
from pathlib import Path

from django.test import TestCase

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.importer import (
    import_qualidade_operacional,
    row_to_falha,
)


class ImporterTests(TestCase):
    def test_processual_is_not_linked_to_agent(self):
        falha = row_to_falha(
            {
                "Protocolo": "PROC-1",
                "Data de Análise": "04/01/2026",
                "Tipo de Falha": "PROCESSUAL",
                "Matrícula": "c90000a",
            },
            source_file="teste.tsv",
        )

        self.assertIsNotNone(falha)
        self.assertEqual(falha.tipo_falha, "PROCESSUAL")
        self.assertEqual(falha.matricula, "")

    def test_import_sample_tsv(self):
        root = Path(__file__).resolve().parent / "fixtures"
        stats = import_qualidade_operacional(root, mode="replace")
        self.assertGreaterEqual(stats.ok, 3)
        self.assertEqual(QualidadeAuditado.objects.count(), 2)
        self.assertEqual(QualidadeFalha.objects.count(), 1)

        aud = QualidadeAuditado.objects.get(protocolo="45008984")
        self.assertEqual(aud.id_cliente, 6)
        self.assertEqual(aud.id_workflow, 144)
        self.assertEqual(aud.matricula, "c93460a")
        self.assertEqual(aud.matricula_auditor, "c92898a")
        self.assertEqual(aud.tipo_conclusao, "Manual")

        auto = QualidadeAuditado.objects.get(protocolo="44386851")
        self.assertEqual(auto.tipo_conclusao, "Automático")

        falha = QualidadeFalha.objects.get(protocolo="178346351")
        self.assertEqual(falha.id_cliente, 83)
        self.assertEqual(falha.id_workflow, 450)
        self.assertEqual(falha.matricula, "c92629a")
        self.assertEqual(falha.localidade, "Brasília")
        self.assertEqual(falha.localidade_documento, "")

    def test_row_to_falha_sets_localidade_documento_from_uf(self):
        falha = row_to_falha(
            {
                "Protocolo": "UF-1",
                "Data de Análise": "04/01/2026",
                "Tipo de Falha": "Manual",
                "UF": "sp",
                "Localidade": "Brasília",
            },
            source_file="teste.tsv",
        )
        self.assertIsNotNone(falha)
        self.assertEqual(falha.localidade_documento, "SP")
        self.assertEqual(falha.uf, "SP")
        self.assertEqual(falha.localidade, "Brasília")

    def test_upsert_replaces_source_file(self):
        root = Path(__file__).resolve().parent / "fixtures"
        import_qualidade_operacional(root, mode="replace")
        first = QualidadeAuditado.objects.count()
        import_qualidade_operacional(root, mode="upsert", only="auditados")
        self.assertEqual(QualidadeAuditado.objects.count(), first)
