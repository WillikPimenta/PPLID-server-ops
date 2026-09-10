# -*- coding: utf-8 -*-
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.analytics import (
    build_kpis,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version

User = get_user_model()


def _falha(**updates):
    payload = {
        "protocolo": "P-TEST",
        "source_file": "deadline-test",
        "data": date(2026, 8, 1),
        "data_analise": date(2026, 4, 1),
        "tipo_falha": "Manual",
    }
    payload.update(updates)
    return QualidadeFalha.objects.create(**payload)


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
    QUALIDADE_SOURCE_MODE="legacy",
)
class FalhasDataPrazoFilterTests(TestCase):
    def setUp(self):
        QualidadeFalha.objects.filter(source_file="deadline-test").delete()
        QualidadeAuditado.objects.filter(source_file="deadline-test").delete()
        bump_quality_cache_version()

        base_analise = date(2026, 1, 1)
        self.falha_119 = _falha(
            protocolo="P-119",
            data=base_analise + timedelta(days=119),
            data_analise=base_analise,
        )
        self.falha_120 = _falha(
            protocolo="P-120",
            data=base_analise + timedelta(days=120),
            data_analise=base_analise,
        )
        self.falha_121 = _falha(
            protocolo="P-121",
            data=base_analise + timedelta(days=121),
            data_analise=base_analise,
        )
        self.falha_sem_analise = _falha(
            protocolo="P-SEM-ANALISE",
            data=date(2026, 3, 15),
            data_analise=None,
        )
        self.falha_invertida = _falha(
            protocolo="P-INV",
            data=date(2026, 2, 1),
            data_analise=date(2026, 4, 1),
        )
        self.auditado = QualidadeAuditado.objects.create(
            protocolo="P-AUD",
            data=date(2026, 3, 20),
            data_analise=base_analise + timedelta(days=200),
            source_file="deadline-test",
        )

        self.user = User.objects.create_user(
            username=f"prazo-user-{self.id()}",
            password="x",
            email=f"prazo-user-{self.id()}@example.com",
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    params = {
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "metric_mode": "complete",
    }

    def test_auditados_unchanged_with_filter(self):
        off = {**self.params, "exclude_out_of_deadline": "false"}
        on = {**self.params, "exclude_out_of_deadline": "true"}
        self.assertEqual(filtered_auditados(off).count(), filtered_auditados(on).count())
        self.assertEqual(filtered_auditados(on).count(), 1)

    def test_falhas_excludes_only_known_120_plus(self):
        off = {**self.params, "exclude_out_of_deadline": "false"}
        on = {**self.params, "exclude_out_of_deadline": "true"}
        self.assertEqual(filtered_falhas(off).count(), 5)
        kept_ids = set(filtered_falhas(on).values_list("pk", flat=True))
        self.assertEqual(
            kept_ids,
            {
                self.falha_119.pk,
                self.falha_sem_analise.pk,
                self.falha_invertida.pk,
            },
        )

    def test_build_kpis_falhas_drop_auditados_stable(self):
        off = {**self.params, "exclude_out_of_deadline": "false"}
        on = {**self.params, "exclude_out_of_deadline": "true"}
        kpis_off = build_kpis(off)
        kpis_on = build_kpis(on)
        self.assertEqual(kpis_off["auditados"], kpis_on["auditados"])
        self.assertEqual(kpis_off["auditados"], 1)
        self.assertEqual(kpis_off["falhas"], 5)
        self.assertEqual(kpis_on["falhas"], 3)

    def test_api_falhas_list_respects_filter(self):
        query = {
            **self.params,
            "exclude_out_of_deadline": "true",
        }
        res = self.client.get("/api/v1/qualidade/operacional/falhas/", query)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 3)
        auditados = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            query,
        )
        self.assertEqual(auditados.status_code, 200)
        self.assertEqual(auditados.data["count"], 1)

    def test_export_falhas_respects_filter(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/falhas.csv",
            {**self.params, "exclude_out_of_deadline": "true"},
        )
        self.assertEqual(res.status_code, 200)
        body = b"".join(res.streaming_content).decode("utf-8-sig")
        lines = [line for line in body.splitlines() if line.strip()]
        self.assertEqual(len(lines), 4)  # header + 3 falhas
        self.assertIn("P-119", body)
        self.assertNotIn("P-120", body)
        self.assertNotIn("P-121", body)
