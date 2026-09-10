# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db import IntegrityError
from django.test import TestCase

from apps.replicacao_d1.models import ReplicacaoD1Workflow
from apps.replicacao_d1.services.workflows_pending import (
    get_or_create_workflow_pendente,
    workflows_ativos_para_planejamento,
)


class WorkflowsPendingTests(TestCase):
    def test_get_or_create_idempotent(self):
        wf1, created1 = get_or_create_workflow_pendente("WF Novo BI")
        wf2, created2 = get_or_create_workflow_pendente("  wf   novo bi  ")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(wf1.pk, wf2.pk)
        self.assertEqual(wf1.status, ReplicacaoD1Workflow.STATUS_PENDENTE)
        self.assertFalse(wf1.ativo)

    def test_pending_not_in_planning_list(self):
        get_or_create_workflow_pendente("WF Pendente")
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Ativo",
            nome_d1="d1",
            nome_selenium="sel",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ativos = list(workflows_ativos_para_planejamento().values_list("nome_canonico", flat=True))
        self.assertIn("WF Ativo", ativos)
        self.assertNotIn("WF Pendente", ativos)

    def test_integrity_error_race_returns_existing(self):
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Race",
            chave_normalizada="wf race",
            status=ReplicacaoD1Workflow.STATUS_PENDENTE,
            ativo=False,
        )
        wf, created = get_or_create_workflow_pendente("WF Race")
        self.assertFalse(created)
        self.assertEqual(wf.nome_canonico, "WF Race")
