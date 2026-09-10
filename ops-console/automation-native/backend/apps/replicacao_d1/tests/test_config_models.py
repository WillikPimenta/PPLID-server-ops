# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from django.db import IntegrityError
from django.test import TestCase

from apps.replicacao_d1.models import (
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1RetroativoConfig,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.normalization import normalize_key


class NormalizeKeyTests(TestCase):
    def test_case_and_spaces(self):
        self.assertEqual(normalize_key("  Foo   Bar  "), "foo bar")

    def test_accents_and_unicode_dashes(self):
        self.assertEqual(normalize_key("São Paulo"), "sao paulo")
        self.assertEqual(normalize_key("Doc\u2013Scan"), "doc-scan")
        self.assertEqual(normalize_key("Doc\u2014Scan"), "doc-scan")

    def test_none_and_nan(self):
        self.assertEqual(normalize_key(None), "")
        self.assertEqual(normalize_key(float("nan")), "")

    def test_same_key_for_variants(self):
        variants = ["CLARO", "claro", "  CLARO  ", "Clàro"]
        keys = {normalize_key(v) for v in variants}
        self.assertEqual(len(keys), 1)


class ReplicacaoD1ConfigGeralTests(TestCase):
    def test_get_solo_defaults_fonte_banco_inativa(self):
        cfg = ReplicacaoD1ConfigGeral.get_solo()
        self.assertEqual(cfg.pk, ReplicacaoD1ConfigGeral.SINGLETON_PK)
        self.assertFalse(cfg.fonte_banco_ativa)

    def test_get_solo_is_idempotent(self):
        first = ReplicacaoD1ConfigGeral.get_solo()
        second = ReplicacaoD1ConfigGeral.get_solo()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ReplicacaoD1ConfigGeral.objects.count(), 1)


class ReplicacaoD1RetroativoConfigTests(TestCase):
    def test_get_solo_uses_fixed_smallint_pk(self):
        retro = ReplicacaoD1RetroativoConfig.get_solo()
        self.assertEqual(retro.pk, ReplicacaoD1RetroativoConfig.SINGLETON_PK)
        id_field = ReplicacaoD1RetroativoConfig._meta.get_field("id")
        self.assertEqual(id_field.get_internal_type(), "PositiveSmallIntegerField")

class ReplicacaoD1WorkflowConstraintTests(TestCase):
    def test_chave_normalizada_unique(self):
        ReplicacaoD1Workflow.objects.create(nome_canonico="WF Alpha")
        with self.assertRaises(IntegrityError):
            ReplicacaoD1Workflow.objects.create(nome_canonico="  wf   alpha  ")

    def test_save_sets_chave_and_ativo_from_status(self):
        wf = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Beta",
            status=ReplicacaoD1Workflow.STATUS_PENDENTE,
        )
        self.assertEqual(wf.chave_normalizada, normalize_key("WF Beta"))
        self.assertFalse(wf.ativo)


class ReplicacaoD1LedgerConstraintTests(TestCase):
    def test_idempotency_unique_constraint_fields(self):
        meta = ReplicacaoD1LedgerConsumo._meta
        names = {c.name for c in meta.constraints}
        self.assertIn("replicacao_d1_ledger_idempot_uniq", names)
        constraint = next(c for c in meta.constraints if c.name == "replicacao_d1_ledger_idempot_uniq")
        self.assertEqual(
            list(constraint.fields),
            ["run_id", "competencia", "workflow_chave", "origem"],
        )

    def test_duplicate_ledger_row_raises(self):
        ts = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        ReplicacaoD1LedgerConsumo.objects.create(
            competencia="2026-08",
            run_id="20260805_120000",
            workflow_chave="wf alpha",
            data_execucao=ts,
            protocolos=10,
            origem="confirmado",
        )
        with self.assertRaises(IntegrityError):
            ReplicacaoD1LedgerConsumo.objects.create(
                competencia="2026-08",
                run_id="20260805_120000",
                workflow_chave="wf alpha",
                data_execucao=ts,
                protocolos=5,
                origem="confirmado",
            )
