# -*- coding: utf-8 -*-
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.enrichment import build_responsavel_lookup
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.workforce_scope import clear_responsibility_index_cache
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE
from apps.workforce.models import Agent, AgentHistory
from apps.auditoria.models import AuditoriaAtividade, AuditoriaFalhaCadastro
from apps.qualidade_operacional.models import QualidadeIntranetProjection

User = get_user_model()


def _read_csv(response):
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    lines = [line for line in body.splitlines() if line.strip()]
    headers = lines[0].split(";")
    rows = [line.split(";") for line in lines[1:]]
    return headers, rows


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class QualidadeExportColumnsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="qo_export_user", password="x", email="qo_export@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        DimCliente.objects.create(id_cliente=6, nome="Cliente Seis")
        DimWorkflow.objects.create(id_workflow=144, nome="WF 144")
        agent = Agent.objects.create(
            full_name="Agente Teste", user_lan_id="c93460a", active=True
        )
        AgentHistory.objects.create(
            agent=agent,
            team="Operacao Teste",
            start_date=date(2025, 1, 1),
            active=True,
        )
        bump_quality_cache_version()

        QualidadeAuditado.objects.create(
            data=date(2025, 4, 1),
            data_analise=date(2025, 4, 2),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="G Auditoria",
            matricula="c93460a",
            matricula_auditor="c92898a",
            protocolo="45008984",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2024, 5, 2),
            data_analise=date(2024, 5, 3),
            id_cliente=83,
            id_workflow=450,
            protocolo="178346351",
            matricula="c92629a",
            tipo_falha="Manual",
            modulo="Auditoria",
            source_file="test",
        )

    def test_auditados_export_has_agente_before_matricula(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/auditados.csv",
            {"protocolo": "45008984"},
        )
        self.assertEqual(res.status_code, 200)
        headers, rows = _read_csv(res)
        agente_idx = headers.index("Agente")
        matricula_idx = headers.index("Matrícula")
        self.assertLess(agente_idx, matricula_idx)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][agente_idx], "Agente Teste")
        self.assertEqual(rows[0][matricula_idx], "c93460a")

    def test_auditados_export_shows_cliente_workflow_names(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/auditados.csv",
            {"protocolo": "45008984"},
        )
        self.assertEqual(res.status_code, 200)
        headers, rows = _read_csv(res)
        cliente_idx = headers.index("Cliente")
        workflow_idx = headers.index("Workflow")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][cliente_idx], "Cliente Seis")
        self.assertEqual(rows[0][workflow_idx], "WF 144")
        self.assertNotEqual(rows[0][cliente_idx], "6")
        self.assertNotEqual(rows[0][workflow_idx], "144")

    def test_falhas_export_shows_cliente_workflow_names(self):
        DimCliente.objects.create(id_cliente=83, nome="Cliente Oitenta e Três")
        DimWorkflow.objects.create(id_workflow=450, nome="WF 450")
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/falhas.csv",
            {"protocolo": "178346351"},
        )
        self.assertEqual(res.status_code, 200)
        headers, rows = _read_csv(res)
        cliente_idx = headers.index("Cliente")
        workflow_idx = headers.index("Workflow")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][cliente_idx], "Cliente Oitenta e Três")
        self.assertEqual(rows[0][workflow_idx], "WF 450")

    def test_falhas_export_has_agente_before_matricula(self):
        res = self.client.get("/api/v1/qualidade/operacional/export/falhas.csv")
        self.assertEqual(res.status_code, 200)
        headers, _rows = _read_csv(res)
        self.assertLess(headers.index("Agente"), headers.index("Matrícula"))

    def test_processual_falha_export_blanks_agente_and_matricula(self):
        QualidadeFalha.objects.create(
            data=date(2026, 1, 4),
            protocolo="PROC-EXPORT",
            matricula="c93460a",
            tipo_falha="Processual",
            source_file="test",
        )
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/falhas.csv",
            {"protocolo": "PROC-EXPORT", "metric_mode": "complete"},
        )
        self.assertEqual(res.status_code, 200)
        headers, rows = _read_csv(res)
        agente_idx = headers.index("Agente")
        matricula_idx = headers.index("Matrícula")
        row = next(item for item in rows if "PROC-EXPORT" in item)
        self.assertEqual(row[agente_idx], "")
        self.assertEqual(row[matricula_idx], "")

    def test_falhas_export_includes_observacao_auditor_column(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Aud export",
            observacao="Obs export protocolo",
        )
        source = AuditoriaFalhaCadastro.objects.create(
            atividade=atividade,
            protocolo="OBS-EXPORT-1",
            tipo_falha="Colaborador",
            usuario="c92629a",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        )
        falha = QualidadeFalha.objects.create(
            data=date(2024, 6, 3),
            protocolo="OBS-EXPORT-1",
            matricula="c92629a",
            tipo_falha="Manual",
            source_file=INTRANET_SOURCE_FILE,
        )
        QualidadeIntranetProjection.objects.create(
            source=source,
            falha=falha,
            sync_status=QualidadeIntranetProjection.STATUS_OK,
        )

        with override_settings(
            QUALIDADE_INTRANET_SOURCE_ENABLED=True,
            QUALIDADE_SOURCE_MODE="intranet",
        ):
            res = self.client.get(
                "/api/v1/qualidade/operacional/export/falhas.csv",
                {"protocolo": "OBS-EXPORT-1"},
            )
        self.assertEqual(res.status_code, 200)
        headers, rows = _read_csv(res)
        self.assertIn("Observação do auditor", headers)
        obs_idx = headers.index("Observação do auditor")
        self.assertEqual(rows[0][obs_idx], "Obs export protocolo")


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class QualidadeWorkforceExportTests(TestCase):
    def setUp(self):
        cache.clear()
        clear_responsibility_index_cache()
        self.user = User.objects.create_user(
            username="qo_workforce_export", password="x", email="qo_wf@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.leader = Agent.objects.create(full_name="Lider Export", user_lan_id="lider_exp")
        self.facilitator = Agent.objects.create(
            full_name="Facilitador Export", user_lan_id="fac_exp"
        )
        self.agent = Agent.objects.create(full_name="Operador Export", user_lan_id="op_exp")
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="BRFLOW",
            job_activity="Integração",
            start_date=date(2026, 8, 1),
            active=True,
        )
        bump_quality_cache_version()

        QualidadeFalha.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_exp",
            protocolo="FAC-EXPORT",
            tipo_falha="Manual",
            lider="Lider TSV Legado",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_exp",
            protocolo="FAC-AUD",
            tipo_conclusao="Manual",
            source_file="test",
        )

    def test_responsavel_lookup_marks_facilitator_in_window(self):
        falha = QualidadeFalha.objects.get(protocolo="FAC-EXPORT")
        lookup = build_responsavel_lookup([falha], date_field="data")
        meta = lookup[falha.pk]
        self.assertEqual(meta["responsabilidade"], "facilitador")
        self.assertEqual(meta["responsavel_nome"], "Facilitador Export")
        self.assertEqual(meta["responsavel_matricula"], "fac_exp")
        self.assertEqual(meta["regra_responsabilidade"], "onboarding")

    def test_falhas_export_facilitator_scope_includes_responsavel_columns(self):
        from apps.qualidade_operacional.services.analytics import (
            filtered_falhas,
            operational_facilitator_scope_params,
        )

        params = {
            **operational_facilitator_scope_params(
                {"start_date": "2026-08-01", "end_date": "2026-08-31"}
            ),
            "protocolo": "FAC-EXPORT",
        }
        self.assertEqual(filtered_falhas(params).count(), 1)
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/falhas.csv",
            params,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("facilitador", res["Content-Disposition"])
        headers, rows = _read_csv(res)
        self.assertIn("Responsável", headers)
        self.assertIn("Tipo responsável", headers)
        self.assertIn("Matrícula responsável", headers)
        self.assertIn("Jornada", headers)
        self.assertIn("Líder", headers)
        row = rows[0]
        self.assertEqual(row[headers.index("Tipo responsável")], "Facilitador")
        self.assertEqual(row[headers.index("Responsável")], "Facilitador Export")
        self.assertEqual(row[headers.index("Matrícula responsável")], "fac_exp")
        self.assertEqual(row[headers.index("Jornada")], "onboarding")
        self.assertEqual(row[headers.index("Líder")], "Lider TSV Legado")

    def test_responsavel_nome_column_filter_facilitator_scope(self):
        from apps.qualidade_operacional.services.analytics import (
            filtered_falhas,
            operational_facilitator_scope_params,
        )
        from apps.qualidade_operacional.services.detail_column_filters import (
            apply_detail_column_filters,
            build_column_values,
        )

        params = operational_facilitator_scope_params(
            {"start_date": "2026-08-01", "end_date": "2026-08-31"}
        )
        base = filtered_falhas(params)
        values = build_column_values(
            base, "responsavel_nome", kind="falhas", params=params
        )
        self.assertTrue(values["ok"])
        labels = {item["label"] for item in values["values"]}
        self.assertIn("Facilitador Export", labels)

        filtered = apply_detail_column_filters(
            base,
            {**params, "column_responsavel_nome": "Facilitador Export"},
            kind="falhas",
        )
        self.assertEqual(filtered.count(), 1)
        self.assertEqual(filtered.get().protocolo, "FAC-EXPORT")

    def test_auditados_export_leader_scope_responsavel_columns(self):
        from apps.qualidade_operacional.services.analytics import (
            filtered_auditados,
            operational_agentes_scope_params,
        )

        QualidadeAuditado.objects.create(
            data=date(2026, 10, 1),
            data_analise=date(2026, 10, 1),
            id_cliente=6,
            id_workflow=144,
            matricula="op_exp",
            protocolo="LIDER-AUD",
            tipo_conclusao="Manual",
            source_file="test",
        )
        params = {
            **operational_agentes_scope_params(
                {"start_date": "2026-10-01", "end_date": "2026-10-31"}
            ),
            "protocolo": "LIDER-AUD",
        }
        self.assertEqual(filtered_auditados(params).count(), 1)
        res = self.client.get(
            "/api/v1/qualidade/operacional/export/auditados.csv",
            params,
        )
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("facilitador", res["Content-Disposition"])
        headers, rows = _read_csv(res)
        self.assertIn("Responsável", headers)
        row = rows[0]
        self.assertEqual(row[headers.index("Tipo responsável")], "Líder")
        self.assertEqual(row[headers.index("Responsável")], "Lider Export")
