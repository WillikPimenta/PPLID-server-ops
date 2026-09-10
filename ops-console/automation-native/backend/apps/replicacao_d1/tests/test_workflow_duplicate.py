# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.replicacao_d1.models import ReplicacaoD1Workflow
from apps.replicacao_d1.services.workflow_duplicate import (
    duplicate_workflow,
    find_conflicting_destino,
    suggest_duplicate_nome_canonico,
)

User = get_user_model()
BASE = "/api/v1/replicacao-d1/config"


def _user_has_permission(user, code, **_kwargs):
    if user.username == "d1_config" and code in (
        R.PLANEJAMENTO_AUTOMACAO_VIEW,
        R.PLANEJAMENTO_AUTOMACAO_CONFIGURE,
    ):
        return True
    return False


def _can_configure(user):
    return user.username == "d1_config"


@override_settings(ACCESS_ENFORCEMENT=True)
class WorkflowDuplicateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="d1_config",
            email="d1_config@test.local",
            password="x",
        )
        self.client.force_authenticate(self.user)
        self.source = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Cliente A",
            nome_d1="WF Cliente A D1",
            nome_selenium="WF Cliente A Sel",
            fila="G auditoria",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_duplicate_workflow_api_bio(self, _mock_perm, _mock_cfg):
        resp = self.client.post(
            f"{BASE}/workflows/{self.source.pk}/duplicate/",
            {"fila": "Bio"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["fila"], "Bio")
        self.assertEqual(resp.data["nome_d1"], "WF Cliente A D1")
        self.assertEqual(resp.data["workflow_origem"], self.source.pk)
        self.assertIn("[Bio]", resp.data["nome_canonico"])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_duplicate_redoc_requires_regra(self, _mock_perm, _mock_cfg):
        resp = self.client.post(
            f"{BASE}/workflows/{self.source.pk}/duplicate/",
            {"fila": "Redoc"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nome_regra_brflow", resp.data)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_duplicate_same_destino_rejected(self, _mock_perm, _mock_cfg):
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Cliente A [Bio]",
            nome_d1="WF Cliente A D1",
            nome_selenium="WF Cliente A Sel",
            fila="Bio",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        resp = self.client.post(
            f"{BASE}/workflows/{self.source.pk}/duplicate/",
            {"fila": "Bio"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("detail", resp.data)

    def test_find_conflicting_destino_same_fila(self):
        dup = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Cliente A [Bio]",
            nome_d1="WF Cliente A D1",
            nome_selenium="WF Cliente A Sel",
            fila="Bio",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        conflict = find_conflicting_destino(
            chave_d1=dup.chave_d1_normalizada,
            fila="Bio",
            nome_regra_brflow="",
            nome_selenium="WF Cliente A Sel",
        )
        self.assertEqual(conflict.pk, dup.pk)

    def test_suggest_duplicate_nome_unique(self):
        name = suggest_duplicate_nome_canonico(self.source, fila="Bio")
        duplicate_workflow(self.source, fila="Bio", user=self.user)
        name2 = suggest_duplicate_nome_canonico(self.source, fila="Bio")
        self.assertNotEqual(name, name2)
        self.assertIn("#2", name2)
