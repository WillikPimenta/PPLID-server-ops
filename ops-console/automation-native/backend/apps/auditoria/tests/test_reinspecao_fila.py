from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    role_group_name,
)

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaCompliance,
    QualidadeAnaliseOrigem,
    QualidadePendenteReinspecao,
    ReinspecaoAuditorPresence,
    ReinspecaoFilaHistorico,
)
from apps.auditoria.services.reinspecao_fila import (
    ativos_por_auditor,
    direcionar_protocolo,
    distribuir_protocolos,
    set_auditor_status,
)

User = get_user_model()


def _make_falha(*, protocolo: str, data_contestacao, **kwargs) -> QualidadePendenteReinspecao:
    tipo_falha = kwargs.pop("tipo_falha", "reinspecao")
    return QualidadePendenteReinspecao.objects.create(
        protocolo=protocolo,
        usuario="c19131q",
        tipo_falha=tipo_falha,
        descricao_irregularidades="teste",
        data_contestacao=data_contestacao,
        analise_status=QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO,
        **kwargs,
    )


@override_settings(ACCESS_ENFORCEMENT=False)
class ReinspecaoFilaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.actor = User.objects.create_superuser(
            username="fila_admin",
            email="fila_admin@test.local",
            password="test12345",
        )
        self.a1 = User.objects.create_user(
            username="auditor1",
            email="auditor1@test.local",
            password="test12345",
            first_name="Ana",
        )
        self.a2 = User.objects.create_user(
            username="auditor2",
            email="auditor2@test.local",
            password="test12345",
            first_name="Bruno",
        )
        self.client.force_authenticate(user=self.actor)
        now = timezone.now()
        self.f_old = _make_falha(protocolo="100", data_contestacao=now - timedelta(days=3))
        self.f_mid = _make_falha(protocolo="200", data_contestacao=now - timedelta(days=2))
        self.f_new = _make_falha(protocolo="300", data_contestacao=now - timedelta(days=1))

    def test_offline_does_not_receive(self):
        set_auditor_status(user=self.a1, status="offline", actor=self.actor)
        result = distribuir_protocolos(actor=self.actor)
        self.assertEqual(result["assigned"], 0)
        self.assertEqual(result["online_auditors"], 0)

    def test_online_receives_one_each_oldest_first(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        set_auditor_status(user=self.a2, status="online", actor=self.actor)
        result = distribuir_protocolos(actor=self.actor)
        # Máx. 1 protocolo ativo por auditor → 2 atribuídos, 1 permanece na fila geral.
        self.assertEqual(result["assigned"], 2)
        self.assertEqual(result["remaining"], 1)

        self.f_old.refresh_from_db()
        self.f_mid.refresh_from_db()
        self.f_new.refresh_from_db()
        self.assertIsNotNone(self.f_old.responsavel_id)
        self.assertEqual(self.f_old.analise_status, QualidadePendenteReinspecao.ANALISE_AGUARDANDO)
        self.assertIsNotNone(self.f_mid.responsavel_id)
        self.assertIsNone(self.f_new.responsavel_id)

        owners = {self.f_old.responsavel_id, self.f_mid.responsavel_id}
        self.assertEqual(owners, {self.a1.id, self.a2.id})

        hist = ReinspecaoFilaHistorico.objects.filter(tipo=ReinspecaoFilaHistorico.TIPO_ATRIBUICAO_AUTO)
        self.assertEqual(hist.count(), 2)

    def test_no_double_assign(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        set_auditor_status(user=self.a2, status="online", actor=self.actor)
        distribuir_protocolos(actor=self.actor)
        again = distribuir_protocolos(actor=self.actor)
        self.assertEqual(again["assigned"], 0)
        self.assertEqual(
            QualidadePendenteReinspecao.objects.filter(responsavel__isnull=False).count(),
            2,
        )

    def test_ausente_releases_queue(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="manual",
        )
        self.f_old.refresh_from_db()
        self.assertEqual(self.f_old.responsavel_id, self.a1.id)

        set_auditor_status(user=self.a1, status="ausente", actor=self.actor)
        self.f_old.refresh_from_db()
        self.assertIsNone(self.f_old.responsavel_id)
        self.assertEqual(self.f_old.analise_status, QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO)

    def test_offline_releases_queue(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="manual",
        )
        self.f_old.refresh_from_db()
        self.assertEqual(self.f_old.responsavel_id, self.a1.id)

        set_auditor_status(user=self.a1, status="offline", actor=self.actor)
        self.f_old.refresh_from_db()
        self.assertIsNone(self.f_old.responsavel_id)
        self.assertEqual(self.f_old.analise_status, QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO)

    def test_direcionar_fills_second_slot(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="primeiro",
        )
        second = direcionar_protocolo(
            falha_id=self.f_mid.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="segundo",
        )
        self.f_old.refresh_from_db()
        self.f_mid.refresh_from_db()
        self.assertEqual(self.f_old.responsavel_id, self.a1.id)
        self.assertEqual(second.responsavel_id, self.a1.id)
        self.assertEqual(self.f_mid.fila_origem, QualidadePendenteReinspecao.FILA_ORIGEM_DIRECIONADO)
        self.assertEqual(ativos_por_auditor().get(self.a1.id), 2)

        with self.assertRaises(ValueError):
            direcionar_protocolo(
                falha_id=self.f_new.id,
                auditor_id=self.a1.id,
                actor=self.actor,
                justificativa="terceiro",
            )

    def test_fila_timeout_releases_to_general(self):
        from datetime import timedelta

        from apps.auditoria.services.reinspecao_fila import (
            FILA_TIMEOUT_SECONDS,
            claim_next_for_auditor,
            expire_stale_fila_assignments,
        )

        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="timeout",
        )
        self.f_old.refresh_from_db()
        self.f_old.atribuido_em = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS + 5)
        self.f_old.save(update_fields=["atribuido_em"])

        released = expire_stale_fila_assignments(actor=self.actor)
        self.assertGreaterEqual(released, 1)
        self.f_old.refresh_from_db()
        self.assertIsNone(self.f_old.responsavel_id)
        self.assertEqual(self.f_old.analise_status, QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO)

        # Não reatribui o mesmo protocolo imediatamente ao auditor que sofreu timeout.
        claimed = claim_next_for_auditor(user=self.a1)
        self.f_old.refresh_from_db()
        self.assertIsNone(self.f_old.responsavel_id)
        if claimed is not None:
            self.assertNotEqual(claimed.protocolo, self.f_old.protocolo)

    def test_second_slot_does_not_consume_sla_before_promotion(self):
        from apps.auditoria.services.reinspecao_fila import (
            FILA_TIMEOUT_SECONDS,
            expire_stale_fila_assignments,
            list_minha_fila,
        )

        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="primeiro",
        )
        direcionar_protocolo(
            falha_id=self.f_mid.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="segundo",
        )
        self.f_mid.refresh_from_db()
        self.assertIsNone(self.f_mid.atribuido_em)

        # Simula um registro legado da posição 2 que já possuía timestamp.
        self.f_old.analise_status = QualidadePendenteReinspecao.ANALISE_EM_ANALISE
        self.f_old.save(update_fields=["analise_status"])
        self.f_mid.atribuido_em = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS + 1)
        self.f_mid.save(update_fields=["atribuido_em"])

        released = expire_stale_fila_assignments(actor=self.actor)
        self.assertEqual(released, 0)
        self.f_mid.refresh_from_db()
        self.assertEqual(self.f_mid.responsavel_id, self.a1.id)

        fila = list_minha_fila(self.a1, auto_claim=False)
        second = fila["slots"][1]["results"][0]
        self.assertFalse(second["sla_ativo"])
        self.assertIsNone(second["timeout_restante_segundos"])
        self.assertIsNone(second["sla_atendimento_segundos"])

    def test_second_slot_sla_starts_when_first_slot_expires(self):
        from apps.auditoria.services.reinspecao_fila import (
            FILA_TIMEOUT_SECONDS,
            expire_stale_fila_assignments,
        )

        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="primeiro",
        )
        direcionar_protocolo(
            falha_id=self.f_mid.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="segundo",
        )
        self.f_mid.atribuido_em = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS + 1)
        self.f_mid.save(update_fields=["atribuido_em"])
        self.f_old.atribuido_em = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS + 5)
        self.f_old.save(update_fields=["atribuido_em"])
        before = timezone.now()

        released = expire_stale_fila_assignments(actor=self.actor)

        self.assertEqual(released, 1)
        self.f_old.refresh_from_db()
        self.f_mid.refresh_from_db()
        self.assertIsNone(self.f_old.responsavel_id)
        # Timeout no slot 1 coloca o auditor offline e libera os demais protocolos.
        self.assertIsNone(self.f_mid.responsavel_id)
        presence = ReinspecaoAuditorPresence.objects.get(user=self.a1)
        self.assertEqual(presence.status, ReinspecaoAuditorPresence.STATUS_OFFLINE)
        self.assertGreaterEqual(presence.status_changed_at, before)

    def test_second_slot_sla_starts_when_first_slot_is_completed(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="primeiro",
        )
        direcionar_protocolo(
            falha_id=self.f_mid.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="segundo",
        )
        self.f_mid.refresh_from_db()
        self.assertIsNone(self.f_mid.atribuido_em)
        self.client.force_authenticate(user=self.a1)
        before = timezone.now()

        response = self.client.post(
            f"/api/v1/qualidade/auditoria/reinspecao/falhas/{self.f_old.id}/concluir/",
            {
                "cliente": "Claro",
                "status": "Improcedente",
                "observacao": "ok",
                "matricula_agente": "c12345a",
                "matricula_auditor": "auditor1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.f_mid.refresh_from_db()
        self.assertIsNotNone(self.f_mid.atribuido_em)
        self.assertGreaterEqual(self.f_mid.atribuido_em, before)

    def test_auditor_can_remove_directed_protocol_from_second_slot(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="primeiro",
        )
        direcionar_protocolo(
            falha_id=self.f_mid.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="segundo",
        )
        self.client.force_authenticate(user=self.a1)

        response = self.client.post(
            f"/api/v1/qualidade/auditoria/reinspecao/fila/me/{self.f_mid.id}/remover/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["removed"])
        self.f_old.refresh_from_db()
        self.f_mid.refresh_from_db()
        self.assertEqual(self.f_old.responsavel_id, self.a1.id)
        self.assertIsNone(self.f_mid.responsavel_id)
        self.assertEqual(
            self.f_mid.analise_status,
            QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO,
        )
        event = ReinspecaoFilaHistorico.objects.filter(
            pendente=self.f_mid,
            tipo=ReinspecaoFilaHistorico.TIPO_LIBERACAO_STATUS,
        ).latest("created_at")
        self.assertEqual(event.detalhe.get("motivo"), "remocao_manual_direcionado")

    def test_first_slot_cannot_be_removed_with_directed_action(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="primeiro",
        )
        self.client.force_authenticate(user=self.a1)

        response = self.client.post(
            f"/api/v1/qualidade/auditoria/reinspecao/fila/me/{self.f_old.id}/remover/"
        )

        self.assertEqual(response.status_code, 400)
        self.f_old.refresh_from_db()
        self.assertEqual(self.f_old.responsavel_id, self.a1.id)

    def test_controle_can_remove_any_slot_protocol(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="primeiro",
        )
        direcionar_protocolo(
            falha_id=self.f_mid.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="segundo",
        )
        self.client.force_authenticate(user=self.actor)

        response = self.client.post(
            f"/api/v1/qualidade/auditoria/reinspecao/fila/{self.a1.id}/remover/",
            {"falha_id": self.f_old.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["removed"])
        self.f_old.refresh_from_db()
        self.f_mid.refresh_from_db()
        self.assertIsNone(self.f_old.responsavel_id)
        self.assertEqual(self.f_mid.responsavel_id, self.a1.id)
        self.assertIsNotNone(self.f_mid.atribuido_em)

        response2 = self.client.post(
            f"/api/v1/qualidade/auditoria/reinspecao/fila/{self.a1.id}/remover/",
            {"protocolo": "200"},
            format="json",
        )
        self.assertEqual(response2.status_code, 200, response2.data)
        self.f_mid.refresh_from_db()
        self.assertIsNone(self.f_mid.responsavel_id)

    def test_auditoria_compliance_queue_is_isolated_from_reinspecao(self):
        from apps.auditoria.services.reinspecao_fila import (
            FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            list_minha_fila,
            reset_fila_contexto,
            set_fila_contexto,
        )

        auditoria_item = _make_falha(
            protocolo="AUD-COMP-1",
            data_contestacao=None,
            data_analise=timezone.now(),
            contexto=FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            tipo_falha="auditoria",
        )
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="reinspecao",
        )

        token = set_fila_contexto(FILA_CONTEXTO_AUDITORIA_COMPLIANCE)
        try:
            set_auditor_status(user=self.a1, status="online", actor=self.actor)
            direcionar_protocolo(
                falha_id=auditoria_item.id,
                auditor_id=self.a1.id,
                actor=self.actor,
                justificativa="auditoria compliance",
            )
            fila_auditoria = list_minha_fila(self.a1, auto_claim=False)
            self.assertEqual(fila_auditoria["results"][0]["protocolo"], "AUD-COMP-1")
            self.assertEqual(
                fila_auditoria["results"][0]["contexto"],
                FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            )
        finally:
            reset_fila_contexto(token)

        fila_reinspecao = list_minha_fila(self.a1, auto_claim=False)
        self.assertEqual(fila_reinspecao["results"][0]["protocolo"], self.f_old.protocolo)
        self.assertEqual(
            ReinspecaoAuditorPresence.objects.filter(user=self.a1).count(),
            2,
        )
        self.assertTrue(
            ReinspecaoFilaHistorico.objects.filter(
                pendente=auditoria_item,
                contexto=FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            ).exists()
        )

    def test_daily_metrics_are_isolated_by_queue_context(self):
        from apps.auditoria.services.reinspecao_fila import (
            FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            list_minha_fila,
            reset_fila_contexto,
            set_fila_contexto,
        )

        completed_at = timezone.now()
        assigned_at = completed_at - timedelta(minutes=3)
        AuditoriaFalhaCadastro.objects.create(
            protocolo="REIN-DIARIO",
            tipo_falha="reinspecao",
            usuario="c19131q",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            responsavel=self.a1,
            atribuido_em=assigned_at,
            analise_concluida_em=completed_at,
        )

        origem_compliance = QualidadeAnaliseOrigem.objects.create(
            protocolo="COMP-DIARIO",
            contexto={"fila_contexto": FILA_CONTEXTO_AUDITORIA_COMPLIANCE},
            conteudo_hash="compliance-daily-metrics",
        )
        AuditoriaFalhaCadastro.objects.create(
            protocolo="COMP-DIARIO",
            tipo_falha="auditoria",
            usuario="c19131q",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            responsavel=self.a1,
            atribuido_em=completed_at - timedelta(minutes=5),
            analise_concluida_em=completed_at,
            analise_origem=origem_compliance,
            brflow_parsed={},
        )

        reinspecao = list_minha_fila(self.a1, auto_claim=False)["metricas_hoje"]
        self.assertEqual(reinspecao["realizados"], 1)
        self.assertEqual(reinspecao["tempo_medio_analise_segundos"], 180)

        token = set_fila_contexto(FILA_CONTEXTO_AUDITORIA_COMPLIANCE)
        try:
            compliance = list_minha_fila(self.a1, auto_claim=False)["metricas_hoje"]
        finally:
            reset_fila_contexto(token)

        self.assertEqual(compliance["realizados"], 1)
        self.assertEqual(compliance["tempo_medio_analise_segundos"], 300)

    def test_daily_metrics_count_irregularidades_not_protocolos(self):
        from apps.auditoria.services.reinspecao_fila import (
            FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            list_minha_fila,
            reset_fila_contexto,
            set_fila_contexto,
        )

        completed_at = timezone.now()
        assigned_at = completed_at - timedelta(minutes=4)
        origem = QualidadeAnaliseOrigem.objects.create(
            protocolo="COMP-MULTI",
            contexto={"fila_contexto": FILA_CONTEXTO_AUDITORIA_COMPLIANCE},
            conteudo_hash="compliance-multi-irreg",
        )
        for index in range(3):
            AuditoriaFalhaCadastro.objects.create(
                protocolo="COMP-MULTI",
                tipo_falha="auditoria",
                usuario="c19131q",
                origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
                tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
                responsavel=self.a1,
                atribuido_em=assigned_at,
                analise_concluida_em=completed_at,
                analise_origem=origem,
                brflow_parsed={},
                motivo_falha=f"Irregularidade {index + 1}",
            )

        token = set_fila_contexto(FILA_CONTEXTO_AUDITORIA_COMPLIANCE)
        try:
            metricas = list_minha_fila(self.a1, auto_claim=False)["metricas_hoje"]
        finally:
            reset_fila_contexto(token)

        self.assertEqual(metricas["realizados"], 3)

    def test_daily_metrics_count_irregularidades_for_reinspecao_compliance(self):
        from apps.auditoria.services.reinspecao_fila import list_minha_fila

        completed_at = timezone.now()
        assigned_at = completed_at - timedelta(minutes=6)
        for index in range(2):
            AuditoriaFalhaCadastro.objects.create(
                protocolo="REIN-MULTI",
                tipo_falha="reinspecao",
                usuario="c19131q",
                origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
                tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
                responsavel=self.a1,
                atribuido_em=assigned_at,
                analise_concluida_em=completed_at,
                motivo_falha=f"Irregularidade {index + 1}",
            )

        metricas = list_minha_fila(self.a1, auto_claim=False)["metricas_hoje"]
        self.assertEqual(metricas["realizados"], 2)

    def test_api_context_does_not_expose_reinspecao_item_in_auditoria(self):
        from apps.auditoria.services.reinspecao_fila import FILA_CONTEXTO_AUDITORIA_COMPLIANCE

        auditoria_item = QualidadePendenteAuditoriaCompliance.objects.create(
            protocolo="AUD-COMP-API",
            data_analise=timezone.now(),
            tipo_falha="auditoria",
            usuario="c19131q",
            descricao_irregularidades="teste",
        )

        response = self.client.get(
            "/api/v1/qualidade/auditoria/compliance/falhas/",
        )

        self.assertEqual(response.status_code, 200, response.data)
        protocolos = {item["protocolo"] for item in response.data["results"]}
        self.assertEqual(protocolos, {auditoria_item.protocolo})

    def test_finalizados_are_isolated_between_reinspecao_and_auditoria_compliance(self):
        from apps.auditoria.services.reinspecao_fila import FILA_CONTEXTO_AUDITORIA_COMPLIANCE

        AuditoriaFalhaCadastro.objects.create(
            protocolo="REIN-FINALIZADO",
            tipo_falha="reinspecao",
            usuario="c19131q",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
        )
        AuditoriaFalhaCadastro.objects.create(
            protocolo="COMP-FINALIZADO",
            tipo_falha="auditoria",
            usuario="c19131q",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            brflow_parsed={"fila_contexto": FILA_CONTEXTO_AUDITORIA_COMPLIANCE},
        )

        reinspecao = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/falhas/",
            {"contexto": "reinspecao", "andamento": "finalizados"},
        )
        compliance = self.client.get(
            "/api/v1/qualidade/auditoria/compliance/falhas/",
            {"andamento": "finalizados"},
        )

        self.assertEqual(reinspecao.status_code, 200, reinspecao.data)
        self.assertEqual(compliance.status_code, 200, compliance.data)
        self.assertEqual(
            {item["protocolo"] for item in reinspecao.data["results"]},
            {"REIN-FINALIZADO"},
        )
        self.assertEqual(
            {item["protocolo"] for item in compliance.data["results"]},
            {"COMP-FINALIZADO"},
        )

    def test_manual_direcionamento_and_historico(self):
        falha = direcionar_protocolo(
            protocolo="200",
            auditor_id=self.a2.id,
            actor=self.actor,
            justificativa="Prioridade cliente",
        )
        self.assertEqual(falha.responsavel_id, self.a2.id)
        event = ReinspecaoFilaHistorico.objects.filter(
            pendente=falha,
            tipo=ReinspecaoFilaHistorico.TIPO_DIRECIONAMENTO,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.justificativa, "Prioridade cliente")

        response = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/historico/",
            {"falha_id": falha.id},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["protocolo"], falha.protocolo)
        self.assertEqual(response.data["results"][0]["pendente_id"], falha.id)
        self.assertEqual(response.data["results"][0]["auditor_nome"], "Bruno")

    def test_consulta_auditor_responsavel_column(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="x",
        )
        listing = self.client.get("/api/v1/qualidade/auditoria/reinspecao/falhas/")
        self.assertEqual(listing.status_code, 200)
        by_proto = {row["protocolo"]: row for row in listing.data["results"]}
        self.assertEqual(str(by_proto["100"]["responsavel_id"]), str(self.a1.id))
        self.assertIn("Aguardando", by_proto["100"]["analise_status_label"])
        self.assertIsNone(by_proto["300"]["responsavel_id"])
        self.assertEqual(by_proto["300"]["analise_status"], "nao_atribuido")

    def test_consulta_exposes_business_days_using_brasilia_date(self):
        from apps.auditoria.services.reinspecao_fila import serialize_fila_item

        received = datetime(2026, 8, 11, 2, 30, tzinfo=dt_timezone.utc)
        now = datetime(2026, 8, 12, 2, 0, tzinfo=dt_timezone.utc)
        falha = _make_falha(protocolo="SLA-BSB", data_contestacao=received)

        payload = serialize_fila_item(falha, now=now)

        self.assertEqual(payload["sla_dias_uteis"], 1)
        self.assertEqual(payload["sla_farol"], "d1")
        self.assertEqual(payload["data_final"], "2026-08-12")

    def test_controle_lists_only_role_for_each_context(self):
        reinspecao_group, _ = Group.objects.get_or_create(
            name=role_group_name(ROLE_QUAL_CONTESTACAO_COMPLIANCE)
        )
        auditoria_group, _ = Group.objects.get_or_create(
            name=role_group_name(ROLE_QUAL_AUDITORIA_COMPLIANCE)
        )
        self.a1.groups.add(reinspecao_group)
        self.a2.groups.add(auditoria_group)

        reinspecao = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/controle/auditores/",
            {"contexto": "reinspecao"},
        )
        auditoria = self.client.get(
            "/api/v1/qualidade/auditoria/compliance/controle/auditores/",
        )

        self.assertEqual(reinspecao.status_code, 200, reinspecao.data)
        self.assertEqual(auditoria.status_code, 200, auditoria.data)
        self.assertEqual(
            {row["user_id"] for row in reinspecao.data["auditores"]},
            {str(self.a1.id)},
        )
        self.assertEqual(
            {row["user_id"] for row in auditoria.data["auditores"]},
            {str(self.a2.id)},
        )

    def test_controle_compliance_counts_tratados_from_analise_origem(self):
        from apps.auditoria.services.qualidade_promocao import (
            promover_pendente_auditoria_compliance,
        )
        from apps.auditoria.services.auditoria_compliance_fila import (
            build_controle_operacoes,
        )

        auditoria_group, _ = Group.objects.get_or_create(
            name=role_group_name(ROLE_QUAL_AUDITORIA_COMPLIANCE)
        )
        self.a2.groups.add(auditoria_group)
        now = timezone.now()
        pendente = QualidadePendenteAuditoriaCompliance.objects.create(
            protocolo="COMP-CTRL-1",
            usuario="c19131q",
            tipo_falha="Colaborador",
            descricao_irregularidades="teste",
            data_analise=now,
            analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE,
            responsavel=self.a2,
            atribuido_em=now - timedelta(minutes=10),
            analise_iniciada_em=now - timedelta(minutes=8),
            analise_concluida_em=now,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.actor,
        )
        promover_pendente_auditoria_compliance(
            pendente,
            finalizador=self.a2,
        )

        payload = build_controle_operacoes()

        by_user = {row["user_id"]: row for row in payload["auditores"]}
        self.assertIn(str(self.a2.id), by_user)
        self.assertGreaterEqual(by_user[str(self.a2.id)]["concluidos"], 1)
        self.assertGreaterEqual(payload["kpis"]["protocolos_concluidos_periodo"], 1)

    def test_controle_includes_active_auditor_without_role(self):
        from apps.auditoria.services.reinspecao_fila import (
            FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            list_auditores_controle,
            reset_fila_contexto,
            set_fila_contexto,
        )

        now = timezone.now()
        QualidadePendenteReinspecao.objects.create(
            protocolo="COMP-NO-ROLE",
            contexto=FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
            usuario="c19131q",
            tipo_falha="Colaborador",
            descricao_irregularidades="teste",
            data_analise=now,
            analise_status=QualidadePendenteReinspecao.ANALISE_AGUARDANDO,
            responsavel=self.a2,
            atribuido_em=now,
            created_by=self.actor,
        )

        token = set_fila_contexto(FILA_CONTEXTO_AUDITORIA_COMPLIANCE)
        try:
            auditores = list_auditores_controle()
        finally:
            reset_fila_contexto(token)

        self.assertEqual({row["user_id"] for row in auditores}, {str(self.a2.id)})
        self.assertGreaterEqual(auditores[0]["aguardando"], 1)

    def test_presence_and_fila_api(self):
        me = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/presence/me/",
            {"status": "online"},
            format="json",
        )
        self.assertEqual(me.status_code, 200, me.data)
        self.assertEqual(me.data["presence"]["status"], "online")
        self.assertIn("distribution", me.data)

        # actor is online; distribute to actor
        distribuir_protocolos(actor=self.actor)
        fila = self.client.get("/api/v1/qualidade/auditoria/reinspecao/fila/me/")
        self.assertEqual(fila.status_code, 200)
        self.assertGreaterEqual(fila.data["total"], 1)

    def test_unassigned_seeds_prefer_oldest_date_and_diversify_agents(self):
        from apps.auditoria.services.reinspecao_fila import _unassigned_protocolo_seeds

        QualidadePendenteReinspecao.objects.all().delete()
        now = timezone.now()
        old = now - timedelta(days=5)
        recent = now - timedelta(days=1)
        for idx in range(6):
            QualidadePendenteReinspecao.objects.create(
                protocolo=f"old-a-{idx}",
                usuario="agente_a",
                tipo_falha="reinspecao",
                descricao_irregularidades="teste",
                data_contestacao=old,
                analise_status=QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO,
            )
        for idx in range(6):
            QualidadePendenteReinspecao.objects.create(
                protocolo=f"old-b-{idx}",
                usuario="agente_b",
                tipo_falha="reinspecao",
                descricao_irregularidades="teste",
                data_contestacao=old,
                analise_status=QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO,
            )
        QualidadePendenteReinspecao.objects.create(
            protocolo="recent-1",
            usuario="agente_c",
            tipo_falha="reinspecao",
            descricao_irregularidades="teste",
            data_contestacao=recent,
            analise_status=QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO,
        )

        seeds = _unassigned_protocolo_seeds(limit=4)
        self.assertEqual(len(seeds), 4)
        self.assertTrue(all((s.data_contestacao or now) == old for s in seeds))
        self.assertNotIn("recent-1", {s.protocolo for s in seeds})
        # Com intercalação por agente, os 2 primeiros tendem a ser agentes distintos.
        self.assertEqual({seeds[0].usuario, seeds[1].usuario}, {"agente_a", "agente_b"})

        first_picks = {
            _unassigned_protocolo_seeds(limit=1)[0].protocolo
            for _ in range(30)
        }
        self.assertGreater(len(first_picks), 1)

    def test_same_protocol_assigns_all_irregularidades(self):
        now = timezone.now()
        _make_falha(protocolo="100", data_contestacao=now - timedelta(days=3))
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        result = distribuir_protocolos(actor=self.actor, limit=1)
        self.assertEqual(result["assigned"], 1)
        group = QualidadePendenteReinspecao.objects.filter(protocolo="100", responsavel=self.a1)
        self.assertEqual(group.count(), 2)
        self.assertEqual(ativos_por_auditor().get(self.a1.id), 1)

    def test_direcionar_proximo_da_fila(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        falha = direcionar_protocolo(
            protocolo="",
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="próximo da fila",
        )
        self.assertEqual(falha.protocolo, "100")
        self.assertEqual(falha.responsavel_id, self.a1.id)

    def test_online_claims_next_for_self(self):
        set_auditor_status(user=self.a2, status="online", actor=self.actor)
        from apps.auditoria.services.reinspecao_fila import set_auditor_status_and_maybe_distribute

        result = set_auditor_status_and_maybe_distribute(user=self.a1, status="online", actor=self.actor)
        self.assertEqual(result["distribution"]["assigned"], 1)
        self.assertIsNotNone(result["distribution"]["protocolo"])
        self.assertEqual(
            QualidadePendenteReinspecao.objects.filter(
                responsavel=self.a1,
                analise_status__in=["aguardando_analise", "em_analise"],
            ).count(),
            1,
        )

    def test_concluir_registra_modulo_e_sla(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="x",
        )
        self.client.force_authenticate(user=self.a1)
        resp = self.client.post(
            f"/api/v1/qualidade/auditoria/reinspecao/falhas/{self.f_old.id}/concluir/",
            {
                "cliente": "Claro",
                "status": "Improcedente",
                "observacao": "ok",
                "matricula_agente": "c12345a",
                "matricula_auditor": "auditor1",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(QualidadePendenteReinspecao.objects.filter(pk=self.f_old.pk).exists())
        tratado = AuditoriaFalhaCadastro.objects.get(pk=resp.data["id"])
        self.assertEqual(tratado.analise_status, AuditoriaFalhaCadastro.ANALISE_CONCLUIDO)
        self.assertEqual(tratado.modulo, "Contestação")
        self.assertEqual(tratado.status, "Improcedente")
        self.assertEqual(tratado.cliente, "Claro")
        self.assertEqual(tratado.usuario, "c12345a")
        self.assertEqual(tratado.auditor, "auditor1")
        self.assertIsNotNone(tratado.atribuido_em)
        self.assertIsNotNone(tratado.analise_concluida_em)
        self.assertIsNotNone(resp.data.get("sla_atendimento_segundos"))
        event = ReinspecaoFilaHistorico.objects.filter(
            falha=tratado,
            tipo=ReinspecaoFilaHistorico.TIPO_CONCLUSAO,
        ).first()
        self.assertIsNotNone(event)
        self.assertIn("sla_atendimento_segundos", event.detalhe)

    def test_concluir_fora_do_prazo_sem_preencher(self):
        set_auditor_status(user=self.a1, status="online", actor=self.actor)
        direcionar_protocolo(
            falha_id=self.f_old.id,
            auditor_id=self.a1.id,
            actor=self.actor,
            justificativa="x",
        )
        self.client.force_authenticate(user=self.a1)
        resp = self.client.post(
            f"/api/v1/qualidade/auditoria/reinspecao/falhas/{self.f_old.id}/concluir/",
            {
                "cliente": "Claro",
                "status": "Fora do prazo",
                "matricula_auditor": "auditor1",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(QualidadePendenteReinspecao.objects.filter(pk=self.f_old.pk).exists())
        tratado = AuditoriaFalhaCadastro.objects.get(pk=resp.data["id"])
        self.assertEqual(tratado.status, "Fora do prazo")
        self.assertEqual(tratado.usuario, "c19131q")
        self.assertEqual(tratado.brflow_parsed.get("situacao"), "fora_do_prazo")
        self.assertEqual(resp.data.get("situacao"), "fora_do_prazo")
        self.assertEqual(resp.data.get("status"), "Fora do prazo")

    def test_concluir_fora_do_prazo_rejeitado_em_auditoria_compliance(self):
        from apps.auditoria.services.auditoria_compliance_fila import (
            direcionar_protocolo as direcionar_compliance,
            set_auditor_status as set_compliance_status,
        )

        compliance = QualidadePendenteAuditoriaCompliance.objects.create(
            protocolo="400",
            data_analise=timezone.now(),
            tipo_falha="auditoria",
            usuario="c19131q",
            descricao_irregularidades="teste",
        )
        set_compliance_status(user=self.a2, status="online", actor=self.actor)
        direcionar_compliance(
            falha_id=compliance.id,
            auditor_id=self.a2.id,
            actor=self.actor,
            justificativa="x",
        )
        self.client.force_authenticate(user=self.a2)
        resp = self.client.post(
            f"/api/v1/qualidade/auditoria/compliance/falhas/{compliance.id}/concluir/",
            {
                "cliente": "Claro",
                "status": "Fora do prazo",
                "matricula_auditor": "auditor2",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_assign_permission_required_when_enforced(self):
        with self.settings(ACCESS_ENFORCEMENT=True):
            user = User.objects.create_user(
                username="no_assign",
                email="no_assign@test.local",
                password="test12345",
            )
            client = APIClient()
            client.force_authenticate(user=user)
            resp = client.post(
                "/api/v1/qualidade/auditoria/reinspecao/direcionar/",
                {
                    "protocolo": "100",
                    "auditor_id": self.a1.id,
                    "justificativa": "teste",
                },
                format="json",
            )
            self.assertIn(resp.status_code, {401, 403})
