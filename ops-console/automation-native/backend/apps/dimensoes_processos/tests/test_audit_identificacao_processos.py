# -*- coding: utf-8 -*-
"""Testes do comando audit_identificacao_processos e cleanup de vigência."""

from __future__ import annotations

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.dimensoes_processos.models import DimCliente, DimNivelHierarquico, DimWorkflow, ProjecaoSla
from apps.dimensoes_processos.services.identificacao_processos.cleanup_vigencia import (
    cleanup_duplicate_vigentes,
    list_duplicate_vigente_groups,
)


class AuditIdentificacaoProcessosTests(TestCase):
    def setUp(self):
        self.cliente = DimCliente.objects.create(id_cliente=1, nome="C1", operations=1)
        self.nh = DimNivelHierarquico.objects.create(id_nh=1, nome="NH1", ind_considerar=1)
        self.workflow = DimWorkflow.objects.create(id_workflow=1, nome="WF1", ind_considerar=1)

    def test_lists_duplicate_vigente_sla_groups(self):
        for start in (date(2026, 1, 1), date(2026, 6, 1)):
            ProjecaoSla.objects.create(
                cliente=self.cliente,
                workflow=self.workflow,
                nivel_hierarquico=self.nh,
                dias_semana="{0..4}",
                data_inicio=start,
                volume=10,
            )
        groups = list_duplicate_vigente_groups()
        self.assertEqual(groups["total_groups"], 1)
        self.assertEqual(groups["total_rows_to_finalize"], 1)

    def test_cleanup_duplicate_vigentes_dry_run(self):
        older = ProjecaoSla.objects.create(
            cliente=self.cliente,
            workflow=self.workflow,
            nivel_hierarquico=self.nh,
            dias_semana="{0..4}",
            data_inicio=date(2026, 1, 1),
            volume=10,
        )
        newer = ProjecaoSla.objects.create(
            cliente=self.cliente,
            workflow=self.workflow,
            nivel_hierarquico=self.nh,
            dias_semana="{0..4}",
            data_inicio=date(2026, 6, 1),
            volume=20,
        )
        result = cleanup_duplicate_vigentes(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["actions"]), 1)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertIsNone(older.data_fim)
        self.assertIsNone(newer.data_fim)

    def test_cleanup_duplicate_vigentes_apply(self):
        older = ProjecaoSla.objects.create(
            cliente=self.cliente,
            workflow=self.workflow,
            nivel_hierarquico=self.nh,
            dias_semana="{0..4}",
            data_inicio=date(2026, 1, 1),
            volume=10,
        )
        ProjecaoSla.objects.create(
            cliente=self.cliente,
            workflow=self.workflow,
            nivel_hierarquico=self.nh,
            dias_semana="{0..4}",
            data_inicio=date(2026, 6, 1),
            volume=20,
        )
        cleanup_duplicate_vigentes(dry_run=False)
        older.refresh_from_db()
        self.assertEqual(older.data_fim, date(2026, 5, 31))
        self.assertEqual(list_duplicate_vigente_groups()["total_groups"], 0)

    def test_audit_command_json_skip_xlsx(self):
        out = StringIO()
        call_command("audit_identificacao_processos", "--skip-xlsx", "--json", stdout=out)
        payload = out.getvalue()
        self.assertIn("duplicate_vigentes", payload)
        self.assertIn("healthy", payload)
