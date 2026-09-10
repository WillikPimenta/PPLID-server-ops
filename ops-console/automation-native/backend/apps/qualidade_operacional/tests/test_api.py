# -*- coding: utf-8 -*-
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_ADM_PORTAL,
    ROLE_PLAN_ANALISTA,
    ROLE_QUAL_GERENCIA,
    ROLE_QUAL_USUARIO,
    role_group_name,
)
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.filter_catalog import _build_filter_payload
from apps.workforce.models import Agent, AgentHistory
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.auditoria.models import AuditoriaAtividade, AuditoriaFalhaCadastro
from apps.qualidade_operacional.models import QualidadeIntranetProjection
from apps.qualidade_operacional.services.enrichment import merge_observacao_auditor
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class QualidadeOperacionalApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="qo_user", password="x", email="qo_user@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        DimCliente.objects.create(id_cliente=6, nome="Cliente Seis")
        DimWorkflow.objects.create(id_workflow=144, nome="WF 144")
        Agent.objects.create(full_name="Agente Teste", user_lan_id="c93460a", active=True)
        Agent.objects.create(full_name="Agente Falha", user_lan_id="c92629a", active=True)
        AgentHistory.objects.create(
            agent=Agent.objects.get(user_lan_id="c93460a"),
            team="Operacao Teste",
            location="Brasília",
            journey_shift="Matutino",
            start_date=date(2025, 1, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=Agent.objects.get(user_lan_id="c92629a"),
            team="Operacao Teste",
            location="Brasília",
            start_date=date(2024, 1, 1),
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
            localidade_documento="SP",
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
            nivel_dificuldade="Fácil Original",
            nivel_dificuldade_confer="Difícil Confer",
            localidade="Brasília",
            localidade_documento="SP",
            uf="SP",
            source_file="test",
        )

    def test_meta_ok(self):
        res = self.client.get("/api/v1/qualidade/operacional/meta/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["auditados_count"], 1)
        self.assertEqual(res.data["falhas_count"], 1)

    def test_auditados_enrich(self):
        res = self.client.get("/api/v1/qualidade/operacional/auditados/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        row = res.data["results"][0]
        self.assertEqual(row["cliente_nome"], "Cliente Seis")
        self.assertEqual(row["workflow_nome"], "WF 144")
        self.assertEqual(row["agente_nome"], "Agente Teste")

    def test_auditados_expose_audit_and_analysis_dates(self):
        res = self.client.get("/api/v1/qualidade/operacional/auditados/")

        self.assertEqual(res.status_code, 200)
        row = res.data["results"][0]
        self.assertEqual(row["data"], "2025-04-01")
        self.assertEqual(row["data_analise"], "2025-04-02")
        self.assertEqual(row["dias_prazo"], -1)

    def test_falhas_expose_audit_and_analysis_dates(self):
        res = self.client.get("/api/v1/qualidade/operacional/falhas/")

        self.assertEqual(res.status_code, 200)
        row = res.data["results"][0]
        self.assertEqual(row["data"], "2024-05-02")
        self.assertEqual(row["data_analise"], "2024-05-03")
        self.assertEqual(row["dias_prazo"], -1)

    def test_falhas_expose_effective_difficulty_and_audit_module(self):
        res = self.client.get("/api/v1/qualidade/operacional/falhas/")

        self.assertEqual(res.status_code, 200)
        row = res.data["results"][0]
        self.assertEqual(row["nivel_dificuldade_efetiva"], "Difícil Confer")
        self.assertEqual(row["modulo"], "Auditoria")

    def test_reconciliation_fields_are_admin_only(self):
        regular = self.client.get("/api/v1/qualidade/operacional/falhas/")

        self.assertEqual(regular.status_code, 200)
        regular_row = regular.data["results"][0]
        self.assertNotIn("reconciliation_status", regular_row)
        self.assertNotIn("reconciliation_observation", regular_row)
        self.assertNotIn("reconciliation_differences", regular_row)

        Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_ADM_PORTAL)))
        admin = self.client.get("/api/v1/qualidade/operacional/falhas/")

        self.assertEqual(admin.status_code, 200)
        admin_row = admin.data["results"][0]
        self.assertIn("reconciliation_status", admin_row)
        self.assertIn("reconciliation_observation", admin_row)
        self.assertIn("reconciliation_differences", admin_row)

    def test_falhas_effective_difficulty_fallback_and_missing_value(self):
        QualidadeFalha.objects.create(
            data=date(2024, 5, 4),
            protocolo="FALHA-DIFICULDADE-ORIGINAL",
            tipo_falha="Manual",
            nivel_dificuldade="Média Original",
            nivel_dificuldade_confer="",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2024, 5, 5),
            protocolo="FALHA-DIFICULDADE-AUSENTE",
            tipo_falha="Manual",
            tipo_analise="G Auditoria",
            nivel_dificuldade="",
            nivel_dificuldade_confer="",
            source_file="test",
        )

        original = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"protocolo": "FALHA-DIFICULDADE-ORIGINAL"},
        )
        missing = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"protocolo": "FALHA-DIFICULDADE-AUSENTE"},
        )

        self.assertEqual(original.status_code, 200)
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(
            original.data["results"][0]["nivel_dificuldade_efetiva"],
            "Média Original",
        )
        self.assertEqual(
            missing.data["results"][0]["nivel_dificuldade_efetiva"],
            "Não informado",
        )
        self.assertEqual(missing.data["results"][0]["modulo"], "")

    def test_date_fields_keep_nulls_instead_of_copying_the_other_date(self):
        QualidadeAuditado.objects.create(
            data=date(2025, 4, 3),
            data_analise=None,
            protocolo="AUD-SEM-ANALISE",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2024, 5, 4),
            data_analise=None,
            protocolo="FALHA-SEM-ANALISE",
            tipo_falha="Manual",
            source_file="test",
        )

        auditados = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"protocolo": "AUD-SEM-ANALISE"},
        )
        falhas = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"protocolo": "FALHA-SEM-ANALISE"},
        )

        self.assertEqual(auditados.status_code, 200)
        self.assertEqual(falhas.status_code, 200)
        self.assertEqual(auditados.data["results"][0]["data"], "2025-04-03")
        self.assertIsNone(auditados.data["results"][0]["data_analise"])
        self.assertEqual(falhas.data["results"][0]["data"], "2024-05-04")
        self.assertIsNone(falhas.data["results"][0]["data_analise"])

    def test_cliente_e_workflow_9999_use_aliases_and_procedimento(self):
        QualidadeFalha.objects.create(
            data=date(2026, 7, 12),
            data_analise=date(2026, 7, 12),
            id_cliente=9999,
            id_workflow=9999,
            protocolo="P9999",
            categoria_falha="",
            tipo_falha="Manual",
            source_file="test",
        )
        res = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"id_cliente": "9999", "categoria_falha": "Procedimento"},
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        row = res.data["results"][0]
        self.assertEqual(row["cliente_nome"], "CLARO - FORMALIZAÇÃO")
        self.assertEqual(row["workflow_nome"], "CLARO - CONFER")
        self.assertEqual(row["categoria_falha"], "Procedimento")

        catalog = _build_filter_payload()
        clientes = {row["id_cliente"]: row["nome"] for row in catalog["clientes"]}
        workflows = {row["id_workflow"]: row["nome"] for row in catalog["workflows"]}
        self.assertEqual(clientes[9999], "CLARO - FORMALIZAÇÃO")
        self.assertEqual(workflows[9999], "CLARO - CONFER")

    def test_falhas_filter_localidade(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/falhas/", {"localidade": "SP"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)

    def test_falhas_filter_localidade_hc(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/falhas/", {"localidade_hc": "Brasília"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)

        empty = self.client.get(
            "/api/v1/qualidade/operacional/falhas/", {"localidade_hc": "Fortaleza"}
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data["count"], 0)

    def test_auditados_filter_localidade_hc(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/auditados/", {"localidade_hc": "Brasília"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)

        empty = self.client.get(
            "/api/v1/qualidade/operacional/auditados/", {"localidade_hc": "Fortaleza"}
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data["count"], 0)

    def test_filter_turno(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/auditados/", {"turno": "Matutino"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)

        empty = self.client.get(
            "/api/v1/qualidade/operacional/auditados/", {"turno": "Noturno"}
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data["count"], 0)

        catalog = _build_filter_payload()
        self.assertIn("Matutino", catalog["turnos"])

    def test_dashboard_agentes_workforce_dimensions(self):
        bump_quality_cache_version()
        params = {
            "module": "agentes",
            "start_date": "2025-04-01",
            "end_date": "2025-04-30",
            "by": "agente",
            "workforce_only": "1",
        }
        res = self.client.get("/api/v1/qualidade/operacional/dashboard/", params)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["module"], "agentes")
        dims = res.data.get("workforce_dimensions") or {}
        self.assertIn("localidade_hc", dims)
        self.assertIn("turno", dims)

        hc = {row["label"]: row for row in dims["localidade_hc"]}
        self.assertIn("Brasília", hc)
        self.assertEqual(hc["Brasília"]["auditados"], 1)

        turno = {row["label"]: row for row in dims["turno"]}
        self.assertIn("Matutino", turno)
        self.assertEqual(turno["Matutino"]["auditados"], 1)

        filtered = self.client.get(
            "/api/v1/qualidade/operacional/dashboard/",
            {**params, "localidade_hc": "Fortaleza"},
        )
        self.assertEqual(filtered.status_code, 200)
        hc_filtered = filtered.data["workforce_dimensions"]["localidade_hc"]
        self.assertEqual(hc_filtered, [])

    def test_falhas_expose_localidade_documento(self):
        res = self.client.get("/api/v1/qualidade/operacional/falhas/")
        self.assertEqual(res.status_code, 200)
        row = res.data["results"][0]
        self.assertEqual(row["localidade_documento"], "SP")

    def test_falhas_filter_localidade_documento_column(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"column_localidade_documento": "SP"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)

        empty = self.client.get(
            "/api/v1/qualidade/operacional/detalhe/colunas/",
            {"lista": "falhas", "column": "localidade_documento"},
        )
        self.assertEqual(empty.status_code, 200)
        labels = {row["label"] for row in empty.data["values"]}
        self.assertIn("SP", labels)

    def test_dashboard_agentes_includes_kpis_m1_month(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/dashboard/",
            {
                "module": "agentes",
                "start_date": "2025-04-01",
                "end_date": "2025-04-30",
                "workforce_only": "1",
                "by": "agente",
            },
        )
        self.assertEqual(res.status_code, 200)
        block = res.data.get("kpis_m1_month") or {}
        self.assertTrue(block.get("ok"))
        self.assertIn("anchor_label", block)
        self.assertIn("previous_label", block)

    def test_auditados_filter_localidade_documento(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/auditados/", {"localidade": "SP"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)

    def test_detail_column_filters_accept_brazilian_date_and_labels(self):
        by_date = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"column_data": "01-04-2025"},
        )
        by_cliente = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"column_id_cliente": "Cliente Seis"},
        )
        by_agent = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"column_matricula": "Agente Teste"},
        )

        self.assertEqual(by_date.data["count"], 1)
        self.assertEqual(by_cliente.data["count"], 1)
        self.assertEqual(by_agent.data["count"], 1)

    def test_existing_processual_is_not_exposed_or_filtered_as_agent_link(self):
        QualidadeFalha.objects.create(
            data=date(2026, 1, 4),
            protocolo="PROC-LEGADO",
            matricula="c93460a",
            tipo_falha="Processual",
            source_file="test",
        )

        detail = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"protocolo": "PROC-LEGADO", "metric_mode": "complete"},
        )
        linked = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {
                "protocolo": "PROC-LEGADO",
                "matricula": "c93460a",
                "metric_mode": "complete",
            },
        )
        column_linked = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {
                "protocolo": "PROC-LEGADO",
                "column_matricula": "Agente Teste",
                "metric_mode": "complete",
            },
        )

        self.assertEqual(detail.data["count"], 1)
        self.assertEqual(detail.data["results"][0]["matricula"], "")
        self.assertIsNone(detail.data["results"][0]["agente_nome"])
        self.assertEqual(linked.data["count"], 0)
        self.assertEqual(column_linked.data["count"], 0)

    def test_filters_uses_consolidated_catalog(self):
        res = self.client.get("/api/v1/qualidade/operacional/filtros/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("G Auditoria", res.data["tipos_analise"])
        self.assertIn("Manual", res.data["tipos_falha"])
        self.assertEqual(res.data["clientes"][0]["nome"], "Cliente Seis")
        self.assertEqual(res.data["workflows"][0]["nome"], "WF 144")
        self.assertIn("qualidade;dur=", res.headers["Server-Timing"])
        self.assertIn("equipes", res.data)
        agentes = res.data["agentes"]
        self.assertTrue(agentes)
        sample = agentes[0]
        if isinstance(sample, dict):
            self.assertIn("value", sample)
            self.assertIn("matricula", sample)
            self.assertIn("label", sample)
            self.assertEqual(sample["value"], sample["matricula"])
        else:
            self.fail("agentes deve ser lista estruturada {value,matricula,label}")

    def test_detail_column_values_endpoint_returns_friendly_labels(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/detalhe/colunas/",
            {"lista": "auditados", "column": "matricula"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])
        labels = {row["label"] for row in res.data["values"]}
        self.assertIn("Agente Teste", labels)

    def test_detail_column_multi_filter_and_sort(self):
        multi = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"column_matricula": "Agente Teste,Inexistente"},
        )
        self.assertEqual(multi.data["count"], 1)

        sorted_res = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"detail_sort": "protocolo", "detail_dir": "asc"},
        )
        self.assertEqual(sorted_res.status_code, 200)
        self.assertEqual(sorted_res.data["count"], 1)
        self.assertEqual(sorted_res.data["results"][0]["protocolo"], "45008984")

    def test_detail_column_values_empty_token(self):
        QualidadeAuditado.objects.create(
            data=date(2025, 4, 3),
            protocolo="SEM-AGENTE",
            matricula="",
            source_file="test",
        )
        bump_quality_cache_version()
        res = self.client.get(
            "/api/v1/qualidade/operacional/detalhe/colunas/",
            {"lista": "auditados", "column": "matricula", "q": "—"},
        )
        self.assertEqual(res.status_code, 200)
        labels = {row["label"] for row in res.data["values"]}
        self.assertIn("—", labels)

    def test_detail_column_cenario_prefers_des_problemas(self):
        QualidadeFalha.objects.create(
            data=date(2026, 2, 1),
            protocolo="38269484",
            matricula="c92629a",
            tipo_falha="reinspecao",
            cenario="Não sinalizada - Irregularidade / Sinalização incorreta - Irregularidade",
            des_problemas=(
                "IC - 650 - IMEI no Contrato de Habilitação em inconformidade com o IMEI do Sistema"
            ),
            source_file="test",
        )
        bump_quality_cache_version()

        values = self.client.get(
            "/api/v1/qualidade/operacional/detalhe/colunas/",
            {"lista": "falhas", "column": "cenario", "protocolo": "38269484"},
        )
        self.assertEqual(values.status_code, 200)
        labels = {row["label"] for row in values.data["values"]}
        self.assertIn(
            "IC - 650 - IMEI no Contrato de Habilitação em inconformidade com o IMEI do Sistema",
            labels,
        )
        self.assertNotIn(
            "Não sinalizada - Irregularidade / Sinalização incorreta - Irregularidade",
            labels,
        )

        filtered = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {
                "protocolo": "38269484",
                "column_cenario": (
                    "IC - 650 - IMEI no Contrato de Habilitação em inconformidade com o IMEI do Sistema"
                ),
                "metric_mode": "complete",
            },
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.data["count"], 1)
        self.assertEqual(
            filtered.data["results"][0]["des_problemas"],
            "IC - 650 - IMEI no Contrato de Habilitação em inconformidade com o IMEI do Sistema",
        )


    def test_deny_without_perm(self):
        other = User.objects.create_user(
            username="no_qo", password="x", email="no_qo@example.com"
        )
        c = APIClient()
        c.force_authenticate(user=other)
        res = c.get("/api/v1/qualidade/operacional/meta/")
        self.assertEqual(res.status_code, 403)

    def test_qual_usuario_can_view(self):
        user = User.objects.create_user(
            username="qual_uo", password="x", email="qual_uo@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_USUARIO))
        user.groups.add(Group.objects.get(name=role_group_name(ROLE_QUAL_USUARIO)))
        c = APIClient()
        c.force_authenticate(user=user)
        res = c.get("/api/v1/qualidade/operacional/meta/")
        self.assertEqual(res.status_code, 200)

    def test_sync_denied_for_view_only(self):
        res = self.client.post("/api/v1/qualidade/operacional/sync/", {"mode": "upsert"})
        self.assertEqual(res.status_code, 403)

    def test_auditores_dashboard_requires_gerencia(self):
        params = {
            "module": "auditores",
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
        }
        res = self.client.get("/api/v1/qualidade/operacional/dashboard/", params)
        self.assertEqual(res.status_code, 403)

        qual_user = User.objects.create_user(
            username="qual_uo_auditores",
            password="x",
            email="qual_uo_auditores@example.com",
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_USUARIO))
        qual_user.groups.add(Group.objects.get(name=role_group_name(ROLE_QUAL_USUARIO)))
        qual_client = APIClient()
        qual_client.force_authenticate(user=qual_user)
        self.assertEqual(
            qual_client.get("/api/v1/qualidade/operacional/dashboard/", params).status_code,
            403,
        )

        gerencia = User.objects.create_user(
            username="qual_ger_auditores",
            password="x",
            email="qual_ger_auditores@example.com",
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_GERENCIA))
        gerencia.groups.add(Group.objects.get(name=role_group_name(ROLE_QUAL_GERENCIA)))
        gerencia_client = APIClient()
        gerencia_client.force_authenticate(user=gerencia)
        self.assertEqual(
            gerencia_client.get("/api/v1/qualidade/operacional/dashboard/", params).status_code,
            200,
        )


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="intranet",
)
class QualidadeFalhaObservacaoAuditorApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="qo_obs_user", password="x", email="qo_obs@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        bump_quality_cache_version()

        QualidadeFalha.objects.create(
            data=date(2024, 5, 2),
            data_analise=date(2024, 5, 3),
            protocolo="TSV-OBS",
            matricula="c92629a",
            tipo_falha="Manual",
            source_file="test",
        )

    def test_merge_observacao_auditor_deduplicates(self):
        merged = merge_observacao_auditor("Mesma obs", "Mesma obs")
        self.assertEqual(merged, "Mesma obs")

    def test_falhas_tsv_observacao_auditor_empty(self):
        with override_settings(QUALIDADE_SOURCE_MODE="legacy", QUALIDADE_INTRANET_SOURCE_ENABLED=False):
            res = self.client.get(
                "/api/v1/qualidade/operacional/falhas/",
                {"protocolo": "TSV-OBS"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"][0]["observacao_auditor"], "")

    def test_falhas_intranet_observacao_auditor_merges_falha_and_atividade(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Aud obs",
            observacao="Obs do protocolo",
        )
        source = AuditoriaFalhaCadastro.objects.create(
            atividade=atividade,
            protocolo="OBS-INTRA-1",
            tipo_falha="Colaborador",
            usuario="c92629a",
            observacao="Obs da falha",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        )
        falha = QualidadeFalha.objects.create(
            data=date(2024, 6, 1),
            data_analise=date(2024, 6, 2),
            protocolo="OBS-INTRA-1",
            matricula="c92629a",
            tipo_falha="Manual",
            source_file=INTRANET_SOURCE_FILE,
        )
        QualidadeIntranetProjection.objects.create(
            source=source,
            falha=falha,
            sync_status=QualidadeIntranetProjection.STATUS_OK,
        )

        res = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"protocolo": "OBS-INTRA-1"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.data["results"][0]["observacao_auditor"],
            "Obs da falha\nObs do protocolo",
        )
