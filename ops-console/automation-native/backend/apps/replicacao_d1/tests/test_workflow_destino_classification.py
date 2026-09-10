# -*- coding: utf-8 -*-
from __future__ import annotations

from django.test import SimpleTestCase

from apps.replicacao_d1.normalization import (
    WORKFLOW_DESTINO_BIO,
    WORKFLOW_DESTINO_DOC_31,
    WORKFLOW_DESTINO_G_AUDITORIA,
    WORKFLOW_DESTINO_OUTROS,
    WORKFLOW_DESTINO_REDOC,
    WORKFLOW_DESTINO_SEM_INFORMACAO,
    classify_workflow_destino,
)


class WorkflowDestinoClassificationTests(SimpleTestCase):
    def test_classify_g_auditoria_variants(self):
        for raw in ("G Auditoria - G Auditoria", "G Auditoria Destino", "g auditoria destino"):
            key, label = classify_workflow_destino(raw)
            self.assertEqual(key, WORKFLOW_DESTINO_G_AUDITORIA)
            self.assertEqual(label, "G Auditoria")

    def test_classify_doc_31(self):
        for raw in (
            "Documentoscopia 3.1 - Documentoscopia 3.1",
            "Analise Direcionada",
        ):
            key, label = classify_workflow_destino(raw)
            self.assertEqual(key, WORKFLOW_DESTINO_DOC_31)
            self.assertEqual(label, "Analise Direcionada")

    def test_classify_bio(self):
        key, label = classify_workflow_destino("Auditoria Biometria - Auditoria Biometria")
        self.assertEqual(key, WORKFLOW_DESTINO_BIO)
        self.assertEqual(label, "Auditoria Biometria")

    def test_classify_redoc(self):
        key, label = classify_workflow_destino("Auditoria Redoc - Auditoria Redoc")
        self.assertEqual(key, WORKFLOW_DESTINO_REDOC)
        self.assertEqual(label, "Auditoria Redoc")

    def test_classify_outros(self):
        key, _ = classify_workflow_destino("Workflow Customizado XYZ")
        self.assertEqual(key, WORKFLOW_DESTINO_OUTROS)

    def test_classify_sem_informacao(self):
        key, _ = classify_workflow_destino("")
        self.assertEqual(key, WORKFLOW_DESTINO_SEM_INFORMACAO)
