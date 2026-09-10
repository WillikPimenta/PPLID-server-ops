from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    ReinspecaoGedDivergencia,
    ReinspecaoGedDivergenciaEvento,
)
from apps.common.models import BotDataIngestion, BotDbSyncJob
from apps.workforce.models import Agent
from apps.auditoria.services.reinspecao_ged_divergencias import reconcile_ged_occurrence
from apps.auditoria.views_reinspecao_ged_divergencias import (
    ReinspecaoGedDivergenciaListView,
    ReinspecaoGedDivergenciaReconhecerView,
    ReinspecaoGedDivergenciaResumoView,
)
from apps.access.permissions import HasAnyPortalPermission
from apps.access.registry import QUAL_AUDITORIA_ASSIGN, QUAL_AUDITORIA_CREATE, QUAL_AUDITORIA_VIEW


User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class ReinspecaoGedDivergenciaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="ged_divergencia_admin",
            email="ged-divergencia@test.local",
            password="test12345",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.data_contestacao = timezone.make_aware(datetime(2026, 8, 27, 14, 35, 20))
        self.tratado = self._tratado(self.data_contestacao)

    def _tratado(self, data_contestacao):
        return AuditoriaFalhaCadastro.objects.create(
            protocolo="18426529",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            tipo_falha="reinspecao",
            usuario="c19131q",
            descricao_irregularidades="IC - 175 - CPF ausente",
            data_contestacao=data_contestacao,
        )

    def _reconcile(self, **overrides):
        payload = {
            "contexto": "reinspecao",
            "protocolo": self.tratado.protocolo,
            "descricao_irregularidades": self.tratado.descricao_irregularidades,
            "data_contestacao": self.data_contestacao,
            "ged_elegivel": True,
            "tratado": self.tratado,
            "source_file": "ged.csv",
            "source_hash": "a" * 64,
        }
        payload.update(overrides)
        return reconcile_ged_occurrence(**payload)

    def test_repeated_cycles_update_one_divergence_without_event_storm(self):
        first = self._reconcile()
        second = self._reconcile(source_file="ged-2.csv", source_hash="b" * 64)

        self.assertEqual(first.action, "created")
        self.assertEqual(second.action, "seen")
        self.assertEqual(ReinspecaoGedDivergencia.objects.count(), 1)
        divergencia = ReinspecaoGedDivergencia.objects.get()
        self.assertEqual(divergencia.seen_count, 2)
        self.assertEqual(divergencia.last_source_file, "ged-2.csv")
        self.assertEqual(divergencia.eventos.count(), 1)
        self.assertEqual(divergencia.eventos.get().tipo, "detectada")

    def test_new_contestation_datetime_is_another_occurrence(self):
        self._reconcile()
        nova_data = self.data_contestacao + timedelta(hours=1)
        novo_tratado = self._tratado(nova_data)
        result = self._reconcile(data_contestacao=nova_data, tratado=novo_tratado)

        self.assertEqual(result.action, "created")
        self.assertEqual(ReinspecaoGedDivergencia.objects.count(), 2)
        self.assertEqual(
            ReinspecaoGedDivergencia.objects.values("occurrence_key").distinct().count(),
            2,
        )

    def test_rejects_treated_record_from_another_occurrence(self):
        with self.assertRaisesMessage(ValueError, "nao corresponde a ocorrencia exata"):
            self._reconcile(data_contestacao=self.data_contestacao + timedelta(minutes=1))
        self.assertFalse(ReinspecaoGedDivergencia.objects.exists())

    def test_answered_occurrence_resolves_and_later_open_row_reopens(self):
        created = self._reconcile()
        resolved = self._reconcile(ged_elegivel=False, tratado=None, source_file="answered.csv")
        reopened = self._reconcile(source_file="open-again.csv")

        self.assertEqual(resolved.action, "resolved")
        self.assertEqual(reopened.action, "reopened")
        divergencia = ReinspecaoGedDivergencia.objects.get(pk=created.divergencia.pk)
        self.assertEqual(divergencia.status, ReinspecaoGedDivergencia.STATUS_ABERTA)
        self.assertEqual(divergencia.reopened_count, 1)
        self.assertEqual(divergencia.seen_count, 2)
        self.assertEqual(
            list(divergencia.eventos.order_by("created_at").values_list("tipo", flat=True)),
            ["detectada", "resolvida", "reaberta"],
        )

    def test_absence_from_window_does_not_change_open_status(self):
        created = self._reconcile()

        created.divergencia.refresh_from_db()
        self.assertEqual(created.divergencia.status, ReinspecaoGedDivergencia.STATUS_ABERTA)
        self.assertIsNone(created.divergencia.resolvida_em)

    def test_summary_list_and_acknowledge_endpoints(self):
        auditor = Agent.objects.create(
            user_lan_id="auditor.ged",
            full_name="Maria Auditora",
            active=True,
        )
        self.tratado.auditor = "auditor.ged"
        self.tratado.auditor_ref = auditor
        self.tratado.data_analise_intranet = timezone.make_aware(
            datetime(2026, 8, 27, 15, 10, 0)
        )
        self.tratado.save(
            update_fields=["auditor", "auditor_ref", "data_analise_intranet", "updated_at"]
        )
        created = self._reconcile()

        summary = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/ged-divergencias/resumo/"
        )
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.json()["abertas"], 1)
        self.assertEqual(summary.json()["total_ativas"], 1)
        self.assertTrue(summary.json()["lacuna_cobertura"])

        listing = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/ged-divergencias/",
            {"status": "aberta", "protocolo": "1842"},
        )
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()["total"], 1)
        self.assertEqual(listing.json()["results"][0]["seen_count"], 1)
        self.assertEqual(
            listing.json()["results"][0]["tratado"]["auditor"],
            "Maria Auditora",
        )
        self.assertEqual(
            datetime.fromisoformat(
                listing.json()["results"][0]["tratado"]["tratado_em"]
            ),
            self.tratado.data_analise_intranet,
        )

        acknowledge = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/"
            f"ged-divergencias/{created.divergencia.pk}/reconhecer/",
            {"observacao": "Ciente; aguardando atualizacao no GED."},
            format="json",
        )
        self.assertEqual(acknowledge.status_code, 200, acknowledge.content)
        self.assertTrue(acknowledge.json()["changed"])
        created.divergencia.refresh_from_db()
        self.assertEqual(
            created.divergencia.status,
            ReinspecaoGedDivergencia.STATUS_RECONHECIDA,
        )
        self.assertEqual(
            created.divergencia.eventos.filter(
                tipo=ReinspecaoGedDivergenciaEvento.TIPO_RECONHECIDA
            ).count(),
            1,
        )

        repeated = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/"
            f"ged-divergencias/{created.divergencia.pk}/reconhecer/",
            {},
            format="json",
        )
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertFalse(repeated.json()["changed"])
        self.assertEqual(created.divergencia.eventos.filter(tipo="reconhecida").count(), 1)

    def test_summary_reports_recent_successful_ingestion_as_covered(self):
        BotDataIngestion.objects.create(
            source_key="ged-recent",
            domain=BotDbSyncJob.DOMAIN_REINSPECAO_GED,
            kind="irregularidade",
            reference_date=timezone.localdate(),
            status=BotDataIngestion.STATUS_COMPLETED,
            finished_at=timezone.now(),
        )

        summary = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/ged-divergencias/resumo/"
        )

        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertFalse(summary.json()["lacuna_cobertura"])
        self.assertIsNotNone(summary.json()["ultima_ingestao_em"])

    def test_active_filter_excludes_resolved_and_summary_counts_acknowledged(self):
        aberta = self._reconcile()
        nova_data = self.data_contestacao + timedelta(hours=1)
        novo_tratado = self._tratado(nova_data)
        resolvida = self._reconcile(data_contestacao=nova_data, tratado=novo_tratado)
        self._reconcile(
            data_contestacao=nova_data,
            ged_elegivel=False,
            tratado=None,
        )

        acknowledge = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/"
            f"ged-divergencias/{aberta.divergencia.pk}/reconhecer/",
            {},
            format="json",
        )
        self.assertEqual(acknowledge.status_code, 200, acknowledge.content)

        summary = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/ged-divergencias/resumo/"
        )
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.json()["abertas"], 0)
        self.assertEqual(summary.json()["reconhecidas"], 1)
        self.assertEqual(summary.json()["resolvidas"], 1)
        self.assertEqual(summary.json()["total_ativas"], 1)

        listing = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/ged-divergencias/",
            {"status": "ativas"},
        )
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()["total"], 1)
        self.assertEqual(listing.json()["results"][0]["id"], aberta.divergencia.pk)
        self.assertNotEqual(listing.json()["results"][0]["id"], resolvida.divergencia.pk)

    def test_api_permissions_follow_existing_reinspecao_contract(self):
        expected_read_permissions = (
            QUAL_AUDITORIA_VIEW,
            QUAL_AUDITORIA_CREATE,
            QUAL_AUDITORIA_ASSIGN,
        )
        self.assertEqual(
            ReinspecaoGedDivergenciaListView.portal_permissions,
            expected_read_permissions,
        )
        self.assertEqual(
            ReinspecaoGedDivergenciaResumoView.portal_permissions,
            expected_read_permissions,
        )
        self.assertIn(HasAnyPortalPermission, ReinspecaoGedDivergenciaListView.permission_classes)
        self.assertEqual(
            ReinspecaoGedDivergenciaReconhecerView.portal_permission,
            QUAL_AUDITORIA_ASSIGN,
        )
