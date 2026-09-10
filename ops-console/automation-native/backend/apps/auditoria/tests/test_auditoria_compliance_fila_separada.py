import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaComplianceAuditorPresence,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
    ReinspecaoAuditorPresence,
    ReinspecaoFilaHistorico,
)
from apps.auditoria.services import auditoria_compliance_fila, reinspecao_fila


User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaComplianceFilaSeparadaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.actor = User.objects.create_superuser(
            username="fila_separada_admin",
            email="fila_separada@test.local",
            password="test12345",
        )
        self.auditor = User.objects.create_user(
            username="fila_separada_auditor",
            email="fila_separada_auditor@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.actor)
        self.reinspecao = QualidadePendenteReinspecao.objects.create(
            contexto="reinspecao",
            protocolo="PROTOCOLO-IGUAL",
            usuario="c11111q",
            descricao_irregularidades="Reinspeção",
            data_contestacao=timezone.now(),
        )
        self.compliance = QualidadePendenteAuditoriaCompliance.objects.create(
            protocolo="PROTOCOLO-IGUAL",
            usuario="c22222q",
            descricao_irregularidades="Auditoria Compliance",
            data_analise=timezone.now(),
        )

    def test_endpoints_consultam_tabelas_fisicas_distintas(self):
        reinspecao = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/falhas/"
        )
        compliance = self.client.get(
            "/api/v1/qualidade/auditoria/compliance/falhas/"
        )

        self.assertEqual(reinspecao.status_code, 200, reinspecao.data)
        self.assertEqual(compliance.status_code, 200, compliance.data)
        self.assertEqual(
            {item["id"] for item in reinspecao.data["results"]},
            {self.reinspecao.id},
        )
        self.assertEqual(
            {item["id"] for item in compliance.data["results"]},
            {self.compliance.id},
        )
        self.assertEqual(compliance.data["results"][0]["contexto"], "auditoria_compliance")

    def test_endpoint_legado_rejeita_contexto_de_compliance(self):
        response = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/fila/me/",
            {"contexto": "auditoria_compliance"},
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_presenca_online_e_exclusiva_entre_contextos(self):
        reinspecao_fila.set_auditor_status(
            user=self.auditor,
            status="online",
            actor=self.actor,
        )
        self.assertTrue(
            ReinspecaoAuditorPresence.objects.filter(
                user=self.auditor,
                contexto="reinspecao",
                status="online",
            ).exists()
        )
        self.assertFalse(
            AuditoriaComplianceAuditorPresence.objects.filter(user=self.auditor).exists()
        )

        with self.assertRaises(ValueError) as ctx:
            auditoria_compliance_fila.set_auditor_status(
                user=self.auditor,
                status="online",
                actor=self.actor,
            )
        self.assertIn("Reinspeção", str(ctx.exception))

        reinspecao_fila.set_auditor_status(
            user=self.auditor,
            status="offline",
            actor=self.actor,
        )
        auditoria_compliance_fila.set_auditor_status(
            user=self.auditor,
            status="online",
            actor=self.actor,
        )
        auditoria_compliance_fila.distribuir_protocolos(actor=self.actor)

        self.compliance.refresh_from_db()
        self.reinspecao.refresh_from_db()
        self.assertEqual(self.compliance.responsavel_id, self.auditor.id)
        self.assertIsNone(self.reinspecao.responsavel_id)

        with self.assertRaises(ValueError) as ctx:
            reinspecao_fila.set_auditor_status(
                user=self.auditor,
                status="online",
                actor=self.actor,
            )
        self.assertIn("Auditoria Compliance", str(ctx.exception))

    def test_presence_me_rejeita_online_cruzado(self):
        self.client.force_authenticate(user=self.auditor)
        reinspecao_fila.set_auditor_status(
            user=self.auditor,
            status="online",
            actor=self.actor,
        )

        response = self.client.post(
            "/api/v1/qualidade/auditoria/compliance/presence/me/",
            {"status": "online"},
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Reinspeção", str(response.data.get("errors", {}).get("status", "")))


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaComplianceDataMigrationTests(TestCase):
    def test_copia_dados_e_preserva_origem_legada(self):
        user = User.objects.create_user(username="migration_compliance")
        pending = QualidadePendenteReinspecao.objects.create(
            contexto="auditoria_compliance",
            protocolo="MIGRAR-COMP-1",
            usuario="c33333q",
            descricao_irregularidades="Conferência",
            data_analise=timezone.now(),
            responsavel=user,
        )
        ReinspecaoAuditorPresence.objects.create(
            contexto="auditoria_compliance",
            user=user,
            status="online",
        )
        ReinspecaoFilaHistorico.objects.create(
            contexto="auditoria_compliance",
            pendente=pending,
            auditor=user,
            actor=user,
            tipo=ReinspecaoFilaHistorico.TIPO_DIRECIONAMENTO,
        )

        migration = importlib.import_module(
            "apps.auditoria.migrations.0058_auditoriacomplianceimportstaging_and_more"
        )
        migration.copy_auditoria_compliance_data(apps, None)

        copied = QualidadePendenteAuditoriaCompliance.objects.get(pk=pending.pk)
        self.assertEqual(copied.protocolo, pending.protocolo)
        self.assertEqual(copied.responsavel_id, user.id)
        self.assertTrue(
            AuditoriaComplianceAuditorPresence.objects.filter(
                user=user,
                status="online",
            ).exists()
        )
        self.assertTrue(
            copied.auditoria_compliance_historico.filter(
                tipo=ReinspecaoFilaHistorico.TIPO_DIRECIONAMENTO,
            ).exists()
        )
        self.assertTrue(QualidadePendenteReinspecao.objects.filter(pk=pending.pk).exists())


@override_settings(ACCESS_ENFORCEMENT=False)
@skipUnless(connection.vendor == "postgresql", "Concorrência validada com locks do PostgreSQL.")
class AuditoriaPresenceMutexConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.auditor = User.objects.create_user(
            username="auditor_mutex",
            email="auditor_mutex@test.local",
        )
        self.actor = User.objects.create_user(
            username="actor_mutex",
            email="actor_mutex@test.local",
        )

    def test_online_simultaneo_persiste_em_apenas_uma_fila(self):
        barrier = threading.Barrier(2)

        def go_online(service):
            close_old_connections()
            try:
                auditor = User.objects.get(pk=self.auditor.pk)
                actor = User.objects.get(pk=self.actor.pk)
                barrier.wait(timeout=5)
                service.set_auditor_status(user=auditor, status="online", actor=actor)
                return "online"
            except ValueError:
                return "conflict"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    go_online,
                    (reinspecao_fila, auditoria_compliance_fila),
                )
            )

        self.assertCountEqual(results, ["online", "conflict"])
        online_count = ReinspecaoAuditorPresence.objects.filter(
            user=self.auditor,
            contexto=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
            status=ReinspecaoAuditorPresence.STATUS_ONLINE,
        ).count() + AuditoriaComplianceAuditorPresence.objects.filter(
            user=self.auditor,
            status=AuditoriaComplianceAuditorPresence.STATUS_ONLINE,
        ).count()
        self.assertEqual(online_count, 1)

    def test_primeira_criacao_simultanea_no_mesmo_contexto_e_idempotente(self):
        cases = (
            ("reinspecao-online", reinspecao_fila, "online"),
            ("reinspecao-offline", reinspecao_fila, "offline"),
            ("compliance-online", auditoria_compliance_fila, "online"),
            ("compliance-offline", auditoria_compliance_fila, "offline"),
        )

        for suffix, service, status in cases:
            with self.subTest(contexto=suffix):
                auditor = User.objects.create_user(
                    username=f"auditor_mutex_{suffix}",
                    email=f"auditor_mutex_{suffix}@test.local",
                )
                barrier = threading.Barrier(2)

                def set_status(_):
                    close_old_connections()
                    try:
                        thread_auditor = User.objects.get(pk=auditor.pk)
                        thread_actor = User.objects.get(pk=self.actor.pk)
                        barrier.wait(timeout=5)
                        presence = service.set_auditor_status(
                            user=thread_auditor,
                            status=status,
                            actor=thread_actor,
                        )
                        return presence.status
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(set_status, range(2)))

                self.assertEqual(results, [status, status])
                if service is reinspecao_fila:
                    count = ReinspecaoAuditorPresence.objects.filter(
                        user=auditor,
                        contexto=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
                    ).count()
                else:
                    count = AuditoriaComplianceAuditorPresence.objects.filter(
                        user=auditor,
                    ).count()
                self.assertEqual(count, 1)
