# -*- coding: utf-8 -*-
"""Regressão: escopo HC Claro Formalização/Confer."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.analytics import (
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.claro_confer_scope import (
    CLARO_CONFER_HC_TEAM,
)
from apps.qualidade_operacional.services.criticidade import (
    CLIENTE_CLARO_FORMALIZACAO_ID,
)
from apps.qualidade_operacional.services.intranet_source import (
    CLARO_CONFER_CLIENT_ID,
    CLARO_CONFER_WORKFLOW_ID,
)
from apps.qualidade_operacional.services.workforce_scope import (
    clear_responsibility_index_cache,
)
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


def _read_csv(response):
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    lines = [line for line in body.splitlines() if line.strip()]
    headers = lines[0].split(";")
    rows = [line.split(";") for line in lines[1:]]
    return headers, rows


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class ClaroConferTeamScopeTests(TestCase):
    """Claro Formalização/Confer só conta com equipe Operacional/Compliance na data."""

    def setUp(self):
        cache.clear()
        clear_responsibility_index_cache()
        self.user = User.objects.create_user(
            username="claro_confer_scope", password="x", email="cc@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.agent_confer = Agent.objects.create(
            full_name="Agente Confer", user_lan_id="confer01a", active=True
        )
        self.agent_fraud = Agent.objects.create(
            full_name="Agente Fraud", user_lan_id="fraud01a", active=True
        )
        self.agent_moved = Agent.objects.create(
            full_name="Agente Movido", user_lan_id="moved01a", active=True
        )
        self.agent_other = Agent.objects.create(
            full_name="Agente Outro Cliente", user_lan_id="other01a", active=True
        )

        AgentHistory.objects.create(
            agent=self.agent_confer,
            team=CLARO_CONFER_HC_TEAM,
            start_date=date(2026, 1, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent_fraud,
            team="Operacional/Fraud",
            start_date=date(2026, 1, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent_moved,
            team="Operacional/Fraud",
            start_date=date(2026, 1, 1),
            final_date=date(2026, 6, 30),
            active=False,
        )
        AgentHistory.objects.create(
            agent=self.agent_moved,
            team=CLARO_CONFER_HC_TEAM,
            start_date=date(2026, 7, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent_other,
            team="Operacional/Fraud",
            start_date=date(2026, 1, 1),
            active=True,
        )

        event_date = date(2026, 8, 10)
        fraud_window_date = date(2026, 6, 15)
        confer_window_date = date(2026, 8, 10)

        QualidadeFalha.objects.create(
            data=event_date,
            data_analise=event_date,
            id_cliente=CLARO_CONFER_CLIENT_ID,
            id_workflow=CLARO_CONFER_WORKFLOW_ID,
            protocolo="CLARO-CONFER-OK",
            matricula="confer01a",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=event_date,
            data_analise=event_date,
            id_cliente=CLARO_CONFER_CLIENT_ID,
            id_workflow=CLARO_CONFER_WORKFLOW_ID,
            protocolo="CLARO-CONFER-OK",
            matricula="confer01a",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=event_date,
            data_analise=event_date,
            id_cliente=CLARO_CONFER_CLIENT_ID,
            id_workflow=CLARO_CONFER_WORKFLOW_ID,
            protocolo="CLARO-FRAUD-BAD",
            matricula="fraud01a",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=event_date,
            data_analise=event_date,
            id_cliente=CLARO_CONFER_CLIENT_ID,
            id_workflow=CLARO_CONFER_WORKFLOW_ID,
            protocolo="CLARO-FRAUD-BAD",
            matricula="fraud01a",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=event_date,
            data_analise=event_date,
            id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID,
            id_workflow=9999,
            protocolo="CLARO-9999-OK",
            matricula="confer01a",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=event_date,
            data_analise=event_date,
            id_cliente=6,
            id_workflow=144,
            protocolo="OUTRO-CLIENTE",
            matricula="other01a",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=event_date,
            data_analise=event_date,
            id_cliente=CLARO_CONFER_CLIENT_ID,
            id_workflow=CLARO_CONFER_WORKFLOW_ID,
            protocolo="CLARO-SEM-MAT",
            matricula="",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=fraud_window_date,
            data_analise=fraud_window_date,
            id_cliente=CLARO_CONFER_CLIENT_ID,
            id_workflow=CLARO_CONFER_WORKFLOW_ID,
            protocolo="CLARO-MOVED-FRAUD",
            matricula="moved01a",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=confer_window_date,
            data_analise=confer_window_date,
            id_cliente=CLARO_CONFER_CLIENT_ID,
            id_workflow=CLARO_CONFER_WORKFLOW_ID,
            protocolo="CLARO-MOVED-CONFER",
            matricula="moved01a",
            tipo_falha="Manual",
            source_file="test",
        )

        self.base_params = {
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        }

    def test_claro_confer_compliance_included(self):
        qs = filtered_falhas({**self.base_params, "protocolo": "CLARO-CONFER-OK"})
        self.assertEqual(qs.count(), 1)
        qs = filtered_auditados({**self.base_params, "protocolo": "CLARO-CONFER-OK"})
        self.assertEqual(qs.count(), 1)

    def test_claro_confer_fraud_team_excluded(self):
        self.assertFalse(
            filtered_falhas({**self.base_params, "protocolo": "CLARO-FRAUD-BAD"}).exists()
        )
        self.assertFalse(
            filtered_auditados(
                {**self.base_params, "protocolo": "CLARO-FRAUD-BAD"}
            ).exists()
        )

    def test_claro_9999_confer_included(self):
        self.assertEqual(
            filtered_falhas({**self.base_params, "protocolo": "CLARO-9999-OK"}).count(),
            1,
        )

    def test_other_client_unaffected(self):
        self.assertEqual(
            filtered_falhas({**self.base_params, "protocolo": "OUTRO-CLIENTE"}).count(),
            1,
        )

    def test_empty_matricula_excluded(self):
        self.assertFalse(
            filtered_falhas({**self.base_params, "protocolo": "CLARO-SEM-MAT"}).exists()
        )

    def test_team_change_respects_temporal_vigencia(self):
        self.assertFalse(
            filtered_falhas(
                {**self.base_params, "protocolo": "CLARO-MOVED-FRAUD"}
            ).exists()
        )
        self.assertEqual(
            filtered_falhas(
                {**self.base_params, "protocolo": "CLARO-MOVED-CONFER"}
            ).count(),
            1,
        )

    def test_filtered_population_counts(self):
        falhas = filtered_falhas(self.base_params)
        protocolos = set(falhas.values_list("protocolo", flat=True))
        self.assertIn("CLARO-CONFER-OK", protocolos)
        self.assertIn("CLARO-9999-OK", protocolos)
        self.assertIn("CLARO-MOVED-CONFER", protocolos)
        self.assertIn("OUTRO-CLIENTE", protocolos)
        self.assertNotIn("CLARO-FRAUD-BAD", protocolos)
        self.assertNotIn("CLARO-SEM-MAT", protocolos)
        self.assertNotIn("CLARO-MOVED-FRAUD", protocolos)

    def test_export_matches_filtered_falhas(self):
        params = {
            **self.base_params,
            "protocolo": "CLARO-CONFER-OK",
        }
        self.assertEqual(filtered_falhas(params).count(), 1)
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/falhas.csv",
            params,
        )
        self.assertEqual(res.status_code, 200)
        headers, rows = _read_csv(res)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][headers.index("Protocolo")], "CLARO-CONFER-OK")

    def test_export_excludes_fraud_team_claro(self):
        params = {
            **self.base_params,
            "protocolo": "CLARO-FRAUD-BAD",
        }
        self.assertEqual(filtered_falhas(params).count(), 0)
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/falhas.csv",
            params,
        )
        self.assertEqual(res.status_code, 200)
        _headers, rows = _read_csv(res)
        self.assertEqual(len(rows), 0)

    def test_export_auditados_matches_filtered(self):
        params = {
            **self.base_params,
            "protocolo": "CLARO-CONFER-OK",
        }
        self.assertEqual(filtered_auditados(params).count(), 1)
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/auditados.csv",
            params,
        )
        self.assertEqual(res.status_code, 200)
        _headers, rows = _read_csv(res)
        self.assertEqual(len(rows), 1)
