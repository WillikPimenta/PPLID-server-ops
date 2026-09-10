import uuid
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.constants import ROLE_QUAL_AUDITORIA_FRAUD, role_group_name
from apps.access.models import PortalRoleDefinition
from apps.access.registry import (
    QUAL_AUDITORIA_COMPLIANCE_VIEW,
    QUAL_AUDITORIA_CHANGE,
    QUAL_AUDITORIA_RESULT_CHANGE,
    QUAL_AUDITORIA_VIEW,
)
from apps.auditoria.models import (
    AuditoriaFalhaAlteracao,
    AuditoriaFalhaCadastro,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.falha_alteracoes import EDITABLE_FIELDS
from apps.auditoria.services.qualidade_promocao import promover_pendente_reinspecao
from apps.workforce.models import Agent, UserProfile


User = get_user_model()


class AuditoriaFalhaAgentBackfillTests(TestCase):
    def setUp(self):
        self.system = Agent.objects.create(
            full_name="SISTEMA",
            user_lan_id="c000000a",
            active=True,
        )
        self.inactive_auditor = Agent.objects.create(
            full_name="Auditor Inativo",
            user_lan_id="c90001a",
            active=False,
        )

    def _record(self):
        return AuditoriaFalhaCadastro.objects.create(
            protocolo="BACKFILL-1",
            tipo_falha="Documento",
            usuario="SISTEMA",
            auditor="Auditor Inativo",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        )

    def test_dry_run_does_not_persist_links(self):
        record = self._record()
        call_command("backfill_auditoria_falha_agents", stdout=StringIO())
        record.refresh_from_db()
        self.assertIsNone(record.agente_ref_id)
        self.assertIsNone(record.auditor_ref_id)
        self.assertEqual(record.usuario, "SISTEMA")
        self.assertEqual(record.auditor, "Auditor Inativo")

    def test_apply_links_system_and_inactive_without_touching_legacy(self):
        record = self._record()
        call_command("backfill_auditoria_falha_agents", "--apply", stdout=StringIO())
        record.refresh_from_db()
        self.assertEqual(record.agente_ref_id, self.system.id)
        self.assertEqual(record.auditor_ref_id, self.inactive_auditor.id)
        self.assertEqual(record.usuario, "SISTEMA")
        self.assertEqual(record.auditor, "Auditor Inativo")

    def test_apply_uses_created_by_for_fraud_without_legacy_auditor(self):
        user = User.objects.create_user(username="c90003a")
        auditor = Agent.objects.create(
            full_name="Auditora do Registro Legado",
            user_lan_id=user.username,
            active=True,
        )
        UserProfile.objects.create(user=user, agent=auditor)
        record = AuditoriaFalhaCadastro.objects.create(
            protocolo="BACKFILL-FRAUD",
            tipo_falha="Documento",
            usuario="SISTEMA",
            auditor="",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            created_by=user,
        )

        call_command("backfill_auditoria_falha_agents", "--apply", stdout=StringIO())

        record.refresh_from_db()
        self.assertEqual(record.auditor_ref_id, auditor.id)
        self.assertEqual(record.auditor, "")


class AuditoriaFalhaAlteracaoApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="c90002a",
            email="c90002a@test.local",
            password="test12345",
        )
        self.actor = Agent.objects.create(
            full_name="Gestora da Qualidade",
            user_lan_id="c90002a",
            active=False,
        )
        UserProfile.objects.create(user=self.user, agent=self.actor)
        self.system = Agent.objects.create(
            full_name="SISTEMA",
            user_lan_id="c000000a",
            active=True,
        )
        self.auditor = Agent.objects.create(
            full_name="Auditor Responsavel",
            user_lan_id="c90003a",
            active=True,
        )
        self.falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="ALTERAR-1",
            tipo_falha="Documento",
            usuario=self.auditor.user_lan_id,
            agente_ref=self.auditor,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        )
        self.endpoint = f"/api/v1/qualidade/auditoria/falhas/{self.falha.id}/alteracoes/"
        self.client.force_authenticate(self.user)

    def _user_with_permissions(self, username: str, *permissions: str):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@test.local",
            password="test12345",
        )
        agent = Agent.objects.create(full_name=f"Usuario {username}", user_lan_id=username)
        UserProfile.objects.create(user=user, agent=agent)
        role = f"resultado_{username}"
        PortalRoleDefinition.objects.create(
            role=role,
            label=f"Alteracao de resultado {username}",
            area="qualidade",
            permissions=list(permissions),
            granted_routes=None,
            default_scope="global",
            is_editable=True,
        )
        group = Group.objects.create(name=role_group_name(role))
        user.groups.add(group)
        return user

    def test_new_treated_record_links_system_and_inactive_finalizer(self):
        pending = QualidadePendenteReinspecao.objects.create(
            protocolo="NOVO-SISTEMA-1",
            usuario="SISTEMA",
            auditor=self.user.username,
            tipo_falha="Documento",
            created_by=self.user,
        )
        treated = promover_pendente_reinspecao(pending, user=self.user)
        self.assertEqual(treated.agente_ref_id, self.system.id)
        self.assertEqual(treated.auditor_ref_id, self.actor.id)
        self.assertFalse(treated.auditor_ref.active)

    def test_changes_current_row_and_creates_de_para_history(self):
        response = self.client.post(
            self.endpoint,
            {
                "dados": {
                    "agente_id": str(self.system.id),
                    "resultado_qualidade": "sem_falha",
                },
                "justificativa": "Correcao confirmada pelo processo.",
                "expected_updated_at": self.falha.updated_at.isoformat(),
                "idempotency_key": "2dc29fa0-288d-42a3-8826-4c05fd26c8f7",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.falha.refresh_from_db()
        self.assertEqual(self.falha.agente_ref_id, self.system.id)
        self.assertEqual(self.falha.usuario, "c000000a")
        self.assertEqual(
            self.falha.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
        )
        self.assertEqual(
            self.falha.resultado_qualidade_override,
            AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
        )
        self.assertEqual(
            self.falha.status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
        )
        change = AuditoriaFalhaAlteracao.objects.get(falha=self.falha)
        self.assertEqual(change.alterado_por_id, self.actor.id)
        self.assertIn("agente_id", change.campos_alterados)
        self.assertIn("resultado_qualidade", change.campos_alterados)
        self.assertIn("status_falha", change.campos_alterados)
        self.assertEqual(
            change.dados_anteriores["status_falha"],
            AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        )
        self.assertEqual(
            change.dados_alterados["status_falha"],
            AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
        )
        self.assertEqual(change.dados_anteriores["agente_id"], str(self.auditor.id))
        self.assertEqual(change.dados_alterados["agente_id"], str(self.system.id))

        history = self.client.get(self.endpoint)
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(history.data["alteracoes"][0]["versao"], 1)
        self.assertTrue(history.data["can_change"])
        agents_by_id = {item["id"]: item for item in history.data["agents"]}
        self.assertEqual(agents_by_id[str(self.system.id)]["user_lan_id"], "c000000a")
        self.assertTrue(agents_by_id[str(self.actor.id)]["is_auditor"])

    def test_get_returns_all_editable_fields_for_selected_fraud_record(self):
        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["profile"]["key"], "fraud")
        fields = {item["key"]: item for item in response.data["fields"]}
        self.assertIn("tipo_falha", fields)
        self.assertIn("resultado_cliente", fields)
        self.assertEqual(fields["motivo_falha"]["label"], "Cenario")
        self.assertEqual(fields["status_falha"]["type"], "readonly")
        self.assertEqual(set(fields), set(EDITABLE_FIELDS))

    def test_agent_options_return_full_headcount_system_and_only_mark_auditors(self):
        auditor_user = User.objects.create_user(username="c90004a", password="test12345")
        auditor_agent = Agent.objects.create(
            full_name="Auditora Especialista",
            user_lan_id="c90004a",
        )
        UserProfile.objects.create(user=auditor_user, agent=auditor_agent)
        auditor_group, _ = Group.objects.get_or_create(
            name=role_group_name(ROLE_QUAL_AUDITORIA_FRAUD)
        )
        auditor_user.groups.add(auditor_group)
        inactive = Agent.objects.create(
            full_name="Agente Inativo do Headcount",
            user_lan_id="c90005a",
            active=False,
        )
        for index in range(55):
            Agent.objects.create(
                full_name=f"Agente Headcount {index:02d}",
                user_lan_id=f"h{index:05d}",
            )

        response = self.client.get("/api/v1/qualidade/auditoria/falhas/agentes/")

        self.assertEqual(response.status_code, 200, response.data)
        by_id = {item["id"]: item for item in response.data["results"]}
        self.assertEqual(len(by_id), Agent.objects.count())
        self.assertFalse(by_id[str(inactive.id)]["active"])
        self.assertTrue(by_id[str(self.system.id)]["is_system"])
        self.assertTrue(by_id[str(auditor_agent.id)]["is_auditor"])
        self.assertFalse(by_id[str(inactive.id)]["is_auditor"])

    def test_agent_options_create_system_option_when_headcount_does_not_have_it(self):
        self.system.delete()

        response = self.client.get("/api/v1/qualidade/auditoria/falhas/agentes/")

        self.assertEqual(response.status_code, 200, response.data)
        system_options = [item for item in response.data["results"] if item["is_system"]]
        self.assertEqual(len(system_options), 1)
        self.assertEqual(system_options[0]["full_name"], "SISTEMA")

    def test_get_lists_every_grid_row_from_same_fraud_protocol(self):
        sibling = AuditoriaFalhaCadastro.objects.create(
            protocolo=self.falha.protocolo,
            tipo_falha="Processo",
            motivo_falha="Etapa incorreta",
            usuario=self.auditor.user_lan_id,
            agente_ref=self.auditor,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        )
        unrelated = AuditoriaFalhaCadastro.objects.create(
            protocolo=self.falha.protocolo,
            tipo_falha="reinspecao",
            usuario=self.auditor.user_lan_id,
            agente_ref=self.auditor,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            brflow_parsed={"fila_contexto": "reinspecao"},
        )

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            {item["id"] for item in response.data["analises"]},
            {self.falha.id, sibling.id},
        )
        self.assertNotIn(unrelated.id, {item["id"] for item in response.data["analises"]})

    def test_get_lists_every_grid_row_from_same_compliance_protocol(self):
        first = AuditoriaFalhaCadastro.objects.create(
            protocolo="ALTERAR-COMPLIANCE-GRID",
            tipo_falha="auditoria",
            motivo_falha="Documento",
            usuario=self.auditor.user_lan_id,
            agente_ref=self.auditor,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            ordem_etapa=0,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )
        second = AuditoriaFalhaCadastro.objects.create(
            protocolo=first.protocolo,
            tipo_falha="auditoria",
            motivo_falha="Assinatura",
            usuario=self.auditor.user_lan_id,
            agente_ref=self.auditor,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            ordem_etapa=1,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )
        endpoint = f"/api/v1/qualidade/auditoria/falhas/{first.id}/alteracoes/"

        response = self.client.get(endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [item["id"] for item in response.data["analises"]],
            [first.id, second.id],
        )

    def test_get_returns_all_editable_fields_for_selected_compliance_record(self):
        compliance = AuditoriaFalhaCadastro.objects.create(
            protocolo="ALTERAR-COMPLIANCE-1",
            tipo_falha="auditoria",
            usuario=self.auditor.user_lan_id,
            agente_ref=self.auditor,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )
        endpoint = f"/api/v1/qualidade/auditoria/falhas/{compliance.id}/alteracoes/"

        response = self.client.get(endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["profile"]["key"], "compliance")
        fields = {item["key"]: item for item in response.data["fields"]}
        self.assertIn("descricao_irregularidades", fields)
        self.assertIn("data_analise", fields)
        self.assertEqual(fields["motivo_falha"]["label"], "Irregularidade")
        self.assertEqual(fields["etapa_falha"]["label"], "Status da irregularidade")
        self.assertEqual(set(fields), set(EDITABLE_FIELDS))

    def test_compliance_permissions_open_only_compliance_change_flow(self):
        compliance = AuditoriaFalhaCadastro.objects.create(
            protocolo="ALTERAR-COMPLIANCE-PERMISSAO",
            tipo_falha="auditoria",
            usuario=self.auditor.user_lan_id,
            agente_ref=self.auditor,
            auditor=self.actor.user_lan_id,
            auditor_ref=self.actor,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )
        endpoint = f"/api/v1/qualidade/auditoria/falhas/{compliance.id}/alteracoes/"
        user = self._user_with_permissions(
            "c90006a",
            QUAL_AUDITORIA_COMPLIANCE_VIEW,
            QUAL_AUDITORIA_RESULT_CHANGE,
        )
        self.client.force_authenticate(user)

        detail = self.client.get(endpoint)
        catalogs = self.client.get("/api/v1/qualidade/auditoria/catalogs/")
        fraud = self.client.get(self.endpoint)

        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertTrue(detail.data["can_change"])
        self.assertEqual(catalogs.status_code, 200, catalogs.data)
        self.assertEqual(fraud.status_code, 403, fraud.data)

    def test_allows_registration_fields_for_selected_fraud_origin(self):
        response = self.client.post(
            self.endpoint,
            {
                "dados": {
                    "cruzamento_bases": "Sim",
                    "data_analise": "2026-08-24T12:00:00-03:00",
                },
                "justificativa": "Complemento dos dados cadastrados.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.falha.refresh_from_db()
        self.assertEqual(self.falha.cruzamento_bases, "Sim")
        self.assertIsNotNone(self.falha.data_analise)

    def test_protocol_is_readonly(self):
        response = self.client.post(
            self.endpoint,
            {
                "dados": {"protocolo": "PROTOCOLO-ALTERADO"},
                "justificativa": "Tentativa de alterar o protocolo.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.falha.refresh_from_db()
        self.assertEqual(self.falha.protocolo, "ALTERAR-1")
        self.assertEqual(AuditoriaFalhaAlteracao.objects.count(), 0)

    def test_status_falha_is_readonly(self):
        initial_status = self.falha.status_falha
        response = self.client.post(
            self.endpoint,
            {
                "dados": {"status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA},
                "justificativa": "Tentativa de alterar campo controlado.",
                "expected_updated_at": self.falha.updated_at.isoformat(),
                "idempotency_key": str(uuid.uuid4()),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("status_falha", str(response.data))
        self.falha.refresh_from_db()
        self.assertEqual(self.falha.status_falha, initial_status)
        self.assertEqual(AuditoriaFalhaAlteracao.objects.count(), 0)

    def test_idempotency_key_does_not_duplicate_history(self):
        payload = {
            "dados": {"observacao": "Valor corrigido"},
            "justificativa": "Ajuste solicitado pela gestao.",
            "idempotency_key": "dc81d260-6536-41f1-bc69-d50613248d4c",
        }
        first = self.client.post(self.endpoint, payload, format="json")
        second = self.client.post(self.endpoint, payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertFalse(second.data["created"])
        self.assertEqual(AuditoriaFalhaAlteracao.objects.filter(falha=self.falha).count(), 1)

    def test_system_cannot_be_auditor(self):
        response = self.client.post(
            self.endpoint,
            {
                "dados": {"auditor_id": str(self.system.id)},
                "justificativa": "Tentativa de auditor tecnico.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(AuditoriaFalhaAlteracao.objects.count(), 0)

    def test_generic_change_permission_does_not_allow_result_change(self):
        user = self._user_with_permissions(
            "c90004a",
            QUAL_AUDITORIA_VIEW,
            QUAL_AUDITORIA_CHANGE,
        )
        self.client.force_authenticate(user)
        response = self.client.post(
            self.endpoint,
            {"dados": {"observacao": "Nao autorizado"}, "justificativa": "Sem permissao."},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)

    def test_dedicated_permission_allows_result_change(self):
        user = self._user_with_permissions(
            "c90005a",
            QUAL_AUDITORIA_VIEW,
            QUAL_AUDITORIA_RESULT_CHANGE,
        )
        self.client.force_authenticate(user)

        history = self.client.get(self.endpoint)
        self.assertEqual(history.status_code, 200, history.data)
        self.assertTrue(history.data["can_change"])

        response = self.client.post(
            self.endpoint,
            {
                "dados": {"observacao": "Alteracao autorizada"},
                "justificativa": "Usuario com permissao exclusiva.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
