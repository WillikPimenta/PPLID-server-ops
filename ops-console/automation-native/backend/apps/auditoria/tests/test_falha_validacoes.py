import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_QUAL_CAPACITACAO, role_group_name
from apps.access.models import PortalRoleDefinition
from apps.access.registry import (
    QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE,
    QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE,
    QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW,
)
from apps.access.roles import ROLE_DEFINITIONS
from apps.auditoria.models import (
    AuditoriaFalhaAlteracao,
    AuditoriaFalhaCadastro,
    AuditoriaFalhaValidacaoHistorico,
)
from apps.auditoria.services.falha_validacoes import (
    FalhaValidacaoConflict,
    decidir_conforme,
    decidir_nao_conforme,
)
from apps.workforce.models import Agent, UserProfile


User = get_user_model()


class FalhaValidacaoServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="c91001a", email="c91001a@test.local"
        )
        self.actor = Agent.objects.create(
            full_name="Capacitacao Um",
            user_lan_id=self.user.username,
            active=True,
        )
        UserProfile.objects.create(user=self.user, agent=self.actor)
        self.audited = Agent.objects.create(
            full_name="Pessoa Auditada", user_lan_id="c91002a", active=True
        )
        self.falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="REVISAO-1",
            tipo_falha="Documento",
            usuario=self.audited.user_lan_id,
            agente_ref=self.audited,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO,
        )

    def test_em_validacao_is_not_classified(self):
        self.assertEqual(
            self.falha.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO,
        )

    def test_conforme_activates_without_creating_change(self):
        falha, history, change, created = decidir_conforme(
            falha_id=self.falha.id,
            user=self.user,
            observacao="Falha cadastrada corretamente.",
            expected_updated_at=self.falha.updated_at.isoformat(),
            idempotency_key=str(uuid.uuid4()),
        )

        self.assertTrue(created)
        self.assertIsNone(change)
        self.assertEqual(falha.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA)
        self.assertEqual(history.status_de, "em_validacao")
        self.assertEqual(history.status_para, "ativa")
        self.assertEqual(history.resultado, "conforme")
        self.assertIsNone(history.alteracao_id)
        self.assertEqual(history.snapshot_inicial["status_falha"], "em_validacao")
        self.assertEqual(history.snapshot_final["status_falha"], "ativa")

    def test_nao_conforme_edits_atomically_and_sets_sem_falha_status(self):
        falha, history, change, created = decidir_nao_conforme(
            falha_id=self.falha.id,
            user=self.user,
            dados={
                "resultado_qualidade": AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
                "observacao": "Correcao aplicada",
            },
            observacao="Registro corrigido pela capacitacao.",
            expected_updated_at=self.falha.updated_at.isoformat(),
            idempotency_key=str(uuid.uuid4()),
        )

        self.assertTrue(created)
        self.assertIsNotNone(change)
        self.assertEqual(
            falha.status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_NAO_CONFORME,
        )
        self.assertEqual(falha.resultado_qualidade, AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA)
        self.assertEqual(history.alteracao_id, change.id)
        self.assertEqual(history.status_para, "nao_conforme")
        self.assertEqual(change.dados_alterados["status_falha"], "nao_conforme")
        self.assertIn("status_falha", change.campos_alterados)
        self.assertEqual(AuditoriaFalhaAlteracao.objects.filter(falha=falha).count(), 1)

    def test_nao_conforme_keeps_active_when_final_result_has_failure(self):
        falha, history, change, _created = decidir_nao_conforme(
            falha_id=self.falha.id,
            user=self.user,
            dados={
                "resultado_qualidade": AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
                "motivo_falha": "Documento divergente",
            },
            observacao="Classificacao da falha foi corrigida.",
            expected_updated_at=self.falha.updated_at.isoformat(),
            idempotency_key=str(uuid.uuid4()),
        )
        self.assertEqual(falha.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA)
        self.assertEqual(history.status_para, "ativa")
        self.assertEqual(change.dados_alterados["status_falha"], "ativa")

    def test_same_idempotency_key_replays_and_different_payload_conflicts(self):
        key = str(uuid.uuid4())
        kwargs = {
            "falha_id": self.falha.id,
            "user": self.user,
            "observacao": "Validacao confirmada pela area.",
            "expected_updated_at": self.falha.updated_at.isoformat(),
            "idempotency_key": key,
        }
        _falha, first, _change, created = decidir_conforme(**kwargs)
        _falha, replay, _change, replay_created = decidir_conforme(**kwargs)
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(first.id, replay.id)
        self.assertEqual(AuditoriaFalhaValidacaoHistorico.objects.count(), 1)

        with self.assertRaises(FalhaValidacaoConflict):
            decidir_conforme(**{**kwargs, "observacao": "Outro payload de decisao."})

    def test_stale_or_terminal_decision_conflicts(self):
        stale = "2000-01-01T00:00:00+00:00"
        self.falha.observacao = "Mudanca concorrente"
        self.falha.save(update_fields=["observacao", "updated_at"])
        with self.assertRaises(FalhaValidacaoConflict):
            decidir_conforme(
                falha_id=self.falha.id,
                user=self.user,
                observacao="Tentativa com versao antiga.",
                expected_updated_at=stale,
                idempotency_key=str(uuid.uuid4()),
            )

        current = self.falha.updated_at.isoformat()
        decidir_conforme(
            falha_id=self.falha.id,
            user=self.user,
            observacao="Decisao final da capacitacao.",
            expected_updated_at=current,
            idempotency_key=str(uuid.uuid4()),
        )
        with self.assertRaises(FalhaValidacaoConflict):
            decidir_conforme(
                falha_id=self.falha.id,
                user=self.user,
                observacao="Segunda decisao nao permitida.",
                expected_updated_at=self.falha.updated_at.isoformat(),
                idempotency_key=str(uuid.uuid4()),
            )

    def test_nao_conforme_rolls_back_when_edit_is_invalid(self):
        with self.assertRaises(ValidationError):
            decidir_nao_conforme(
                falha_id=self.falha.id,
                user=self.user,
                dados={"campo_inexistente": "valor"},
                observacao="Tentativa de alteracao invalida.",
                expected_updated_at=self.falha.updated_at.isoformat(),
                idempotency_key=str(uuid.uuid4()),
            )
        self.falha.refresh_from_db()
        self.assertEqual(self.falha.status_falha, "em_validacao")
        self.assertFalse(AuditoriaFalhaAlteracao.objects.exists())
        self.assertFalse(AuditoriaFalhaValidacaoHistorico.objects.exists())


