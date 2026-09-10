# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from datetime import date
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1Run,
    ReplicacaoD1Workflow,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.services.config_export import build_config_csv
from apps.replicacao_d1.services.config_import import apply_import, preview_import
from apps.replicacao_d1.services.config_snapshot import compute_config_hash, load_persistent_config
from apps.replicacao_d1.services.planning_adapter import (
    COL_USAR_ARQUIVO_CSV,
    snapshot_to_dataframes,
)
from apps.replicacao_d1.services.workflow_duplicate import duplicate_workflow

User = get_user_model()
BASE = "/api/v1/replicacao-d1/config"


def _user_has_permission(user, code, **_kwargs):
    return user.username == "mode_config" and code in (
        R.PLANEJAMENTO_AUTOMACAO_VIEW,
        R.PLANEJAMENTO_AUTOMACAO_CONFIGURE,
    )


def _can_configure(user):
    return user.username == "mode_config"


class ReplicationModeSchemaTests(TestCase):
    def test_defaults_are_protocolos_for_new_records(self):
        workflow = ReplicacaoD1Workflow.objects.create(nome_canonico="WF Novo")
        run = ReplicacaoD1Run.objects.create(
            run_id="mode_defaults",
            data_referencia_d1=date(2026, 8, 19),
        )
        workflow_dia = ReplicacaoD1WorkflowDia.objects.create(
            run=run,
            data_referencia_d1=run.data_referencia_d1,
            workflow_config="WF Novo",
        )

        self.assertTrue(workflow.usar_arquivo_csv)
        self.assertEqual(workflow_dia.modo_replicacao, ReplicacaoD1WorkflowDia.MODO_PROTOCOLOS)

    def test_data_migration_backfills_all_legacy_queues(self):
        workflows = {
            fila: ReplicacaoD1Workflow.objects.create(
                nome_canonico=f"WF {fila}",
                fila=fila,
                usar_arquivo_csv=False,
            )
            for fila in ("G auditoria", "3.1", "Bio", " Redoc ")
        }
        run = ReplicacaoD1Run.objects.create(
            run_id="mode_backfill",
            data_referencia_d1=date(2026, 8, 19),
        )
        workflows_dia = {
            fila: ReplicacaoD1WorkflowDia.objects.create(
                run=run,
                data_referencia_d1=run.data_referencia_d1,
                workflow_config=f"WF Dia {fila}",
                fila=fila,
                modo_replicacao=ReplicacaoD1WorkflowDia.MODO_QTD,
            )
            for fila in ("G auditoria", "3.1", "Bio", " Redoc ")
        }

        migration = importlib.import_module(
            "apps.replicacao_d1.migrations.0022_workflow_modo_replicacao"
        )
        migration.backfill_modo_replicacao(django_apps, None)

        for fila, workflow in workflows.items():
            workflow.refresh_from_db()
            self.assertEqual(
                workflow.usar_arquivo_csv,
                fila.strip().casefold() not in ("bio", "redoc"),
            )
        for fila, workflow_dia in workflows_dia.items():
            workflow_dia.refresh_from_db()
            esperado = "qtd" if fila.strip().casefold() in ("bio", "redoc") else "protocolos"
            self.assertEqual(workflow_dia.modo_replicacao, esperado)


