# -*- coding: utf-8 -*-
from __future__ import annotations

import io
from unittest.mock import patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.replicacao_d1.exceptions import ImportPreviewTokenError
from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.services.config_import import apply_import, preview_import

User = get_user_model()
BASE = "/api/v1/replicacao-d1/config"


class ConfigImportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="d1_import", email="d1_import@test.local", password="x"
        )

    def _categoria_bytes(self) -> bytes:
        buf = io.BytesIO()
        pd.DataFrame(
            {
                "Cliente": ["Cliente Import"],
                "Segmento": ["Seg"],
                "Categoria": ["Cat"],
                "Meta Cliente": [100],
            }
        ).to_excel(buf, index=False, engine="openpyxl")
        return buf.getvalue()

    def test_preview_does_not_write(self):
        preview = preview_import("categoria_xlsx", self._categoria_bytes(), "Categoria.xlsx")
        self.assertIn("preview_token", preview)
        self.assertEqual(ReplicacaoD1Cliente.objects.count(), 0)

    @override_settings(ACCESS_ENFORCEMENT=True)
    @patch("apps.access.permissions.user_has_permission", side_effect=lambda user, code, **_kw: code == R.PLANEJAMENTO_AUTOMACAO_CONFIGURE)
    def test_apply_upsert_via_api(self, _mock_perm):
        preview = preview_import("categoria_xlsx", self._categoria_bytes(), "Categoria.xlsx")
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f"{BASE}/import/apply/",
            {"kind": "categoria_xlsx", "preview_token": preview["preview_token"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ReplicacaoD1Cliente.objects.filter(nome="Cliente Import").count(), 1)

    def test_token_mismatch_fails(self):
        preview = preview_import("categoria_xlsx", self._categoria_bytes(), "Categoria.xlsx")
        with self.assertRaises(ImportPreviewTokenError):
            apply_import("default_xlsx", preview["preview_token"], self.user)

    def test_apply_unit(self):
        preview = preview_import("categoria_xlsx", self._categoria_bytes(), "Categoria.xlsx")
        stats = apply_import("categoria_xlsx", preview["preview_token"], self.user)
        self.assertGreaterEqual(stats.get("includes", 0), 1)
        self.assertTrue(ReplicacaoD1Cliente.objects.filter(nome="Cliente Import").exists())

    def test_clientes_csv_is_additive_and_does_not_inactivate_absent_rows(self):
        existente = ReplicacaoD1Cliente.objects.create(nome="Cliente Existente", ativo=True)
        csv_bytes = (
            "nome;segmento_nome;categoria_nome;meta_mensal;ativo\n"
            "Cliente Novo;Varejo;High;250;true\n"
        ).encode("utf-8")

        preview = preview_import("clientes_csv", csv_bytes, "clientes.csv")

        self.assertEqual(preview["counts"]["includes"], 1)
        self.assertEqual(preview["counts"]["to_inactivate"], 0)
        apply_import("clientes_csv", preview["preview_token"], self.user)
        existente.refresh_from_db()
        self.assertTrue(existente.ativo)
        novo = ReplicacaoD1Cliente.objects.get(nome="Cliente Novo")
        self.assertEqual(novo.meta_mensal, 250)

    def test_workflows_csv_upserts_multiple_rows(self):
        cliente = ReplicacaoD1Cliente.objects.create(nome="Cliente CSV")
        csv_bytes = (
            "nome_canonico;nome_d1;nome_selenium;cliente_nome;fila;status;amostra_pct_especial;amostra_100;ativo\n"
            "WF CSV 1;WF D1 1;WF Sel 1;Cliente CSV;G auditoria;ATIVO;25;false;true\n"
            "WF CSV 2;WF D1 2;WF Sel 2;Cliente CSV;3.1;ATIVO;;true;true\n"
        ).encode("utf-8")

        preview = preview_import("workflows_csv", csv_bytes, "workflows.csv")
        stats = apply_import("workflows_csv", preview["preview_token"], self.user)

        self.assertEqual(stats["includes"], 2)
        self.assertEqual(ReplicacaoD1Workflow.objects.filter(cliente=cliente).count(), 2)
        self.assertEqual(ReplicacaoD1Workflow.objects.get(nome_canonico="WF CSV 1").amostra_pct_especial, 25)
        self.assertTrue(ReplicacaoD1Workflow.objects.get(nome_canonico="WF CSV 2").amostra_100)

    def test_duplicate_csv_rows_block_apply(self):
        csv_bytes = "nome;meta_mensal\nDuplicado;10\nduplicado;20\n".encode("utf-8")
        preview = preview_import("clientes_csv", csv_bytes, "clientes.csv")

        self.assertEqual(preview["counts"]["errors"], 1)
        with self.assertRaises(ImportPreviewTokenError):
            apply_import("clientes_csv", preview["preview_token"], self.user)

    def test_escala_csv_imports_multiple_rows_and_accepts_blank_case(self):
        csv_bytes = (
            "data;auditores_ativos;auditores_case\n"
            "2026-08-10;8;3\n"
            "11/08/2026;5;\n"
        ).encode("utf-8")

        preview = preview_import("escala_csv", csv_bytes, "escala.csv")
        stats = apply_import("escala_csv", preview["preview_token"], self.user)

        self.assertEqual(stats["includes"], 2)
        segundo_dia = ReplicacaoD1EscalaDia.objects.get(data="2026-08-11")
        self.assertEqual(segundo_dia.auditores_brflow, 5)
        self.assertEqual(segundo_dia.auditores_case, 5)

    def test_escala_csv_rejects_negative_capacity(self):
        csv_bytes = "data;auditores_ativos\n2026-08-10;-1\n".encode("utf-8")

        with self.assertRaisesRegex(ValueError, "nao pode ser negativo"):
            preview_import("escala_csv", csv_bytes, "escala.csv")
