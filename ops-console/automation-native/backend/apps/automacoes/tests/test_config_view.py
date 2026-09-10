# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.replicacao_d1.models import ReplicacaoD1Run


class AutomacoesConfigViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            user=get_user_model().objects.create_user(username="config_view", password="x")
        )

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch(
        "apps.replicacao_d1.services.robot_config_bridge.merge_db_config_for_robot_manager",
        return_value={"agendamento_ativo": False, "run_id": "run-atual"},
    )
    @patch(
        "apps.replicacao_d1.services.config_snapshot.is_fonte_banco_ativa",
        return_value=True,
    )
    @patch("apps.automacoes.views.get_robot_manager")
    def test_listagem_geral_prioriza_config_d1_do_banco(
        self,
        get_robot_manager,
        _fonte_banco,
        merge_db_config,
        _can_access,
    ):
        robot_manager = MagicMock()
        robot_manager.robot_configs.return_value = {
            "rotina": {"headless": True},
            "replicacao_auditoria_d1": {"agendamento_ativo": True},
        }
        get_robot_manager.return_value = robot_manager

        response = self.client.get("/api/v1/automacoes/config/")

        self.assertEqual(response.status_code, 200)
        configs = response.json()["configs"]
        self.assertTrue(configs["rotina"]["headless"])
        self.assertFalse(configs["replicacao_auditoria_d1"]["agendamento_ativo"])
        self.assertEqual(configs["replicacao_auditoria_d1"]["run_id"], "run-atual")
        merge_db_config.assert_called_once_with(
            robot_manager,
            mode="replicacao_auditoria_d1",
            active_only=True,
        )

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.automacoes.views.can_configure_automacoes", return_value=False)
    @patch(
        "apps.replicacao_d1.services.config_snapshot.is_fonte_banco_ativa",
        return_value=True,
    )
    @patch("apps.automacoes.views.get_robot_manager")
    def test_opcoes_temporarias_d1_nao_exigem_configuracao_permanente(
        self,
        get_robot_manager,
        _fonte_banco,
        can_configure,
        _can_access,
    ):
        robot_manager = MagicMock()
        robot_manager.update_robot_config.return_value = (
            True,
            "Opções atualizadas",
            {
                "replicacao_auditoria_d1": {
                    "apenas_planejamento": True,
                    "headless": True,
                    "max_workers": 2,
                    "output_dir": "",
                }
            },
        )
        get_robot_manager.return_value = robot_manager

        response = self.client.post(
            "/api/v1/automacoes/config/",
            {
                "mode": "replicacao_auditoria_d1",
                "config": {
                    "apenas_planejamento": True,
                    "headless": True,
                    "max_workers": 2,
                    "output_dir": "",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        can_configure.assert_not_called()
        robot_manager.update_robot_config.assert_called_once()

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.automacoes.views.get_robot_manager")
    def test_execucao_d1_pendente_e_bloqueada_antes_do_selenium(self, get_robot_manager, _access):
        run = ReplicacaoD1Run.objects.create(
            run_id="20260810_180000",
            data_referencia_d1="2026-08-09",
            status_canonical=ReplicacaoD1Run.STATUS_PLANNED,
            validation_status=ReplicacaoD1Run.VALIDATION_PENDING,
            plan_hash="a" * 64,
        )

        response = self.client.post(
            "/api/v1/automacoes/start/",
            {
                "mode": "replicacao_auditoria_d1",
                "robot_config": {"run_id": run.run_id, "apenas_planejamento": False},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "PLAN_NOT_APPROVED")
        get_robot_manager.return_value.start.assert_not_called()

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.automacoes.views.get_robot_manager")
    def test_execucao_d1_aprovada_chega_ao_robot_manager(self, get_robot_manager, _access):
        run = ReplicacaoD1Run.objects.create(
            run_id="20260810_181000",
            data_referencia_d1="2026-08-09",
            status_canonical=ReplicacaoD1Run.STATUS_PLANNED,
            validation_status=ReplicacaoD1Run.VALIDATION_APPROVED,
            plan_hash="b" * 64,
        )
        manager = get_robot_manager.return_value
        manager.start.return_value = (True, "iniciado")
        manager.status.return_value = {}
        manager.credentials_status.return_value = {}

        response = self.client.post(
            "/api/v1/automacoes/start/",
            {
                "mode": "replicacao_auditoria_d1",
                "robot_config": {"run_id": run.run_id, "apenas_planejamento": False},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        manager.start.assert_called_once()

    @patch("apps.automacoes.views.can_access_automacoes", return_value=True)
    @patch("apps.automacoes.views.get_robot_manager")
    def test_execucao_d1_sem_run_id_usa_ultimo_plano_aprovado(self, get_robot_manager, _access):
        ReplicacaoD1Run.objects.create(
            run_id="20260810_180000",
            data_referencia_d1="2026-08-09",
            status_canonical=ReplicacaoD1Run.STATUS_PLANNED,
            validation_status=ReplicacaoD1Run.VALIDATION_APPROVED,
            plan_hash="a" * 64,
        )
        ReplicacaoD1Run.objects.create(
            run_id="20260810_190000",
            data_referencia_d1="2026-08-10",
            status_canonical=ReplicacaoD1Run.STATUS_PLANNED,
            validation_status=ReplicacaoD1Run.VALIDATION_PENDING,
            plan_hash="c" * 64,
        )
        manager = get_robot_manager.return_value
        manager.start.return_value = (True, "iniciado")
        manager.status.return_value = {}
        manager.credentials_status.return_value = {}

        response = self.client.post(
            "/api/v1/automacoes/start/",
            {
                "mode": "replicacao_auditoria_d1",
                "robot_config": {"apenas_planejamento": False},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        manager.start.assert_called_once()
        robot_config = manager.start.call_args.kwargs.get("robot_config") or {}
        self.assertEqual(robot_config.get("run_id"), "20260810_180000")