@override_settings(ACCESS_ENFORCEMENT=True)
class ReplicationModeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="mode_config", password="x")
        self.client.force_authenticate(self.user)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_api_create_read_and_patch_toggle(self, _mock_perm, _mock_cfg):
        create = self.client.post(
            f"{BASE}/workflows/",
            {
                "nome_canonico": "WF Toggle API",
                "fila": "G auditoria",
                "usar_arquivo_csv": False,
                "status": ReplicacaoD1Workflow.STATUS_ATIVO,
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        self.assertFalse(create.data["usar_arquivo_csv"])
        workflow_id = create.data["id"]
        version_before = ReplicacaoD1ConfigGeral.get_solo().config_version

        detail = self.client.get(f"{BASE}/workflows/{workflow_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.data["usar_arquivo_csv"])

        patched = self.client.patch(
            f"{BASE}/workflows/{workflow_id}/",
            {"usar_arquivo_csv": True},
            format="json",
        )
        self.assertEqual(patched.status_code, 200)
        self.assertTrue(patched.data["usar_arquivo_csv"])
        self.assertGreater(ReplicacaoD1ConfigGeral.get_solo().config_version, version_before)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_redoc_requires_rule_in_both_modes(self, _mock_perm, _mock_cfg):
        for usar_arquivo_csv in (False, True):
            response = self.client.post(
                f"{BASE}/workflows/",
                {
                    "nome_canonico": f"WF Redoc {usar_arquivo_csv}",
                    "fila": "Redoc",
                    "usar_arquivo_csv": usar_arquivo_csv,
                    "status": ReplicacaoD1Workflow.STATUS_ATIVO,
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("nome_regra_brflow", response.data)


class ReplicationModeConfigFlowTests(TestCase):
    def setUp(self):
        ReplicacaoD1ConfigGeral.get_solo()
        self.cliente = ReplicacaoD1Cliente.objects.create(nome="Cliente Modo", meta_mensal=500)
        self.workflow = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Modo",
            nome_d1="WF Modo D1",
            nome_selenium="WF Modo Selenium",
            fila="G auditoria",
            cliente=self.cliente,
            usar_arquivo_csv=False,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )

    def test_snapshot_hash_and_planning_dataframe_include_toggle(self):
        cfg = load_persistent_config()
        payload = cfg.to_dict()
        self.assertFalse(payload["workflows"][0]["usar_arquivo_csv"])
        hash_qtd = compute_config_hash({"persistent": payload})

        snapshot = type("Snapshot", (), {"persistent": payload})()
        mapa = snapshot_to_dataframes(snapshot)["mapa_workflow_d1"]
        self.assertIn(COL_USAR_ARQUIVO_CSV, mapa.columns)
        self.assertFalse(bool(mapa.iloc[0][COL_USAR_ARQUIVO_CSV]))

        self.workflow.usar_arquivo_csv = True
        self.workflow.save(update_fields=["usar_arquivo_csv", "updated_at"])
        hash_protocolos = compute_config_hash({"persistent": load_persistent_config().to_dict()})
        self.assertNotEqual(hash_qtd, hash_protocolos)

    def test_export_and_duplicate_preserve_toggle(self):
        _filename, content = build_config_csv("workflows")
        header = content.lstrip("\ufeff").splitlines()[0].split(";")
        self.assertIn("usar_arquivo_csv", header)
        self.assertIn("false", content)

        # A fila 3.1 teria fallback legado para CSV; a copia deve manter o
        # valor explicitamente desligado na origem.
        duplicate = duplicate_workflow(self.workflow, fila="3.1")
        self.assertFalse(duplicate.usar_arquivo_csv)

    def test_import_explicit_toggle_accepts_boolean_aliases(self):
        user = User.objects.create_user(username="mode_import", password="x")
        csv_bytes = (
            "nome_canonico;nome_d1;nome_selenium;cliente_nome;fila;status;usar_arquivo_csv\n"
            "WF CSV Sim;WF CSV Sim;WF CSV Sim;Cliente Modo;Bio;ATIVO;sim\n"
            "WF CSV Nao;WF CSV Nao;WF CSV Nao;Cliente Modo;G auditoria;ATIVO;nÃ£o\n"
        ).encode("utf-8")

        preview = preview_import("workflows_csv", csv_bytes, "workflows.csv")
        apply_import("workflows_csv", preview["preview_token"], user)

        self.assertTrue(ReplicacaoD1Workflow.objects.get(nome_canonico="WF CSV Sim").usar_arquivo_csv)
        self.assertFalse(ReplicacaoD1Workflow.objects.get(nome_canonico="WF CSV Nao").usar_arquivo_csv)

    def test_legacy_import_preserves_existing_and_falls_back_for_includes(self):
        user = User.objects.create_user(username="mode_import_legacy", password="x")
        csv_bytes = (
            "nome_canonico;nome_d1;nome_selenium;cliente_nome;fila;status\n"
            "WF Modo;WF Modo D1 Atualizado;WF Modo Selenium;Cliente Modo;G auditoria;ATIVO\n"
            "WF Bio Legado;WF Bio Legado;WF Bio Legado;Cliente Modo;Bio;ATIVO\n"
            "WF G Legado;WF G Legado;WF G Legado;Cliente Modo;G auditoria;ATIVO\n"
        ).encode("utf-8")

        preview = preview_import("workflows_csv", csv_bytes, "workflows.csv")
        apply_import("workflows_csv", preview["preview_token"], user)

        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.nome_d1, "WF Modo D1 Atualizado")
        self.assertFalse(self.workflow.usar_arquivo_csv)
        self.assertFalse(
            ReplicacaoD1Workflow.objects.get(nome_canonico="WF Bio Legado").usar_arquivo_csv
        )
        self.assertTrue(
            ReplicacaoD1Workflow.objects.get(nome_canonico="WF G Legado").usar_arquivo_csv
        )
