# -*- coding: utf-8 -*-
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_ADM_PORTAL,
    ROLE_PLAN_ANALISTA,
    ROLE_QUAL_GERENCIA,
    ROLE_QUAL_USUARIO,
    role_group_name,
)
from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.intranet_source import SyncReport

User = get_user_model()
TZ_SP = ZoneInfo("America/Sao_Paulo")


def _assign_role(user, role: str) -> None:
    Group.objects.get_or_create(name=role_group_name(role))
    user.groups.add(Group.objects.get(name=role_group_name(role)))


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class IntranetSyncApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="qo_admin", password="x", email="qo_admin@example.com"
        )
        _assign_role(self.admin, ROLE_ADM_PORTAL)
        self.sync_client = APIClient()
        self.sync_client.force_authenticate(user=self.admin)

        self.gerencia = User.objects.create_user(
            username="qo_gerencia", password="x", email="qo_gerencia@example.com"
        )
        _assign_role(self.gerencia, ROLE_QUAL_GERENCIA)
        self.gerencia_client = APIClient()
        self.gerencia_client.force_authenticate(user=self.gerencia)

        self.view_user = User.objects.create_user(
            username="qo_view", password="x", email="qo_view@example.com"
        )
        _assign_role(self.view_user, ROLE_QUAL_USUARIO)
        self.view_client = APIClient()
        self.view_client.force_authenticate(user=self.view_user)

        DimCliente.objects.create(id_cliente=10, nome="Cliente Sync")
        DimWorkflow.objects.create(id_workflow=20, nome="WF Sync")

    def test_intranet_sync_denied_without_sync_permission(self):
        res = self.view_client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {"full": True},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_intranet_sync_denied_for_qual_gerencia_even_with_sync_perm(self):
        res = self.gerencia_client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {"full": True},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_intranet_sync_denied_plan_analyst_without_sync(self):
        user = User.objects.create_user(
            username="plan_qo", password="x", email="plan_qo@example.com"
        )
        _assign_role(user, ROLE_PLAN_ANALISTA)
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {"full": True},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    @override_settings(QUALIDADE_INTRANET_SOURCE_ENABLED=False)
    def test_intranet_sync_rejected_when_source_disabled(self):
        res = self.sync_client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {"full": True},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"], "intranet_disabled")

    @patch("apps.qualidade_operacional.services.intranet_source.sync_queryset")
    def test_intranet_sync_returns_report_payload(self, sync_queryset_mock):
        report = SyncReport(processed=3, created=2, updated=1)
        sync_queryset_mock.return_value = report

        res = self.sync_client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {"full": True},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])
        self.assertTrue(res.data["full"])
        self.assertEqual(res.data["processed"], 3)
        self.assertEqual(res.data["created"], 2)
        sync_queryset_mock.assert_called_once()
        _, kwargs = sync_queryset_mock.call_args
        self.assertTrue(kwargs.get("force"))

    @patch("apps.qualidade_operacional.services.intranet_source.sync_queryset")
    def test_intranet_sync_defaults_full_true(self, sync_queryset_mock):
        sync_queryset_mock.return_value = SyncReport()

        res = self.sync_client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["full"])
        _, kwargs = sync_queryset_mock.call_args
        self.assertTrue(kwargs.get("force"))

    def test_intranet_sync_projects_eligible_source(self):
        source = AuditoriaFalhaCadastro.objects.create(
            protocolo="SYNC-API-1",
            cliente="Cliente Sync",
            tipo_falha="Sem Falha",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=timezone.now(),
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
            data_resposta=datetime(2026, 8, 10, 12, 0, tzinfo=TZ_SP),
            usuario="c90001a",
            created_by=self.admin,
            brflow_parsed={
                "data_analise": "2026-08-10",
                "cliente": "Cliente Sync",
                "workflow": "WF Sync",
            },
        )

        res = self.sync_client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {"full": True},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(res.data["processed"], 1)
        self.assertEqual(QualidadeAuditado.objects.filter(protocolo=source.protocolo).count(), 1)
        self.assertEqual(QualidadeFalha.objects.filter(protocolo=source.protocolo).count(), 0)

        # Idempotente: segunda chamada incrementa unchanged, não duplica fatos.
        before = QualidadeAuditado.objects.count()
        res2 = self.sync_client.post(
            "/api/v1/qualidade/operacional/intranet-sync/",
            {"full": False},
            format="json",
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(QualidadeAuditado.objects.count(), before)
        self.assertGreaterEqual(res2.data["unchanged"], 1)
