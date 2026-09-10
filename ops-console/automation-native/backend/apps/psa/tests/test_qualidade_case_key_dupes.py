# -*- coding: utf-8 -*-
"""Testes do diagnóstico de duplicatas case_key (Console Ops)."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.falhas_criticas.constants import GROUP_BRASILIA, GROUP_GLOBAL, GROUP_SAO_CARLOS
from apps.qualidade_operacional.models import QualidadeFalha
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE
from apps.psa.services.qualidade_case_key_dupes import check_qualidade_case_key_dupes

User = get_user_model()


class QualidadeCaseKeyDupesServiceTests(TestCase):
    def test_detects_duplicate_groups_and_keeps_oldest(self):
        QualidadeFalha.objects.create(
            protocolo="123",
            matricula="c10001a",
            case_key="123|c10001a",
            data=date(2026, 8, 1),
            source_file=INTRANET_SOURCE_FILE,
        )
        QualidadeFalha.objects.create(
            protocolo="123",
            matricula="c10001a",
            case_key="123|c10001a",
            data=date(2026, 7, 1),
            source_file="jul.tsv",
        )
        payload = check_qualidade_case_key_dupes(sample_limit=10)
        self.assertEqual(payload["status"], "attention")
        self.assertEqual(payload["duplicate_groups"], 1)
        self.assertEqual(payload["duplicate_rows"], 1)
        self.assertEqual(len(payload["sample"]), 1)
        sample = payload["sample"][0]
        self.assertEqual(sample["kept_data"], "2026-07-01")
        self.assertEqual(sample["kept_source_file"], "jul.tsv")
        self.assertEqual(len(sample["removed_ids"]), 1)

    def test_ok_when_no_duplicates(self):
        QualidadeFalha.objects.create(
            protocolo="1",
            matricula="c10001a",
            case_key="1|c10001a",
            data=date(2026, 7, 1),
        )
        payload = check_qualidade_case_key_dupes()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["duplicate_groups"], 0)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class QualidadeCaseKeyDupesApiTests(TestCase):
    def setUp(self):
        for name in (GROUP_GLOBAL, GROUP_BRASILIA, GROUP_SAO_CARLOS):
            Group.objects.get_or_create(name=name)
        adm_group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.ops_user = User.objects.create_user(
            "case_key_ops", password="test123", email="case_key_ops@test.local"
        )
        self.ops_user.groups.add(adm_group)
        self.client = Client()

    def test_api_requires_ops_access(self):
        resp = self.client.get("/api/v1/portal-ops/data-quality/qualidade-case-key-dupes/")
        self.assertIn(resp.status_code, {401, 403})

    def test_api_returns_payload_for_ops_user(self):
        self.client.login(username="case_key_ops", password="test123")
        resp = self.client.get("/api/v1/portal-ops/data-quality/qualidade-case-key-dupes/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("duplicate_groups", resp.json())