@override_settings(ACCESS_ENFORCEMENT=True)
class FalhaValidacaoApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="c92001a", email="c92001a@test.local"
        )
        self.actor = Agent.objects.create(
            full_name="Capacitacao API", user_lan_id=self.user.username, active=True
        )
        UserProfile.objects.create(user=self.user, agent=self.actor)
        role = "qual_revisao_falhas_teste"
        PortalRoleDefinition.objects.create(
            role=role,
            label="Revisao de falhas teste",
            area="qualidade",
            permissions=[
                QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW,
                QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE,
                QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE,
            ],
            granted_routes=None,
            default_scope="global",
            is_editable=True,
        )
        group = Group.objects.create(name=role_group_name(role))
        self.user.groups.add(group)
        self.falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="REVISAO-API-1",
            tipo_falha="Documento",
            usuario="c92002a",
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO,
        )
        self.client.force_authenticate(self.user)

    def test_capacitacao_role_defines_all_review_permissions(self):
        permissions = ROLE_DEFINITIONS[ROLE_QUAL_CAPACITACAO]["permissions"]
        self.assertTrue(
            {
                QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW,
                QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE,
                QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE,
            }.issubset(permissions)
        )

    def test_list_detail_and_conforme_contract(self):
        list_response = self.client.get(
            "/api/v1/qualidade/auditoria/falhas/validacoes/",
            {"status": "em_validacao"},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)
        self.assertEqual(list_response.data["results"][0]["status_falha"], "em_validacao")
        self.assertIn("filter_options", list_response.data)

        detail_url = f"/api/v1/qualidade/auditoria/falhas/validacoes/{self.falha.id}/"
        detail = self.client.get(detail_url)
        self.assertEqual(detail.status_code, 200)
        for key in (
            "falha",
            "current",
            "profile",
            "fields",
            "analises",
            "alteracoes",
            "agents",
            "historico",
            "can_decide",
            "can_change",
        ):
            self.assertIn(key, detail.data)

        response = self.client.post(
            f"{detail_url}conforme/",
            {
                "observacao": "Cadastro conferido pela capacitacao.",
                "expected_updated_at": self.falha.updated_at.isoformat(),
                "idempotency_key": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["created"])
        self.assertEqual(response.data["falha"]["status_falha"], "ativa")
        self.assertIsNone(response.data["alteracao"])

    def test_permission_denied_and_stale_returns_conflict(self):
        outsider = User.objects.create_user(
            username="c92003a", email="c92003a@test.local"
        )
        self.client.force_authenticate(outsider)
        denied = self.client.get("/api/v1/qualidade/auditoria/falhas/validacoes/")
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.user)
        stale = "2000-01-01T00:00:00+00:00"
        self.falha.observacao = "Concorrencia"
        self.falha.save(update_fields=["observacao", "updated_at"])
        conflict = self.client.post(
            f"/api/v1/qualidade/auditoria/falhas/validacoes/{self.falha.id}/conforme/",
            {
                "observacao": "Tentativa usando versao antiga.",
                "expected_updated_at": stale,
                "idempotency_key": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(conflict.status_code, 409)

    def test_nao_conforme_endpoint_uses_atomic_change_contract(self):
        response = self.client.post(
            (
                f"/api/v1/qualidade/auditoria/falhas/validacoes/"
                f"{self.falha.id}/nao-conforme/"
            ),
            {
                "dados": {
                    "resultado_qualidade": "sem_falha",
                    "motivo_falha": "Registro revisado",
                },
                "observacao": "Correcao confirmada pela capacitacao.",
                "expected_updated_at": self.falha.updated_at.isoformat(),
                "idempotency_key": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["falha"]["status_falha"], "nao_conforme")
        self.assertIsNotNone(response.data["alteracao"])
        self.assertEqual(response.data["validacao"]["resultado"], "nao_conforme")
