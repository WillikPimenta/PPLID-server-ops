# -*- coding: utf-8 -*-
"""Testes das extensões do Resumo: melhorias, prioridades por etapa, contestação."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.resumo_extensions import (
    build_clientes_melhorias_all,
    build_prioridades_etapa,
    paginate_clientes_melhorias,
)

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class ResumoExtensionsTests(TestCase):
    def setUp(self):
        bump_quality_cache_version()
        self.user = User.objects.create_user(
            username="eo_melhorias", password="x", email="eo_m@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

        # 5 clientes no período atual (acima do "3 do SVG")
        self.client_ids = [101, 102, 103, 104, 105]
        for cid in self.client_ids:
            for i in range(20):
                QualidadeAuditado.objects.create(
                    data=date(2026, 7, 10),
                    data_analise=date(2026, 7, 10),
                    id_cliente=cid,
                    id_workflow=10,
                    tipo_analise="Auditoria Compliance",
                    matricula=f"m{cid}{i}",
                    protocolo=f"P{cid}-{i}",
                    etapa="Validação",
                    tipo_conclusao="Manual",
                    source_file="test",
                )
            # Falhas com pesos distintos
            n_falhas = 5 if cid == 101 else 2
            for i in range(n_falhas):
                QualidadeFalha.objects.create(
                    data=date(2026, 7, 10),
                    data_analise=date(2026, 7, 10),
                    id_cliente=cid,
                    id_workflow=10,
                    tipo_analise="Auditoria Compliance",
                    matricula=f"m{cid}{i}",
                    protocolo=f"P{cid}-{i}",
                    etapa="Validação" if cid != 102 else "Análise Visual",
                    cenario="Risk Manager" if cid == 101 else "Formalização",
                    des_problemas="Causa A" if cid == 101 else "Causa B",
                    categoria_falha="Crítica" if cid == 101 else "Procedimento",
                    nivel_dificuldade="Difícil" if cid == 101 else "Média",
                    tipo_falha="Manual",
                    source_file="test",
                )

        # Cliente 101 também no mês anterior (histórico próprio) com EO melhor
        for i in range(20):
            QualidadeAuditado.objects.create(
                data=date(2026, 6, 10),
                data_analise=date(2026, 6, 10),
                id_cliente=101,
                id_workflow=10,
                tipo_analise="Auditoria Compliance",
                matricula=f"prev{i}",
                protocolo=f"PREV101-{i}",
                etapa="Validação",
                tipo_conclusao="Manual",
                source_file="test",
            )
        for i in range(1):
            QualidadeFalha.objects.create(
                data=date(2026, 6, 10),
                data_analise=date(2026, 6, 10),
                id_cliente=101,
                id_workflow=10,
                tipo_analise="Auditoria Compliance",
                matricula=f"prev{i}",
                protocolo=f"PREV101-{i}",
                etapa="Validação",
                cenario="Risk Manager",
                des_problemas="Causa A",
                categoria_falha="Procedimento",
                nivel_dificuldade="Fácil",
                tipo_falha="Manual",
                source_file="test",
            )

        # Cliente só com falha (sem auditados) no período
        QualidadeFalha.objects.create(
            data=date(2026, 7, 12),
            data_analise=date(2026, 7, 12),
            id_cliente=999,
            id_workflow=10,
            tipo_analise="Auditoria Compliance",
            matricula="orphan",
            protocolo="ORPHAN-1",
            etapa="Sobreposição",
            cenario="Confer",
            des_problemas="Órfã",
            categoria_falha="Procedimento",
            source_file="test",
        )

        # Contestações operacionais (Contest* exceto Compliance / Formalização)
        for i in range(3):
            QualidadeAuditado.objects.create(
                data=date(2026, 7, 15),
                data_analise=date(2026, 7, 15),
                id_cliente=101,
                id_workflow=10,
                tipo_analise="Contestação Externa",
                matricula=f"e{i}",
                protocolo=f"E101-{i}",
                etapa="Validação",
                tipo_conclusao="Manual",
                source_file="test",
            )
        for i in range(2):
            QualidadeFalha.objects.create(
                data=date(2026, 7, 15),
                data_analise=date(2026, 7, 15),
                id_cliente=101,
                id_workflow=10,
                tipo_analise="Contestação Externa",
                matricula=f"e{i}",
                protocolo=f"E101-{i}",
                etapa="Validação",
                cenario="Risk Manager",
                des_problemas="Contest",
                categoria_falha="Crítica",
                nivel_dificuldade="Média",
                tipo_falha="Manual",
                modulo="Contestação",
                source_file="test",
            )

        # Contestação Compliance — fluxo Formalização; fora do escopo operacional EO/QO
        for i in range(10):
            QualidadeAuditado.objects.create(
                data=date(2026, 7, 15),
                data_analise=date(2026, 7, 15),
                id_cliente=101,
                id_workflow=10,
                tipo_analise="Contestação Compliance",
                matricula=f"c{i}",
                protocolo=f"C101-{i}",
                etapa="Validação",
                tipo_conclusao="Manual",
                source_file="test",
            )
        for i in range(2):
            QualidadeFalha.objects.create(
                data=date(2026, 7, 15),
                data_analise=date(2026, 7, 15),
                id_cliente=101,
                id_workflow=10,
                tipo_analise="Contestação Compliance",
                matricula=f"c{i}",
                protocolo=f"C101-{i}",
                etapa="Validação",
                cenario="Risk Manager",
                des_problemas="Contest",
                categoria_falha="Crítica",
                nivel_dificuldade="Média",
                tipo_falha="Manual",
                modulo="Contestação",
                source_file="test",
            )

        self.params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
            "date_axis": "auditoria",
            "module": "resumo",
        }

    def test_universe_includes_all_clients_not_limited_to_three(self):
        rows = build_clientes_melhorias_all(self.params)
        ids = {r["id_cliente"] for r in rows}
        for cid in self.client_ids:
            self.assertIn(cid, ids)
        self.assertIn(999, ids)  # só falha
        self.assertGreaterEqual(len(rows), 6)
        self.assertNotEqual(len(rows), 3)

    def test_orphan_failure_client_has_null_eo_and_sem_historico(self):
        rows = {r["id_cliente"]: r for r in build_clientes_melhorias_all(self.params)}
        orphan = rows[999]
        self.assertIsNone(orphan["eo_atual_pct"])
        self.assertEqual(orphan["situacao"], "sem_historico")
        self.assertEqual(orphan["auditados"], 0)
        self.assertGreater(orphan["falhas"], 0)

    def test_comparison_uses_own_history_only(self):
        rows = {r["id_cliente"]: r for r in build_clientes_melhorias_all(self.params)}
        c101 = rows[101]
        self.assertIsNotNone(c101["eo_anterior_pct"])
        self.assertEqual(c101["previous"]["auditados"], 20)
        self.assertIsNotNone(c101["participacao_falhas_pct"])
        self.assertIsNotNone(c101["participacao_anterior_pct"])
        self.assertEqual(
            c101["delta_participacao_pp"],
            round(
                c101["participacao_falhas_pct"] - c101["participacao_anterior_pct"],
                1,
            ),
        )
        # 102 sem histórico anterior
        c102 = rows[102]
        self.assertEqual(c102["situacao"], "sem_historico")
        self.assertIsNone(c102["eo_anterior_pct"])
        self.assertIsNone(c102["delta_eo_pp"])
        self.assertIsNotNone(c102["participacao_falhas_pct"])
        self.assertIsNone(c102["delta_participacao_pp"])

    def test_pagination_search_do_not_change_universe(self):
        all_rows = build_clientes_melhorias_all(self.params)
        universe = len(all_rows)
        page1 = paginate_clientes_melhorias(
            all_rows, {**self.params, "melhorias_page": 1, "melhorias_page_size": 2}
        )
        page2 = paginate_clientes_melhorias(
            all_rows, {**self.params, "melhorias_page": 2, "melhorias_page_size": 2}
        )
        self.assertEqual(page1["universe_count"], universe)
        self.assertEqual(page2["universe_count"], universe)
        self.assertEqual(page1["count"], universe)
        self.assertEqual(len(page1["results"]), 2)
        self.assertEqual(len(page2["results"]), 2)
        keys1 = {r["key"] for r in page1["results"]}
        keys2 = {r["key"] for r in page2["results"]}
        self.assertTrue(keys1.isdisjoint(keys2))

        searched = paginate_clientes_melhorias(
            all_rows, {**self.params, "melhorias_q": "Cliente 101"}
        )
        # Sem nome no dim lookup → "Cliente 101"
        self.assertEqual(searched["universe_count"], universe)
        self.assertLessEqual(searched["count"], universe)
        self.assertTrue(all("101" in r["label"] or r["id_cliente"] == 101 for r in searched["results"]))

    def test_prioridades_ordered_by_impacto_not_only_count(self):
        # Cliente 101 tem falhas críticas (peso alto) em Validação
        # Cliente 102 tem Análise Visual com procedimento
        data = build_prioridades_etapa(self.params)
        self.assertGreaterEqual(data["total"], 2)
        ranks = [r["etapa"] for r in data["rows"]]
        self.assertIn("Validação", ranks)
        # Ranking completo disponível (não só 3)
        self.assertEqual(len(data["rows"]), data["total"])
        self.assertEqual(len(data["preview"]), min(3, data["total"]))
        # Impacto da 1ª >= 2ª
        if len(data["rows"]) >= 2:
            self.assertGreaterEqual(
                data["rows"][0]["impacto_ponderado"],
                data["rows"][1]["impacto_ponderado"],
            )
        self.assertIn(data["rows"][0]["prioridade"], {"Muito alto", "Alto", "Médio", "Baixo", "Indefinido"})

    def test_dashboard_includes_extensions_and_filters(self):
        payload = build_dashboard(self.params)
        self.assertEqual(payload["module"], "resumo")
        self.assertIn("clientes_melhorias", payload)
        self.assertNotIn("_clientes_melhorias_all", payload)
        self.assertGreaterEqual(payload["clientes_melhorias"]["universe_count"], 6)
        self.assertLessEqual(len(payload["clientes_melhorias"]["results"]), 25)
        self.assertIn("prioridades_etapa", payload)
        self.assertIn("contestacao_leitura", payload)
        contest = payload["contestacao_leitura"]
        self.assertGreater(contest["protocolos_contestados"], 0)

        contest_dash = build_dashboard({**self.params, "module": "contestacao"})
        self.assertEqual(contest_dash["module"], "contestacao")
        self.assertEqual(contest_dash.get("scope"), "contestacao")
        self.assertIn("falhas_por_etapa", contest_dash)
        self.assertIn("kpis", contest_dash)
        self.assertIn("responsaveis", contest_dash)
        self.assertIn("clientes_melhorias", contest_dash)
        self.assertNotIn("_clientes_melhorias_all", contest_dash)

        # KPIs da aba = universo Contest* (não o EO global)
        resumo_kpis = payload["kpis"]
        contest_kpis = contest_dash["kpis"]
        self.assertLess(contest_kpis["auditados"], resumo_kpis["auditados"])
        self.assertLess(contest_kpis["falhas"], resumo_kpis["falhas"])
        # grain=etapa: auditados/falhas batem com métricas Contest*
        self.assertEqual(
            contest_kpis["auditados"],
            contest_kpis["contestacao"]["auditados_contestacao"],
        )
        self.assertEqual(
            contest_kpis["falhas"],
            contest_kpis["contestacao"]["eventos_contestacao"],
        )

        etapas = contest_dash["falhas_por_etapa"]
        self.assertEqual(etapas.get("scope"), "contestacao")
        self.assertEqual(etapas["total_falhas"], contest_kpis["falhas"])
        self.assertGreaterEqual(etapas["total"], 1)
        first = etapas["rows"][0]
        self.assertIn("impacto_ponderado", first)
        self.assertIn("cenarios", first)
        self.assertIn("criticidades", first)
        self.assertIn("origens", first)
        self.assertEqual(first.get("drill", {}).get("scope"), "contestacao")
        # Ordenação: maior impacto primeiro
        if len(etapas["rows"]) >= 2:
            self.assertGreaterEqual(
                etapas["rows"][0]["impacto_ponderado"],
                etapas["rows"][1]["impacto_ponderado"],
            )

        # Falhas só "Auditoria Compliance" (não Contest*) fora da aba
        contest_melhorias_ids = {
            r["id_cliente"] for r in contest_dash["clientes_melhorias"]["results"]
        }
        self.assertNotIn(999, contest_melhorias_ids)
        self.assertIn(101, contest_melhorias_ids)
        self.assertIn(
            "contestação",
            (contest_dash["clientes_melhorias"].get("title") or "").casefold(),
        )

        prio = contest_dash["prioridades_etapa"]
        self.assertEqual(prio.get("scope"), "contestacao")
        self.assertTrue(
            all(r.get("drill", {}).get("scope") == "contestacao" for r in prio["rows"])
        )

        resp = contest_dash["responsaveis"]
        self.assertEqual(resp.get("scope"), "contestacao")
        if resp["results"]:
            self.assertEqual(resp["results"][0]["drill"].get("scope"), "contestacao")

        contest = contest_dash["contestacao_leitura"]
        self.assertIsNotNone(contest["taxa_falha_pct"])
        # Volume alinhado ao KPI Auditados (linhas Contest*)
        self.assertEqual(
            contest["auditados_contestacao"],
            contest_kpis["auditados"],
        )
        self.assertEqual(
            contest["falhas_contestacao"],
            contest_kpis["falhas"],
        )
        # Taxa = falhas (linhas) ÷ auditados (linhas) — mesma base do EO
        expected = round(
            100.0
            * contest["falhas_contestacao"]
            / contest["auditados_contestacao"],
            1,
        )
        self.assertEqual(contest["taxa_falha_pct"], expected)
        self.assertEqual(contest["eo_pct"], contest_kpis["eo_pct"])
        # Protocolos distintos continua disponível (improcedência)
        self.assertGreater(contest["protocolos_contestados"], 0)
        self.assertLessEqual(
            contest["protocolos_contestados"],
            contest["auditados_contestacao"],
        )
        self.assertIn("clientes", contest)
        self.assertTrue(len(contest["clientes"]) >= 1)
        self.assertIn("improcedencia", contest)
        impro = contest["improcedencia"]
        self.assertEqual(
            impro["improcedentes"],
            max(0, impro["contestados"] - int(impro.get("procedentes") or 0)),
        )
        self.assertEqual(
            contest["link"]["route_name"],
            "indicadores-qualidade",
        )
        self.assertEqual(contest["link"]["module"], "contestacao")

        origem = payload.get("origem_auditorias") or {}
        origem_keys = {r["key"] for r in origem.get("rows") or []}
        self.assertTrue({"Manual", "Automático", "Processual"} <= origem_keys)

        melhorias = payload["clientes_melhorias"]["results"]
        self.assertTrue(any(r.get("participacao_falhas_pct") is not None for r in melhorias))
        c101 = next(r for r in melhorias if r["id_cliente"] == 101)
        self.assertIsNotNone(c101["participacao_falhas_pct"])
        self.assertIsNotNone(c101["delta_participacao_pp"])

        # Filtro por cliente reduz universo
        filtered = build_dashboard({**self.params, "id_cliente": "101"})
        self.assertEqual(filtered["clientes_melhorias"]["universe_count"], 1)
        self.assertEqual(filtered["clientes_melhorias"]["results"][0]["id_cliente"], 101)

    def test_dashboard_api_and_page_slice(self):
        res = self.api.get(
            "/api/v1/qualidade/operacional/dashboard/",
            {**self.params, "melhorias_page_size": 2, "melhorias_page": 1},
        )
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertGreaterEqual(data["clientes_melhorias"]["universe_count"], 6)
        self.assertEqual(len(data["clientes_melhorias"]["results"]), 2)
        res2 = self.api.get(
            "/api/v1/qualidade/operacional/dashboard/",
            {**self.params, "melhorias_page_size": 2, "melhorias_page": 2},
        )
        self.assertEqual(
            res2.data["clientes_melhorias"]["universe_count"],
            data["clientes_melhorias"]["universe_count"],
        )

    def test_no_n_plus_one_on_melhorias_build(self):
        # Warm-up
        build_clientes_melhorias_all(self.params)
        with CaptureQueriesContext(connection) as ctx:
            build_clientes_melhorias_all(self.params)
        # Agregações fixas (não 1 query por cliente)
        self.assertLess(len(ctx), 40)
