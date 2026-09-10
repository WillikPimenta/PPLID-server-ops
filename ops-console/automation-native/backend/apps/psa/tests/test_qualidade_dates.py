# -*- coding: utf-8 -*-
"""Testes do diagnóstico de datas futuras (Console Ops)."""
from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.falhas_criticas.constants import GROUP_BRASILIA, GROUP_GLOBAL, GROUP_SAO_CARLOS
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.psa.services.qualidade_dates import check_qualidade_future_dates

User = get_user_model()


class QualidadeFutureDatesServiceTests(TestCase):
    def test_detects_strictly_future_and_not_today(self):
        today = timezone.localdate()
        QualidadeAuditado.objects.create(
            data=today,
            data_analise=today + timedelta(days=1),
            protocolo="TODAY",
            tipo_analise="Auditoria Compliance",
            source_file="imp.csv",
        )
        QualidadeFalha.objects.create(
            data=today + timedelta(days=2),
            data_analise=today,
            protocolo="FUT",
            tipo_analise="Auditoria Compliance",
            tipo_falha="Manual",
            source_file="imp.csv",
        )
        payload = check_qualidade_future_dates(sample_limit=10)
        self.assertEqual(payload["reference_date"], today.isoformat())
        self.assertEqual(payload["status"], "attention")
        by_key = {(f["table"], f["field"]): f for f in payload["fields"]}
        self.assertEqual(by_key[("qualidade_auditado", "data")]["status"], "ok")
        self.assertEqual(by_key[("qualidade_auditado", "data")]["future_count"], 0)
        self.assertEqual(by_key[("qualidade_auditado", "data_analise")]["future_count"], 1)
        self.assertEqual(by_key[("qualidade_falha", "data")]["future_count"], 1)
        sample = by_key[("qualidade_falha", "data")]["sample"][0]
        self.assertIn("id", sample)
        self.assertIn("source_file", sample)
        self.assertIn("tipo_analise", sample)
        self.assertNotIn("matricula", sample)
        self.assertNotIn("nome", sample)
        # Sem alteração
        self.assertEqual(QualidadeFalha.objects.count(), 1)

    def test_sample_limit_respected(self):
        today = timezone.localdate()
        for i in range(5):
            QualidadeFalha.objects.create(
                data=today + timedelta(days=1 + i),
                data_analise=today,
                protocolo=f"F{i}",
                source_file="x.csv",
            )
        payload = check_qualidade_future_dates(sample_limit=2)
        field = next(f for f in payload["fields"] if f["table"] == "qualidade_falha" and f["field"] == "data")
        self.assertEqual(field["future_count"], 5)
        self.assertEqual(len(field["sample"]), 2)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class QualidadeFutureDatesApiTests(TestCase):
    def setUp(self):
        for name in (GROUP_GLOBAL, GROUP_BRASILIA, GROUP_SAO_CARLOS):
            Group.objects.get_or_create(name=name)
        adm_group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.ops_user = User.objects.create_user(
            "qd_ops", password="test123", email="qd_ops@test.local"
        )
        self.ops_user.groups.add(adm_group)
        self.bsb = User.objects.create_user(
            "qd_bsb", password="test123", email="qd_bsb@test.local"
        )
        self.bsb.groups.add(Group.objects.get(name=GROUP_BRASILIA))
        self.client = Client()

    def test_requires_permission(self):
        self.client.force_login(self.bsb)
        resp = self.client.get(
            "/api/v1/portal-ops/data-quality/qualidade-dates/",
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 403)

    def test_ok_for_console_ops_role(self):
        today = timezone.localdate()
        QualidadeFalha.objects.create(
            data=today + timedelta(days=3),
            data_analise=date(2026, 1, 1),
            protocolo="X",
            source_file="a.csv",
            tipo_analise="Auditoria Compliance",
        )
        self.client.force_login(self.ops_user)
        resp = self.client.get(
            "/api/v1/portal-ops/data-quality/qualidade-dates/",
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "attention")
        self.assertIn("fields", data)
        self.assertEqual(data["reference_date"], today.isoformat())
        self.assertIn("note", data)
