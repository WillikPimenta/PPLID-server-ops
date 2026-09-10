# -*- coding: utf-8 -*-
"""Regressão: atribuição temporal de líderes via AgentHistory."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.analytics import (
    LEADER_TEMPORAL_UNATTRIBUTED_KEY,
    build_leader_hierarchy,
    build_ranking,
    filtered_falhas,
)
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.performance_cache import _PAYLOAD_SCHEMA
from apps.qualidade_operacional.services.queries import apply_common_filters
from apps.qualidade_operacional.services.workforce_scope import (
    clear_responsibility_index_cache,
    resolve_leader_for_date,
)
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class LeaderTemporalScopeTests(TestCase):
    """Cenário central: troca de líder no meio do período."""

    def setUp(self):
        cache.clear()
        clear_responsibility_index_cache()
        self.user = User.objects.create_user(
            username="ltemporal_user", password="x", email="lt@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.leader_old = Agent.objects.create(
            full_name="Líder Antigo", user_lan_id="lider_antigo", active=True
        )
        self.leader_new = Agent.objects.create(
            full_name="Líder Novo", user_lan_id="lider_novo", active=True
        )
        self.agent = Agent.objects.create(
            full_name="Agente Transferido", user_lan_id="ltemporal01a", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader_old,
            team="Operacao Teste",
            start_date=date(2026, 7, 1),
            final_date=date(2026, 7, 15),
            active=False,
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader_new,
            team="Operacao Teste",
            start_date=date(2026, 7, 16),
            active=True,
        )

        QualidadeAuditado.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="ltemporal01a",
            protocolo="ANTES",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 20),
            data_analise=date(2026, 7, 20),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="ltemporal01a",
            protocolo="DEPOIS",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="ltemporal01a",
            protocolo="ANTES",
            tipo_falha="Manual",
            lider="Líder Errado Importado",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 20),
            data_analise=date(2026, 7, 20),
            id_cliente=6,
            id_workflow=144,
            matricula="ltemporal01a",
            protocolo="DEPOIS",
            tipo_falha="Manual",
            lider="Líder Errado Importado",
            source_file="test",
        )

        self.base_params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "workforce_only": "1",
        }

    def _protocols(self, params):
        qs = apply_common_filters(
            QualidadeAuditado.objects.all(),
            params,
            date_field="data",
        )
        return sorted(qs.values_list("protocolo", flat=True))

    def test_filter_old_leader_returns_only_before_transfer(self):
        protocols = self._protocols({**self.base_params, "lider": "Líder Antigo"})
        self.assertEqual(protocols, ["ANTES"])

    def test_filter_new_leader_returns_only_after_transfer(self):
        protocols = self._protocols({**self.base_params, "lider": "Líder Novo"})
        self.assertEqual(protocols, ["DEPOIS"])

    def test_inclusive_boundary_dates(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 15),
            data_analise=date(2026, 7, 15),
            id_cliente=6,
            id_workflow=144,
            matricula="ltemporal01a",
            protocolo="FIM_VIG",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 16),
            data_analise=date(2026, 7, 16),
            id_cliente=6,
            id_workflow=144,
            matricula="ltemporal01a",
            protocolo="INI_VIG",
            source_file="test",
        )
        old_prots = self._protocols({**self.base_params, "lider": "Líder Antigo"})
        new_prots = self._protocols({**self.base_params, "lider": "Líder Novo"})
        self.assertIn("FIM_VIG", old_prots)
        self.assertIn("INI_VIG", new_prots)

    def test_period_after_transfer_excludes_old_leader(self):
        params = {
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "lider": "Líder Antigo",
            "workforce_only": "1",
        }
        qs = apply_common_filters(
            QualidadeAuditado.objects.all(), params, date_field="data"
        )
        self.assertEqual(qs.count(), 0)

    def test_leader_hierarchy_two_segments_consolidated_ranking_one_row(self):
        hierarchy = build_leader_hierarchy(self.base_params)
        segments = [
            row for row in hierarchy["results"]
            if row.get("key") != LEADER_TEMPORAL_UNATTRIBUTED_KEY
        ]
        self.assertEqual(len(segments), 2)
        leaders = {row["lider"] for row in segments}
        self.assertEqual(leaders, {"Líder Antigo", "Líder Novo"})
        total_aud = sum(row["auditados"] for row in hierarchy["results"])
        ranking = build_ranking({**self.base_params, "by": "agente", "responsibility_scope": "lider"})
        agent_rows = [r for r in ranking["results"] if r.get("matricula") == "ltemporal01a"]
        self.assertEqual(len(agent_rows), 1)
        self.assertEqual(agent_rows[0]["auditados"], total_aud)

    def test_leader_hierarchy_exposes_unattributed_rows_and_dashboard_kpis_match(self):
        from apps.qualidade_operacional.services.analytics import (
            LEADER_TEMPORAL_UNATTRIBUTED_KEY,
            LEADER_TEMPORAL_UNATTRIBUTED_LABEL,
        )

        AgentHistory.objects.filter(agent=self.agent, leader=self.leader_new).update(
            start_date=date(2026, 7, 20),
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 17),
            data_analise=date(2026, 7, 17),
            id_cliente=6,
            id_workflow=144,
            matricula="ltemporal01a",
            protocolo="SEM-LIDER-DATA",
            source_file="test",
        )
        hierarchy = build_leader_hierarchy(self.base_params)
        unattributed = [
            row for row in hierarchy["results"]
            if row.get("key") == LEADER_TEMPORAL_UNATTRIBUTED_KEY
        ]
        self.assertEqual(len(unattributed), 1)
        self.assertEqual(unattributed[0]["lider"], LEADER_TEMPORAL_UNATTRIBUTED_LABEL)
        self.assertEqual(unattributed[0]["auditados"], 1)
        self.assertEqual(
            sum(row["auditados"] for row in hierarchy["results"]),
            3,
        )

        dash = build_dashboard({**self.base_params, "module": "agentes", "by": "agente"})
        leader_attributed = sum(
            row["auditados"]
            for row in dash["leader_hierarchy"]["results"]
            if row.get("key") != LEADER_TEMPORAL_UNATTRIBUTED_KEY
        )
        self.assertEqual(dash["kpis"]["auditados"], leader_attributed)
        self.assertIn("facilitator_hierarchy", dash)
        self.assertIn("operational_detail_scope", dash)
        self.assertEqual(dash["workforce_coverage"]["temporal_unattributed_rows"], 1)

    def test_ranking_by_lider_splits_metrics(self):
        ranking = build_ranking({**self.base_params, "by": "lider"})
        by_label = {row["label"]: row for row in ranking["results"]}
        self.assertEqual(by_label["Líder Antigo"]["auditados"], 1)
        self.assertEqual(by_label["Líder Novo"]["auditados"], 1)

    def test_date_axis_analise_uses_data_analise(self):
        from apps.qualidade_operacional.services.queries import (
            apply_common_filters,
            date_field_auditados,
        )

        QualidadeAuditado.objects.filter(protocolo="ANTES").update(
            data=date(2026, 6, 1), data_analise=date(2026, 7, 10)
        )
        QualidadeAuditado.objects.filter(protocolo="DEPOIS").update(
            data=date(2026, 6, 1), data_analise=date(2026, 7, 20)
        )
        params = {**self.base_params, "date_axis": "analise", "lider": "Líder Antigo"}
        date_field = date_field_auditados(params)
        qs = apply_common_filters(
            QualidadeAuditado.objects.all(),
            params,
            date_field=date_field,
        )
        protocols = sorted(qs.values_list("protocolo", flat=True))
        self.assertEqual(protocols, ["ANTES"])

    def test_imported_lider_does_not_affect_canonical_assignment(self):
        resolved = resolve_leader_for_date("ltemporal01a", date(2026, 7, 10))
        self.assertEqual(resolved["lider"], "Líder Antigo")
        fal = filtered_falhas({**self.base_params, "lider": "Líder Antigo"})
        self.assertEqual(fal.count(), 1)
        self.assertEqual(fal.first().lider, "Líder Errado Importado")

    def test_dashboard_exposes_leader_hierarchy(self):
        dash = build_dashboard({**self.base_params, "module": "agentes", "by": "agente"})
        self.assertIn("leader_hierarchy", dash)
        self.assertEqual(len(dash["leader_hierarchy"]["results"]), 2)
        agent_ranking = {
            (row.get("matricula") or "").strip().lower(): row.get("quartil")
            for row in dash["ranking"]["results"]
        }
        for segment in dash["leader_hierarchy"]["results"]:
            mat = (segment.get("matricula") or "").strip().lower()
            self.assertEqual(segment.get("quartil"), agent_ranking.get(mat))

    def test_api_auditados_respects_leader_filter(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {**self.base_params, "lider": "Líder Antigo"},
        )
        self.assertEqual(res.status_code, 200)
        protocols = sorted(r["protocolo"] for r in res.data["results"])
        self.assertEqual(protocols, ["ANTES"])

    def test_payload_schema_bumped(self):
        self.assertIn("leader-temporal", _PAYLOAD_SCHEMA)
        self.assertIn("unattributed", _PAYLOAD_SCHEMA)

    def test_history_overlap_precedence_by_start_date(self):
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader_old,
            team="Overlap",
            start_date=date(2026, 7, 5),
            final_date=date(2026, 7, 12),
            active=False,
        )
        resolved = resolve_leader_for_date("ltemporal01a", date(2026, 7, 10))
        self.assertEqual(resolved["lider"], "Líder Antigo")
