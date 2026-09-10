# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.replicacao_d1.models import (
    ReplicacaoD1Categoria,
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigHistorico,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1ExecutionEvent,
    ReplicacaoD1FonteLote,
    ReplicacaoD1Run,
    ReplicacaoD1SchedulerState,
    ReplicacaoD1Segmento,
    ReplicacaoD1Workflow,
)
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

User = get_user_model()

BASE = "/api/v1/replicacao-d1/config"


def _user_has_permission(user, code, **_kwargs):
    if user.username in ("d1_reader", "d1_config") and code == R.PLANEJAMENTO_AUTOMACAO_VIEW:
        return True
    if user.username in ("d1_config", "d1_import") and code == R.PLANEJAMENTO_AUTOMACAO_CONFIGURE:
        return True
    return False


def _can_configure(user):
    return user.username in ("d1_config", "d1_import")


@override_settings(ACCESS_ENFORCEMENT=True)
class ConfigPageApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.reader = User.objects.create_user(
            username="d1_reader", email="d1_reader@test.local", password="x"
        )
        self.configurator = User.objects.create_user(
            username="d1_config", email="d1_config@test.local", password="x"
        )
        self.nobody = User.objects.create_user(
            username="d1_nobody", email="d1_nobody@test.local", password="x"
        )

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_segmento_crud_lifecycle(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)

        create_resp = self.client.post(f"{BASE}/segmentos/", {"nome": "Varejo"}, format="json")
        self.assertEqual(create_resp.status_code, 201)
        seg_id = create_resp.data["id"]
        self.assertEqual(create_resp.data["nome"], "Varejo")
        self.assertTrue(create_resp.data["ativo"])

        list_resp = self.client.get(f"{BASE}/segmentos/")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.data["count"], 1)

        patch_resp = self.client.patch(f"{BASE}/segmentos/{seg_id}/", {"nome": "Varejo Premium"}, format="json")
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.data["nome"], "Varejo Premium")

        delete_resp = self.client.delete(f"{BASE}/segmentos/{seg_id}/")
        self.assertEqual(delete_resp.status_code, 204)

        detail_resp = self.client.get(f"{BASE}/segmentos/{seg_id}/")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertFalse(detail_resp.data["ativo"])

        reactivate_resp = self.client.patch(f"{BASE}/segmentos/{seg_id}/", {"ativo": True}, format="json")
        self.assertEqual(reactivate_resp.status_code, 200)
        self.assertTrue(reactivate_resp.data["ativo"])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_categoria_crud_with_segmento(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        segmento = ReplicacaoD1Segmento.objects.create(nome="Corporativo")

        create_resp = self.client.post(
            f"{BASE}/categorias/",
            {"nome": "Premium", "segmento": segmento.pk},
            format="json",
        )
        self.assertEqual(create_resp.status_code, 201)
        cat_id = create_resp.data["id"]
        self.assertEqual(create_resp.data["segmento"], segmento.pk)
        self.assertEqual(create_resp.data["segmento_nome"], "Corporativo")

        filter_resp = self.client.get(f"{BASE}/categorias/", {"segmento": segmento.pk})
        self.assertEqual(filter_resp.status_code, 200)
        self.assertEqual(filter_resp.data["count"], 1)

        delete_resp = self.client.delete(f"{BASE}/categorias/{cat_id}/")
        self.assertEqual(delete_resp.status_code, 204)

        detail_resp = self.client.get(f"{BASE}/categorias/{cat_id}/")
        self.assertFalse(detail_resp.data["ativo"])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_resumo_returns_counts(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        segmento = ReplicacaoD1Segmento.objects.create(nome="Seg A")
        categoria = ReplicacaoD1Categoria.objects.create(nome="Cat A", segmento=segmento)
        ReplicacaoD1Cliente.objects.create(nome="Cliente A", segmento=segmento, categoria=categoria, ativo=True)
        ReplicacaoD1Cliente.objects.create(nome="Cliente B", ativo=False)
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Ativo",
            nome_d1="WF D1",
            nome_selenium="WF Sel",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Pendente",
            status=ReplicacaoD1Workflow.STATUS_PENDENTE,
        )
        ReplicacaoD1EscalaDia.objects.create(data=date(2026, 8, 1), auditores_brflow=2, auditores_case=1)

        resp = self.client.get(f"{BASE}/resumo/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("contagens", resp.data)
        self.assertEqual(resp.data["contagens"]["clientes"]["ativos"], 1)
        self.assertEqual(resp.data["contagens"]["clientes"]["inativos"], 1)
        self.assertEqual(resp.data["contagens"]["workflows"]["pendentes"], 1)
        self.assertEqual(resp.data["contagens"]["segmentos"]["ativos"], 1)
        self.assertEqual(resp.data["contagens"]["categorias"]["ativos"], 1)
        self.assertEqual(resp.data["escala"]["total_dias"], 1)
        self.assertIsInstance(resp.data["validation_errors"], list)
        self.assertIsInstance(resp.data["avisos"], list)
        self.assertIn("Server-Timing", resp)

        cached_resp = self.client.get(f"{BASE}/resumo/")
        self.assertEqual(cached_resp.status_code, 200)
        self.assertIn('desc="hit"', cached_resp["Server-Timing"])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_resumo_returns_operational_cards_and_scheduler_health(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.agendamento_ativo = True
        geral.save(update_fields=["agendamento_ativo", "updated_at"])
        source = ReplicacaoD1FonteLote.objects.create(
            report_date=date(2026, 8, 9),
            status=ReplicacaoD1FonteLote.STATUS_READY,
            content_hash="a" * 64,
            rows_read=120,
            rows_valid=117,
            rows_duplicate=2,
            rows_rejected=1,
            finished_at=timezone.now(),
        )
        RotinaDetalhadoBrutoRecord.objects.bulk_create([
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 10), protocolo=101, workflow="WF A"
            ),
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 10), protocolo=102, workflow="WF A"
            ),
        ])
        ReplicacaoD1Run.objects.create(
            run_id="20260810_pending",
            status_canonical=ReplicacaoD1Run.STATUS_PLANNED,
            validation_status=ReplicacaoD1Run.VALIDATION_PENDING,
            data_referencia_d1=date(2026, 8, 9),
            protocolos_total=50,
            workflows_total=3,
            plan_hash="b" * 64,
            plan_warnings=["Atenção"],
            source_batch=source,
        )
        ReplicacaoD1Run.objects.create(
            run_id="20260809_completed",
            status_canonical=ReplicacaoD1Run.STATUS_COMPLETED,
            validation_status=ReplicacaoD1Run.VALIDATION_APPROVED,
            data_referencia_d1=date(2026, 8, 8),
            protocolos_total=45,
            workflows_total=2,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            source_batch=source,
        )
        ReplicacaoD1SchedulerState.objects.create(
            key="replicacao_d1",
            state={
                "status": "running",
                "enabled": True,
                "last_heartbeat": timezone.now().isoformat(),
                "next_execution_at": timezone.now().isoformat(),
            },
        )

        resp = self.client.get(f"{BASE}/resumo/")

        self.assertEqual(resp.status_code, 200)
        operation = resp.data["operacao"]
        self.assertEqual(operation["planos_pendentes"], 1)
        self.assertEqual(operation["plano_pendente_recente"]["run_id"], "20260810_pending")
        self.assertEqual(operation["plano_pendente_recente"]["warnings_count"], 1)
        self.assertEqual(operation["ultima_execucao"]["run_id"], "20260809_completed")
        self.assertEqual(operation["ultima_fonte"]["source"], "rotina_detalhado_bruto_record")
        self.assertEqual(operation["ultima_fonte"]["report_date"], date(2026, 8, 10))
        self.assertEqual(operation["ultima_fonte"]["row_count"], 2)
        self.assertEqual(operation["agendamento"]["runtime_status"], "healthy")
        self.assertTrue(operation["agendamento"]["configurado"])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_resumo_does_not_claim_scheduler_active_without_heartbeat(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.agendamento_ativo = True
        geral.save(update_fields=["agendamento_ativo", "updated_at"])

        resp = self.client.get(f"{BASE}/resumo/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["operacao"]["agendamento"]["runtime_status"], "unknown")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_resumo_marks_stopped_scheduler_as_stale_not_disabled(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.agendamento_ativo = True
        geral.save(update_fields=["agendamento_ativo", "updated_at"])
        ReplicacaoD1SchedulerState.objects.create(
            key="replicacao_d1",
            state={
                "status": "stopped",
                "enabled": False,
                "last_heartbeat": (timezone.now() - timedelta(days=3)).isoformat(),
            },
        )

        resp = self.client.get(f"{BASE}/resumo/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["operacao"]["agendamento"]["runtime_status"], "stale")
        self.assertTrue(resp.data["operacao"]["agendamento"]["configurado"])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_resumo_does_not_show_false_failure_from_unsynced_database_run(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        run = ReplicacaoD1Run.objects.create(
            run_id="20260810_unsynced",
            status_canonical=ReplicacaoD1Run.STATUS_FAILED,
            validation_status=ReplicacaoD1Run.VALIDATION_APPROVED,
            data_referencia_d1=date(2026, 8, 9),
            protocolos_total=50,
            workflows_total=4,
        )
        ReplicacaoD1ExecutionEvent.objects.create(
            run=run,
            phase="manifest",
            status="failed",
            payload={
                "result": {
                    "started_at": "2026-08-10T20:00:00-03:00",
                    "finished_at": "2026-08-10T20:10:00-03:00",
                }
            },
        )
        ReplicacaoD1ExecutionEvent.objects.create(
            run=run,
            phase="close",
            status="failed",
            payload={"workflows_total": 4, "workflows_success": 0, "workflows_failed": 0},
        )

        resp = self.client.get(f"{BASE}/resumo/")

        self.assertEqual(resp.status_code, 200)
        execution = resp.data["operacao"]["ultima_execucao"]
        self.assertEqual(execution["run_id"], run.run_id)
        self.assertEqual(execution["status"], ReplicacaoD1Run.STATUS_PARTIAL)
        self.assertIsNotNone(execution["started_at"])
        self.assertIsNotNone(execution["finished_at"])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_reader_can_get_resumo_and_segmentos_not_post(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        self.assertEqual(self.client.get(f"{BASE}/resumo/").status_code, 200)
        self.assertEqual(self.client.get(f"{BASE}/segmentos/").status_code, 200)
        post_resp = self.client.post(f"{BASE}/segmentos/", {"nome": "X"}, format="json")
        self.assertEqual(post_resp.status_code, 403)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_reader_can_generate_projection(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)

        resp = self.client.get(f"{BASE}/projecao/", {"competencia": "2026-08"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["competencia"], "2026-08")
        self.assertIn("projecao_fim_mes", resp.data)
        self.assertIn("workflows", resp.data)

        invalid_resp = self.client.get(f"{BASE}/projecao/", {"competencia": "2026-13"})
        self.assertEqual(invalid_resp.status_code, 400)
        zero_year_resp = self.client.get(f"{BASE}/projecao/", {"competencia": "0000-01"})
        self.assertEqual(zero_year_resp.status_code, 400)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_calculadora_hides_legacy_duplicate_fields(self, _mock_perm, _mock_cfg):
        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.calculadora_params = {
            "dias_uteis": 24,
            "meta_produ": 300,
            "confianca": 0.95,
            "margin_high": 0.01,
            "margin_low": 0.02,
        }
        geral.save(update_fields=["calculadora_params", "updated_at"])
        self._auth(self.reader)

        resp = self.client.get(f"{BASE}/calculadora/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(resp.data["calculadora_params"]),
            {"confianca", "margin_high", "margin_low"},
        )
        self.assertNotIn("dias_uteis", resp.data["calculadora_params"])
        self.assertNotIn("meta_produ", resp.data["calculadora_params"])
        self.assertIn("meta_produ_diaria", resp.data)
        self.assertIn("meta_produ_diaria_case", resp.data)
        self.assertIn("meta_produ_diaria_bio", resp.data)
        self.assertIn("meta_produ_diaria_redoc", resp.data)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_calculadora_persists_bio_redoc_meta(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        resp = self.client.patch(
            f"{BASE}/calculadora/",
            {
                "meta_produ_diaria_bio": "412.50",
                "meta_produ_diaria_redoc": "388",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(float(resp.data["meta_produ_diaria_bio"]), 412.50)
        self.assertEqual(float(resp.data["meta_produ_diaria_redoc"]), 388.0)

        geral = ReplicacaoD1ConfigGeral.get_solo()
        self.assertEqual(float(geral.meta_produ_diaria_bio), 412.50)
        self.assertEqual(float(geral.meta_produ_diaria_redoc), 388.0)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_escala_includes_bio_redoc_auditores(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 2),
            auditores_brflow=1,
            auditores_case=2,
            auditores_bio=3,
            auditores_redoc=4,
        )
        resp = self.client.get(f"{BASE}/escala/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data["results"][0]
        self.assertEqual(row["auditores_bio"], 3)
        self.assertEqual(row["auditores_redoc"], 4)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_workflow_nome_regra_brflow_redoc(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        resp = self.client.post(
            f"{BASE}/workflows/",
            {
                "nome_canonico": "WF Redoc Regra",
                "fila": "Redoc",
                "nome_regra_brflow": "Regra Especial",
                "status": ReplicacaoD1Workflow.STATUS_ATIVO,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["nome_regra_brflow"], "Regra Especial")

        list_resp = self.client.get(f"{BASE}/workflows/", {"q": "Redoc Regra"})
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.data["results"][0]["nome_regra_brflow"], "Regra Especial")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_workflow_nome_regra_brflow_opcional_g_auditoria(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        resp = self.client.post(
            f"{BASE}/workflows/",
            {
                "nome_canonico": "WF G Com Regra",
                "fila": "G auditoria",
                "nome_regra_brflow": "Regra Opcional G",
                "status": ReplicacaoD1Workflow.STATUS_ATIVO,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["nome_regra_brflow"], "Regra Opcional G")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_reader_can_export_complete_csv(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        ReplicacaoD1Cliente.objects.create(nome="Cliente Exportação", meta_mensal=321)

        resp = self.client.get(f"{BASE}/export/clientes/")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        content = resp.content.decode("utf-8-sig")
        self.assertIn("Cliente Exportação", content)
        self.assertIn("meta_mensal", content.splitlines()[0])

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_configurator_can_post_segmento(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        resp = self.client.post(f"{BASE}/segmentos/", {"nome": "Novo"}, format="json")
        self.assertEqual(resp.status_code, 201)

    @patch("apps.access.permissions.user_has_permission", return_value=False)
    def test_nobody_gets_403(self, _mock):
        self._auth(self.nobody)
        self.assertEqual(self.client.get(f"{BASE}/resumo/").status_code, 403)
        self.assertEqual(self.client.get(f"{BASE}/segmentos/").status_code, 403)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_workflow_q_filter(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        ReplicacaoD1Workflow.objects.create(nome_canonico="Alpha Workflow", fila="G auditoria")
        ReplicacaoD1Workflow.objects.create(nome_canonico="Beta Other", fila="Case")

        resp = self.client.get(f"{BASE}/workflows/", {"q": "alpha"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["nome_canonico"], "Alpha Workflow")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_workflow_list_includes_cliente_segmento_categoria(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        seg = ReplicacaoD1Segmento.objects.create(nome="Seg WF")
        cat = ReplicacaoD1Categoria.objects.create(nome="Cat WF", segmento=seg)
        cliente = ReplicacaoD1Cliente.objects.create(
            nome="Cliente WF",
            segmento=seg,
            categoria=cat,
            segmento_nome=seg.nome,
            categoria_nome=cat.nome,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Classificado",
            cliente=cliente,
            fila="G auditoria",
        )

        resp = self.client.get(f"{BASE}/workflows/", {"q": "classificado"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        row = resp.data["results"][0]
        self.assertEqual(row["segmento_nome"], "Seg WF")
        self.assertEqual(row["categoria_nome"], "Cat WF")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_workflow_cliente_name_filter(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        cliente_a = ReplicacaoD1Cliente.objects.create(nome="Cliente Alpha")
        cliente_b = ReplicacaoD1Cliente.objects.create(nome="Cliente Beta")
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Alpha",
            cliente=cliente_a,
            fila="G auditoria",
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Beta",
            cliente=cliente_b,
            fila="G auditoria",
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Sem Cliente",
            cliente=None,
            fila="G auditoria",
        )

        by_name = self.client.get(f"{BASE}/workflows/", {"cliente": "Cliente Alpha"})
        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(by_name.data["count"], 1)
        self.assertEqual(by_name.data["results"][0]["nome_canonico"], "WF Alpha")

        by_null = self.client.get(f"{BASE}/workflows/", {"cliente": "—"})
        self.assertEqual(by_null.status_code, 200)
        self.assertEqual(by_null.data["count"], 1)
        self.assertEqual(by_null.data["results"][0]["nome_canonico"], "WF Sem Cliente")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_workflow_column_filter_options_lists_all_clients(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        cliente = ReplicacaoD1Cliente.objects.create(nome="SEGURANÇA CORPORATIVA - GRUPO BRADESCO")
        for idx in range(55):
            ReplicacaoD1Workflow.objects.create(
                nome_canonico=f"WF Paginado {idx}",
                cliente=cliente,
                fila="G auditoria",
            )

        options = self.client.get(f"{BASE}/workflows/column-filter-options/")
        self.assertEqual(options.status_code, 200)
        self.assertIn("SEGURANÇA CORPORATIVA - GRUPO BRADESCO", options.data["cliente_nome"])

        filtered = self.client.get(
            f"{BASE}/workflows/",
            {"cliente": "SEGURANÇA CORPORATIVA - GRUPO BRADESCO", "page_size": 10},
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.data["count"], 55)
        self.assertTrue(all(row["cliente_nome"] == cliente.nome for row in filtered.data["results"]))

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_cliente_segmento_filter(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        seg_a = ReplicacaoD1Segmento.objects.create(nome="Seg Filter A")
        seg_b = ReplicacaoD1Segmento.objects.create(nome="Seg Filter B")
        ReplicacaoD1Cliente.objects.create(nome="Cli A", segmento=seg_a)
        ReplicacaoD1Cliente.objects.create(nome="Cli B", segmento=seg_b)

        resp = self.client.get(f"{BASE}/clientes/", {"segmento": seg_a.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["nome"], "Cli A")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_list_ordering_supports_regular_and_calculated_columns(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        seg_maior = ReplicacaoD1Segmento.objects.create(nome="Segmento Maior")
        seg_menor = ReplicacaoD1Segmento.objects.create(nome="Segmento Menor")
        cliente_menor = ReplicacaoD1Cliente.objects.create(
            nome="Cliente Menor",
            segmento=seg_menor,
            meta_mensal=100,
        )
        ReplicacaoD1Cliente.objects.create(
            nome="Cliente Maior",
            segmento=seg_maior,
            meta_mensal=300,
        )
        ReplicacaoD1Cliente.objects.create(nome="Cliente Extra", segmento=seg_maior, meta_mensal=200)
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Amostra 25",
            cliente=cliente_menor,
            amostra_pct_especial=25,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Amostra 100",
            cliente=cliente_menor,
            amostra_100=True,
        )

        clientes = self.client.get(f"{BASE}/clientes/", {"ordering": "-meta_mensal"})
        self.assertEqual(clientes.status_code, 200)
        self.assertEqual(
            [item["nome"] for item in clientes.data["results"]],
            ["Cliente Maior", "Cliente Extra", "Cliente Menor"],
        )

        workflows = self.client.get(f"{BASE}/workflows/", {"ordering": "-amostra_pct"})
        self.assertEqual(workflows.status_code, 200)
        self.assertEqual(
            [item["nome_canonico"] for item in workflows.data["results"]],
            ["WF Amostra 100", "WF Amostra 25"],
        )

        segmentos = self.client.get(f"{BASE}/segmentos/", {"ordering": "-clientes_count"})
        self.assertEqual(segmentos.status_code, 200)
        self.assertEqual(
            [item["nome"] for item in segmentos.data["results"]],
            ["Segmento Maior", "Segmento Menor"],
        )

        ReplicacaoD1ConfigHistorico.objects.create(
            entidade="Alteracao Ampla",
            entidade_id="1",
            operacao="update",
            usuario=self.reader,
            valores_anteriores={},
            valores_novos={"campo_a": 1, "campo_b": 2},
        )
        ReplicacaoD1ConfigHistorico.objects.create(
            entidade="Alteracao Simples",
            entidade_id="2",
            operacao="update",
            usuario=self.reader,
            valores_anteriores={},
            valores_novos={"campo_a": 1},
        )
        historico = self.client.get(f"{BASE}/historico/", {"ordering": "-campos"})
        self.assertEqual(historico.status_code, 200)
        self.assertEqual(
            [item["entidade"] for item in historico.data["results"]],
            ["Alteracao Ampla", "Alteracao Simples"],
        )

        invalid = self.client.get(f"{BASE}/clientes/", {"ordering": "campo_inexistente"})
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(
            [item["nome"] for item in invalid.data["results"]],
            ["Cliente Extra", "Cliente Maior", "Cliente Menor"],
        )

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_escala_invalid_date_returns_400(self, _mock_perm, _mock_cfg):
        self._auth(self.reader)
        resp = self.client.get(f"{BASE}/escala/", {"data_inicio": "not-a-date"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("data_inicio", resp.data)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_rename_cliente_recalculates_chave_and_syncs_names(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        seg = ReplicacaoD1Segmento.objects.create(nome="Seg Sync")
        cat = ReplicacaoD1Categoria.objects.create(nome="Cat Sync", segmento=seg)
        cliente = ReplicacaoD1Cliente.objects.create(
            nome="Cliente Antigo",
            segmento=seg,
            categoria=cat,
            segmento_nome="Legado Errado",
            categoria_nome="Cat Legada",
        )
        old_chave = cliente.chave_normalizada

        resp = self.client.patch(
            f"{BASE}/clientes/{cliente.pk}/",
            {"nome": "Cliente Novo Nome", "segmento": seg.pk, "categoria": cat.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        cliente.refresh_from_db()
        self.assertNotEqual(cliente.chave_normalizada, old_chave)
        self.assertEqual(cliente.chave_normalizada, "cliente novo nome")
        self.assertEqual(cliente.segmento_nome, "Seg Sync")
        self.assertEqual(cliente.categoria_nome, "Cat Sync")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_rename_workflow_recalculates_chave(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        wf = ReplicacaoD1Workflow.objects.create(nome_canonico="WF Antigo", fila="G auditoria")
        resp = self.client.patch(
            f"{BASE}/workflows/{wf.pk}/",
            {"nome_canonico": "WF Renomeado"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        wf.refresh_from_db()
        self.assertEqual(wf.chave_normalizada, "wf renomeado")

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_workflow_bulk_status_activate_and_deactivate(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        ativo = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Ativo Bulk",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        inativo = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Inativo Bulk",
            status=ReplicacaoD1Workflow.STATUS_INATIVO,
        )
        pendente = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Pendente Bulk",
            status=ReplicacaoD1Workflow.STATUS_PENDENTE,
        )

        deactivate_resp = self.client.post(
            f"{BASE}/workflows/bulk-status/",
            {"workflow_ids": [ativo.pk, inativo.pk], "status": "INATIVO"},
            format="json",
        )
        self.assertEqual(deactivate_resp.status_code, 200)
        self.assertEqual(deactivate_resp.data["updated"], 2)
        ativo.refresh_from_db()
        inativo.refresh_from_db()
        self.assertEqual(ativo.status, ReplicacaoD1Workflow.STATUS_INATIVO)
        self.assertFalse(ativo.ativo)

        activate_resp = self.client.post(
            f"{BASE}/workflows/bulk-status/",
            {"workflow_ids": [ativo.pk, pendente.pk], "status": "ATIVO"},
            format="json",
        )
        self.assertEqual(activate_resp.status_code, 200)
        self.assertEqual(activate_resp.data["updated"], 1)
        self.assertEqual(len(activate_resp.data["skipped"]), 1)
        ativo.refresh_from_db()
        pendente.refresh_from_db()
        self.assertEqual(ativo.status, ReplicacaoD1Workflow.STATUS_ATIVO)
        self.assertTrue(ativo.ativo)
        self.assertEqual(pendente.status, ReplicacaoD1Workflow.STATUS_PENDENTE)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_duplicate_nome_returns_400(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        ReplicacaoD1Segmento.objects.create(nome="Duplicado")
        resp = self.client.post(f"{BASE}/segmentos/", {"nome": "duplicado"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nome", resp.data)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_fonte_banco_ativa_ignored_on_geral_patch(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        from apps.replicacao_d1.models import ReplicacaoD1ConfigGeral

        geral = ReplicacaoD1ConfigGeral.get_solo()
        self.assertFalse(geral.fonte_banco_ativa)

        resp = self.client.patch(
            f"{BASE}/geral/",
            {"fonte_banco_ativa": True, "seed": 123},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        geral.refresh_from_db()
        self.assertFalse(geral.fonte_banco_ativa)
        self.assertEqual(geral.seed, 123)
        self.assertFalse(resp.data.get("fonte_banco_ativa"))

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_ledger_purge_runs(self, _mock_perm, _mock_cfg):
        from apps.replicacao_d1.models import ReplicacaoD1LedgerConsumo
        from apps.replicacao_d1.services.ledger import ORIGEM_CONFIRMADO, registrar_consumo_meta_run

        self._auth(self.configurator)
        run_id = "20260805_120000"
        registrar_consumo_meta_run(
            competencia="2026-08",
            run_id=run_id,
            consumo_por_workflow={"wf alpha": 10},
            origem=ORIGEM_CONFIRMADO,
        )
        resp = self.client.post(
            f"{BASE}/ledger/purge-runs/",
            {"run_ids": [run_id]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["deleted"], 1)
        self.assertFalse(ReplicacaoD1LedgerConsumo.objects.filter(run_id=run_id).exists())

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_retroativo_get_patch_and_resumo(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        segmento = ReplicacaoD1Segmento.objects.create(nome="Seg Retro")
        categoria = ReplicacaoD1Categoria.objects.create(nome="Cat Retro", segmento=segmento)
        cliente = ReplicacaoD1Cliente.objects.create(
            nome="Cliente Retro",
            segmento=segmento,
            categoria=categoria,
            ativo=True,
        )
        workflow = ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Retro",
            nome_d1="WF Retro D1",
            nome_selenium="WF Retro Sel",
            cliente=cliente,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )

        get_resp = self.client.get(f"{BASE}/retroativo/")
        self.assertEqual(get_resp.status_code, 200)
        self.assertFalse(get_resp.data["retroativo_ativo"])

        patch_resp = self.client.patch(
            f"{BASE}/retroativo/",
            {
                "retroativo_ativo": True,
                "retroativo_data_inicio": "2026-08-01",
                "retroativo_data_fim": "2026-08-07",
                "workflow_ids": [workflow.pk],
            },
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertTrue(patch_resp.data["retroativo_ativo"])
        self.assertEqual(patch_resp.data["workflow_ids"], [workflow.pk])
        self.assertEqual(patch_resp.data["workflows_total"], 1)

        resumo = self.client.get(f"{BASE}/resumo/")
        self.assertEqual(resumo.status_code, 200)
        self.assertTrue(resumo.data["retroativo"]["retroativo_ativo"])
        self.assertEqual(resumo.data["retroativo"]["workflows_total"], 1)

    @patch("apps.replicacao_d1.views.config_api.can_configure_automacoes", side_effect=_can_configure)
    @patch("apps.access.permissions.user_has_permission", side_effect=_user_has_permission)
    def test_fallback_parquet_dias_ausentes_geral_and_resumo(self, _mock_perm, _mock_cfg):
        self._auth(self.configurator)
        geral = ReplicacaoD1ConfigGeral.get_solo()
        self.assertFalse(geral.fallback_parquet_dias_ausentes)

        get_resp = self.client.get(f"{BASE}/geral/")
        self.assertEqual(get_resp.status_code, 200)
        self.assertFalse(get_resp.data["fallback_parquet_dias_ausentes"])

        patch_resp = self.client.patch(
            f"{BASE}/geral/",
            {"fallback_parquet_dias_ausentes": True},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertTrue(patch_resp.data["fallback_parquet_dias_ausentes"])
        geral.refresh_from_db()
        self.assertTrue(geral.fallback_parquet_dias_ausentes)

        geral.fonte_banco_ativa = True
        geral.save(update_fields=["fonte_banco_ativa", "updated_at"])
        resumo = self.client.get(f"{BASE}/resumo/")
        self.assertEqual(resumo.status_code, 200)
        self.assertTrue(resumo.data["fallback_parquet_dias_ausentes"])
        self.assertTrue(
            any("Fallback parquet ativo" in aviso for aviso in resumo.data.get("avisos", []))
        )

        invalid = self.client.patch(
            f"{BASE}/retroativo/",
            {"retroativo_data_inicio": "2026-08-10", "retroativo_data_fim": "2026-08-01"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
