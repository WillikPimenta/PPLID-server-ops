# -*- coding: utf-8 -*-
from datetime import date

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.workforce.models import Agent, AgentHistory
from apps.qualidade_operacional.services.workforce_scope import (
    build_responsibility_index,
    clear_responsibility_index_cache,
    responsibility_for_date,
)
from apps.qualidade_operacional.services.analytics import (
    _eo_pct,
    build_breakdown,
    build_criticidade_breakdown,
    build_kpis,
    build_month_over_month_kpis,
    build_ranking,
    build_serie,
    failure_weight,
    filtered_falhas,
    shift_period,
)
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.queries import clone_params, resolve_date_axis
from django.http import QueryDict

User = get_user_model()


class EoMathTests(SimpleTestCase):
    def test_eo_pct(self):
        self.assertEqual(_eo_pct(18696, 104), 99.4)
        self.assertEqual(_eo_pct(100, 0), 100.0)
        self.assertIsNone(_eo_pct(0, 10))
    def test_failure_weight_matrix(self):
        # Matriz vale somente com data >= IMPACT_WEIGHT_CUTOVER (01/08/2026).
        cut = {"data": date(2026, 8, 1)}
        cases = (
            ({**cut, "etapa": "Análise Visual", "categoria_falha": "Falha Crítica", "nivel_dificuldade": "Fácil"}, 3.5),
            ({**cut, "etapa": "Análise Visual", "categoria_falha": "Crítica", "nivel_dificuldade": "Média"}, 3.0),
            ({**cut, "etapa": "Análise Visual", "categoria_falha": "Crítica", "nivel_dificuldade": "Difícil"}, 2.5),
            ({**cut, "etapa": "Sobreposição e Validações", "categoria_falha": "Procedimento", "nivel_dificuldade": "Fácil"}, 1.5),
            ({**cut, "etapa": "Validação cadastral", "categoria_falha": "Procedimento", "nivel_dificuldade": "Média"}, 1.3),
            ({**cut, "etapa": "Análise Visual", "categoria_falha": "Procedimento", "nivel_dificuldade": "Fácil"}, 1.0),
            ({**cut, "etapa": "Análise Visual", "categoria_falha": "Não Crítica", "nivel_dificuldade": "Fácil"}, 1.0),
            ({**cut, "etapa": "Análise Visual", "categoria_falha": "Falha Crítica", "nivel_dificuldade": "Cenários avaliativos"}, 0.0),
            ({**cut, "id_cliente": 9999, "etapa": "Análise Visual", "categoria_falha": "Falha Crítica", "nivel_dificuldade": "Fácil"}, 1.0),
            # CLARO - FORMALIZAÇÃO: "Não informada"/vazio = Procedimento com peso 1 (sem majoração).
            ({**cut, "id_cliente": 9999, "etapa": "Sobreposição e Validações", "categoria_falha": "", "nivel_dificuldade": "Fácil"}, 1.0),
            ({**cut, "id_cliente": 9999, "etapa": "Validação cadastral", "categoria_falha": "Não informada", "nivel_dificuldade": "Média"}, 1.0),
        )
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(failure_weight(row), expected)

    def test_failure_weight_temporal_cutover(self):
        from apps.qualidade_operacional.services.analytics import (
            CENARIOS_AVALIATIVOS_ZERO_CUTOVER,
            IMPACT_WEIGHT_CUTOVER,
        )

        self.assertEqual(IMPACT_WEIGHT_CUTOVER, date(2026, 8, 1))
        self.assertEqual(CENARIOS_AVALIATIVOS_ZERO_CUTOVER, date(2026, 1, 4))
        base = {
            "etapa": "Análise Visual",
            "categoria_falha": "Falha Crítica",
            "nivel_dificuldade": "Fácil",
        }
        self.assertEqual(failure_weight({**base, "data": date(2026, 7, 31)}), 1.0)
        self.assertEqual(failure_weight({**base, "data": date(2026, 8, 1)}), 3.5)
        self.assertEqual(
            failure_weight(
                {
                    "etapa": "Análise Visual",
                    "categoria_falha": "Falha Crítica",
                    "nivel_dificuldade": "Cenários Avaliativos",
                    "data": date(2026, 1, 3),
                }
            ),
            1.0,
        )
        self.assertEqual(
            failure_weight(
                {
                    "etapa": "Análise Visual",
                    "categoria_falha": "Falha Crítica",
                    "nivel_dificuldade": "Cenários Avaliativos",
                    "data": date(2026, 1, 4),
                }
            ),
            0.0,
        )
        self.assertEqual(
            failure_weight(
                {
                    "etapa": "Análise Visual",
                    "categoria_falha": "Falha Crítica",
                    "nivel_dificuldade_confer": "CENÁRIOS AVALIATIVOS",
                    "nivel_dificuldade": "Fácil",
                    "data": date(2026, 7, 31),
                }
            ),
            0.0,
        )
        self.assertEqual(failure_weight({**base, "data": None}), 1.0)
        # date_axis não altera o peso: sempre QualidadeFalha.data
        self.assertEqual(
            failure_weight(
                {
                    **base,
                    "data": date(2026, 7, 31),
                    "data_analise": date(2026, 8, 2),
                }
            ),
            1.0,
        )
        self.assertEqual(
            failure_weight(
                {
                    **base,
                    "data": date(2026, 8, 1),
                    "data_analise": date(2026, 7, 30),
                }
            ),
            3.5,
        )

    def test_shift_period_civil_full_months(self):
        """Mês civil completo → mês civil anterior (não janela de N dias iguais)."""
        cases = (
            (date(2026, 7, 1), date(2026, 7, 31), date(2026, 6, 1), date(2026, 6, 30)),
            (date(2026, 6, 1), date(2026, 6, 30), date(2026, 5, 1), date(2026, 5, 31)),
            (date(2026, 5, 1), date(2026, 5, 31), date(2026, 4, 1), date(2026, 4, 30)),
            (date(2026, 2, 1), date(2026, 2, 28), date(2026, 1, 1), date(2026, 1, 31)),
            (date(2024, 2, 1), date(2024, 2, 29), date(2024, 1, 1), date(2024, 1, 31)),
            (date(2026, 1, 1), date(2026, 1, 31), date(2025, 12, 1), date(2025, 12, 31)),
            (date(2026, 4, 1), date(2026, 4, 30), date(2026, 3, 1), date(2026, 3, 31)),
        )
        for start, end, exp_s, exp_e in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(shift_period(start, end), (exp_s, exp_e))
        # Regressão explícita do bug pré-correção (jul → 31/05–30/06).
        self.assertNotEqual(
            shift_period(date(2026, 7, 1), date(2026, 7, 31)),
            (date(2026, 5, 31), date(2026, 6, 30)),
        )

    def test_shift_period_mtd_same_calendar_days(self):
        self.assertEqual(
            shift_period(date(2026, 7, 1), date(2026, 7, 24)),
            (date(2026, 6, 1), date(2026, 6, 24)),
        )
        self.assertEqual(
            shift_period(date(2026, 7, 10), date(2026, 7, 25)),
            (date(2026, 6, 10), date(2026, 6, 25)),
        )
        # Clamp: mar completo → fev completo (2026 não bissexto)
        self.assertEqual(
            shift_period(date(2026, 3, 1), date(2026, 3, 31)),
            (date(2026, 2, 1), date(2026, 2, 28)),
        )

    def test_date_axis_default_is_auditoria(self):
        self.assertEqual(resolve_date_axis({}), "auditoria")
        self.assertEqual(resolve_date_axis({"date_axis": ""}), "auditoria")
        self.assertEqual(resolve_date_axis({"date_axis": "analise"}), "analise")

    def test_clone_params_preserves_querydict_multi(self):
        qd = QueryDict("id_cliente=1&id_cliente=2&start_date=2026-07-01")
        cloned = clone_params(qd)
        self.assertEqual(cloned["id_cliente"], ["1", "2"])
        self.assertEqual(cloned["start_date"], "2026-07-01")


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class EoAnalyticsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="eo_user", password="x", email="eo@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.leader = Agent.objects.create(
            full_name="Lider Teste", user_lan_id="lider_teste", active=True
        )
        self.valid_agent = Agent.objects.create(
            full_name="Agente Base", user_lan_id="c90000a", active=True
        )
        AgentHistory.objects.create(
            agent=self.valid_agent,
            leader=self.leader,
            team="Operacao Teste",
            start_date=date(2026, 1, 1),
            active=True,
        )


        for i in range(100):
            QualidadeAuditado.objects.create(
                data=date(2026, 7, 10),
                data_analise=date(2026, 7, 10),
                id_cliente=6,
                id_workflow=144,
                tipo_analise="Auditoria Compliance",
                matricula=f"c90{i:03d}a",
                protocolo=f"P{i}",
                etapa="Etapa A",
                tipo_conclusao="Manual" if i < 80 else "Automático",
                source_file="test",
            )
        for i in range(5):
            QualidadeFalha.objects.create(
                data=date(2026, 7, 10),
                data_analise=date(2026, 7, 10),
                id_cliente=6,
                id_workflow=144,
                tipo_analise="Auditoria Compliance",
                matricula=f"c90{i:03d}a",
                protocolo=f"P{i}",
                tipo_falha="Manual",
                lider="Lider Teste",
                localidade="Brasília",
                source_file="test",
            )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 11),
            data_analise=date(2026, 7, 11),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="c90001a",
            protocolo="PX",
            tipo_falha="Automático",
            lider="Lider Teste",
            localidade="São Carlos",
            source_file="test",
        )

    def test_build_month_over_month_kpis_mtd_same_calendar_days(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 6, 10),
            protocolo="JUN-AUD",
            matricula="c90000a",
            localidade_documento="DF",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 5, 10),
            protocolo="MAI-AUD",
            matricula="c90000a",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 6, 12),
            protocolo="JUN-FAL",
            matricula="c90000a",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 5, 12),
            protocolo="MAI-FAL",
            matricula="c90000a",
            tipo_falha="Manual",
            source_file="test",
        )

        params = {
            "start_date": "2026-06-01",
            "end_date": "2026-06-19",
            "grain": "etapa",
        }
        block = build_month_over_month_kpis(params, anchor_end=date(2026, 6, 19))

        self.assertTrue(block["ok"])
        self.assertEqual(block["current_start_date"], "2026-06-01")
        self.assertEqual(block["current_end_date"], "2026-06-19")
        self.assertEqual(block["previous_start_date"], "2026-05-01")
        self.assertEqual(block["previous_end_date"], "2026-05-19")
        self.assertEqual(block["anchor_label"], "Jun/26")
        self.assertEqual(block["previous_label"], "Mai/26")
        self.assertEqual(block["current"]["auditados"], 1)
        self.assertEqual(block["previous"]["auditados"], 1)
        self.assertTrue(block["comparable"])
        self.assertIsNotNone(block["delta_eo_ponderado_pp"])

    def test_criticidade_labels_and_unclassified_filter_match(self):
        failures = list(QualidadeFalha.objects.order_by("id")[:2])
        failures[0].categoria_falha = "Falha Crítica"
        failures[0].save(update_fields=["categoria_falha"])
        failures[1].categoria_falha = "Não Crítica"
        failures[1].save(update_fields=["categoria_falha"])
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        fal_qs = filtered_falhas(params)
        breakdown = build_criticidade_breakdown(
            fal_qs, grain="etapa", total_aud=100
        )
        rows = {row["label"]: row for row in breakdown["rows"]}
        self.assertEqual(set(rows), {"Crítica", "Não Crítica", "Não informada"})
        self.assertEqual(rows["Não informada"]["falhas"], 4)
        unclassified = filtered_falhas(
            {**params, "categoria_falha": "__unclassified__"}
        )
        critical = filtered_falhas({**params, "categoria_falha": "Crítica"})
        self.assertEqual(unclassified.count(), rows["Não informada"]["falhas"])
        self.assertEqual(critical.count(), 1)

    def test_nivel_dificuldade_filter_uses_effective_value(self):
        failures = list(QualidadeFalha.objects.order_by("id")[:2])
        failures[0].nivel_dificuldade = "Fácil Original"
        failures[0].nivel_dificuldade_confer = "Difícil Confer"
        failures[0].save(update_fields=["nivel_dificuldade", "nivel_dificuldade_confer"])
        failures[1].nivel_dificuldade = "Média"
        failures[1].nivel_dificuldade_confer = ""
        failures[1].save(update_fields=["nivel_dificuldade", "nivel_dificuldade_confer"])
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        confer = filtered_falhas({**params, "nivel_dificuldade": "Difícil Confer"})
        media = filtered_falhas({**params, "nivel_dificuldade": "Média"})
        self.assertEqual(confer.count(), 1)
        self.assertEqual(media.count(), 1)

    def test_categoria_falha_multi_filter_or(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        both = filtered_falhas(
            {**params, "categoria_falha": ["Crítica", "Não Crítica"]}
        )
        critical = filtered_falhas({**params, "categoria_falha": "Crítica"})
        non_critical = filtered_falhas({**params, "categoria_falha": "Não Crítica"})
        self.assertEqual(both.count(), critical.count() + non_critical.count())

    def test_cliente_9999_is_procedimento_and_filters_match(self):
        QualidadeFalha.objects.create(
            data=date(2026, 7, 12),
            data_analise=date(2026, 7, 12),
            id_cliente=9999,
            id_workflow=9999,
            protocolo="P9999",
            etapa="Análise Visual",
            categoria_falha="",
            nivel_dificuldade="Fácil",
            tipo_falha="Manual",
            source_file="test",
        )
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        breakdown = build_criticidade_breakdown(
            filtered_falhas(params), grain="etapa", total_aud=100
        )
        rows = {row["label"]: row for row in breakdown["rows"]}

        self.assertEqual(rows["Procedimento"]["falhas"], 1)
        self.assertEqual(rows["Procedimento"]["impacto_ponderado"], 1.0)
        self.assertEqual(
            filtered_falhas({**params, "categoria_falha": "Procedimento"}).count(),
            1,
        )
        self.assertEqual(
            filtered_falhas(
                {**params, "categoria_falha": "__unclassified__"}
            ).count(),
            6,
        )

    def test_build_kpis_eo(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        kpis = build_kpis(params)
        self.assertEqual(kpis["auditados"], 100)
        self.assertEqual(kpis["falhas"], 6)
        self.assertEqual(kpis["eo_pct"], 94.0)
        by_tipo = {r["label"]: r for r in kpis["by_tipo_conclusao"]}
        self.assertEqual(by_tipo["Manual"]["auditados"], 80)
        self.assertEqual(by_tipo["Manual"]["falhas"], 5)
        self.assertEqual(by_tipo["Manual"]["eo_pct"], 93.8)
        self.assertEqual(by_tipo["Manual"]["group"], "tipificacao_pareada")
        self.assertIn("formula", by_tipo["Manual"])
        self.assertEqual(by_tipo["Automático"]["auditados"], 20)
        self.assertEqual(by_tipo["Automático"]["falhas"], 1)
        self.assertEqual(by_tipo["Automático"]["eo_pct"], 95.0)
        # Processual: denom = total auditados
        self.assertEqual(by_tipo["Processual"]["auditados"], 100)
        self.assertEqual(by_tipo["Processual"]["denominator_mode"], "total")
        self.assertEqual(by_tipo["Processual"]["group"], "tipificacao_denom_total")
        prev = kpis["previous"]
        self.assertIn("start_date", prev)
        self.assertIn("end_date", prev)
        self.assertIn("comparable", prev)
        self.assertIn("interpretation", prev)
        self.assertEqual(prev["current_eo_pct"], 94.0)
        # Jul completo → jun civil (não 31/05–30/06)
        self.assertEqual(prev["start_date"], "2026-06-01")
        self.assertEqual(prev["end_date"], "2026-06-30")

    def test_previous_preserves_multivalue_filters(self):
        """QueryDict multi id_cliente não pode colapsar no período anterior."""
        for i in range(10):
            QualidadeAuditado.objects.create(
                data=date(2026, 6, 15),
                data_analise=date(2026, 6, 15),
                id_cliente=6,
                protocolo=f"J6-{i}",
                tipo_conclusao="Manual",
                source_file="test",
            )
        for i in range(20):
            QualidadeAuditado.objects.create(
                data=date(2026, 6, 15),
                data_analise=date(2026, 6, 15),
                id_cliente=99,
                protocolo=f"J99-{i}",
                tipo_conclusao="Manual",
                source_file="test",
            )
        qd = QueryDict(mutable=True)
        qd.update(
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "grain": "etapa",
                "date_axis": "auditoria",
            }
        )
        qd.setlist("id_cliente", ["6", "99"])
        kpis = build_kpis(qd)
        # Jul: só cliente 6 (fixtures); Jun: 10+20 se multi preservado; 20 se colapsar no último.
        self.assertEqual(kpis["auditados"], 100)
        self.assertEqual(kpis["previous"]["start_date"], "2026-06-01")
        self.assertEqual(kpis["previous"]["end_date"], "2026-06-30")
        self.assertEqual(kpis["previous"]["auditados"], 30)
    def test_build_kpis_keeps_real_and_weighted_eo(self):
        failure = QualidadeFalha.objects.order_by("id").first()
        failure.etapa = "Análise Visual"
        failure.categoria_falha = "Falha Crítica"
        failure.nivel_dificuldade = "Fácil"
        failure.save(
            update_fields=["etapa", "categoria_falha", "nivel_dificuldade"]
        )
        # Jul/2026 é anterior ao corte 01/08: impacto 1:1 (não aplica matriz).
        kpis = build_kpis(
            {"start_date": "2026-07-01", "end_date": "2026-07-31", "grain": "etapa"}
        )
        self.assertEqual(kpis["falhas"], 6)
        self.assertEqual(kpis["eo_pct"], 94.0)
        self.assertEqual(kpis["impacto_ponderado"], 6.0)
        self.assertEqual(kpis["eo_ponderado_pct"], 94.0)

        # Após o corte, a mesma criticidade usa a matriz (3.5).
        QualidadeFalha.objects.create(
            data=date(2026, 8, 1),
            data_analise=date(2026, 8, 1),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="c90000a",
            protocolo="PAUG",
            etapa="Análise Visual",
            categoria_falha="Falha Crítica",
            nivel_dificuldade="Fácil",
            tipo_falha="Manual",
            lider="Lider Teste",
            localidade="Brasília",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 1),
            data_analise=date(2026, 8, 1),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="c90000a",
            protocolo="PAUG",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        cache.clear()
        kpis_aug = build_kpis(
            {"start_date": "2026-08-01", "end_date": "2026-08-31", "grain": "etapa"}
        )
        self.assertEqual(kpis_aug["falhas"], 1)
        self.assertEqual(kpis_aug["impacto_ponderado"], 3.5)
        self.assertGreaterEqual(kpis_aug["auditados"], 1)
        # Com 1 auditado e impacto 3.5 o EO ponderado satura em 0 (clamp).
        self.assertEqual(kpis_aug["eo_ponderado_pct"], 0.0)

    def test_build_kpis_protocolo(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "protocolo",
        }
        kpis = build_kpis(params)
        self.assertEqual(kpis["auditados"], 100)
        self.assertEqual(kpis["falhas"], 6)

    def test_reconcile_origem_auditados_and_falhas(self):
        """Manual+Automático (auditados) ≈ total; sem card Outros."""
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        kpis = build_kpis(params)
        by_tipo = {r["label"]: r for r in kpis["by_tipo_conclusao"]}
        self.assertNotIn("Outros", by_tipo)
        self.assertEqual(set(by_tipo), {"Manual", "Automático", "Processual"})
        origem_aud = by_tipo["Manual"]["auditados"] + by_tipo["Automático"]["auditados"]
        self.assertEqual(origem_aud, kpis["auditados"])
        self.assertEqual(by_tipo["Processual"]["auditados"], kpis["auditados"])
        fal_sum = sum(
            by_tipo[k]["falhas"] for k in ("Manual", "Automático", "Processual")
        )
        self.assertEqual(fal_sum, kpis["falhas"])

    def test_residual_falha_goes_to_manual(self):
        QualidadeFalha.objects.create(
            data=date(2026, 7, 15),
            data_analise=date(2026, 7, 15),
            id_cliente=6,
            protocolo="FQ0",
            tipo_falha="Biometria",
            source_file="test",
        )
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        kpis = build_kpis(params)
        by_tipo = {r["label"]: r for r in kpis["by_tipo_conclusao"]}
        self.assertNotIn("Outros", by_tipo)
        self.assertEqual(by_tipo["Manual"]["falhas"], 6)  # 5 Manual + 1 Biometria
        bd = build_breakdown({**params, "dim": "tipo_analise", "metric": "taxa"})
        self.assertEqual(bd["metric"], "taxa")
        self.assertIn("Taxa", bd["chart_title"])

    def test_breakdown_metrics(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
            "dim": "tipo_falha",
        }
        qtd = build_breakdown({**params, "metric": "quantidade"})
        part = build_breakdown({**params, "metric": "participacao"})
        taxa = build_breakdown({**params, "metric": "taxa"})
        self.assertEqual(qtd["metric"], "quantidade")
        self.assertTrue(
            qtd["chart_title"].startswith("Concentração")
            or qtd["chart_title"].startswith("Pareto")
            or qtd["chart_title"].startswith("Quantidade")
        )
        self.assertIn("80%", qtd["pareto_note"])
        self.assertNotIn("Outros", {r["label"] for r in qtd["rows"]})
        manual_q = next(r for r in qtd["rows"] if r["label"] == "Manual")
        self.assertEqual(manual_q["value"], 5)
        manual_p = next(r for r in part["rows"] if r["label"] == "Manual")
        self.assertEqual(manual_p["value"], round(100.0 * 5 / 6, 1))
        manual_t = next(r for r in taxa["rows"] if r["label"] == "Manual")
        self.assertEqual(manual_t["value"], round(100.0 * 5 / 80, 1))
        # Regressão: mesmos auditados/falhas/eo independente da métrica
        self.assertEqual(manual_q["auditados"], manual_p["auditados"])
        self.assertEqual(manual_q["falhas"], manual_t["falhas"])
        self.assertEqual(manual_q["eo_pct"], manual_t["eo_pct"])

    def test_previous_incomparable_when_no_prior_auditados(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
            "id_cliente": 99999,
        }
        kpis = build_kpis(params)
        self.assertEqual(kpis["auditados"], 0)
        self.assertFalse(kpis["previous"]["comparable"])
        self.assertIsNone(kpis["previous"]["delta_pp"])
        self.assertIn("comparáveis", kpis["previous"]["interpretation"].lower())

    def test_serie_and_breakdown(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        serie = build_serie({**params, "mode": "volume"})
        self.assertTrue(serie["points"])
        cards = build_breakdown({**params, "dim": "tipo_falha"})
        by_label = {r["label"]: r for r in cards["rows"]}
        self.assertEqual(by_label["Manual"]["auditados"], 80)
        self.assertEqual(by_label["Automático"]["auditados"], 20)
        self.assertNotEqual(by_label["Manual"]["eo_pct"], by_label["Automático"]["eo_pct"])
        by_conc = build_breakdown({**params, "dim": "tipo_conclusao"})
        self.assertIn("Manual", {r["label"] for r in by_conc["rows"]})
        by_tipo = build_breakdown({**params, "dim": "tipo_analise"})
        self.assertGreaterEqual(len(by_tipo["rows"]), 1)

    def test_filter_tipo_conclusao_scopes_falhas(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
            "tipo_conclusao": "Manual",
        }
        kpis = build_kpis(params)
        self.assertEqual(kpis["auditados"], 80)
        self.assertEqual(kpis["falhas"], 5)

    def test_date_axis_selects_date_field_for_auditados_and_falhas(self):
        """O eixo seleciona a mesma coluna de período nas duas populações."""
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 20),
            data_analise=date(2026, 6, 1),
            id_cliente=6,
            protocolo="AAXIS",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 20),
            data_analise=date(2026, 6, 1),
            id_cliente=6,
            protocolo="FAXIS",
            tipo_falha="Manual",
            source_file="test",
        )
        base = {"start_date": "2026-07-01", "end_date": "2026-07-31", "grain": "etapa"}
        analise = build_kpis({**base, "date_axis": "analise"})
        auditoria = build_kpis({**base, "date_axis": "auditoria"})
        self.assertEqual(analise["date_field_falhas"], "data_analise")
        self.assertEqual(analise["date_field_auditados"], "data_analise")
        self.assertEqual(auditoria["date_field_auditados"], "data")
        self.assertEqual(analise["auditados"], 100)
        self.assertEqual(auditoria["auditados"], 101)
        self.assertEqual(auditoria["date_field_falhas"], "data")
        # setUp: 6 com data_analise em julho; FAXIS tem análise em junho → analise=6
        self.assertEqual(analise["falhas"], 6)
        # setUp + FAXIS preenchem `data` em julho → auditoria=7
        self.assertEqual(auditoria["falhas"], 7)
        analise_list = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {**base, "date_axis": "analise"},
        )
        auditoria_list = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {**base, "date_axis": "auditoria"},
        )
        self.assertEqual(analise_list.data["count"], 100)
        self.assertEqual(auditoria_list.data["count"], 101)

    def test_tipo_bucket_outros_maps_to_manual(self):
        """URL legado tipo_bucket=Outros passa a filtrar como Manual (residual)."""
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 12),
            id_cliente=6,
            protocolo="POUT",
            tipo_conclusao="",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 12),
            id_cliente=6,
            protocolo="PMAP",
            tipo_conclusao="Mapeamento",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 12),
            data_analise=date(2026, 7, 12),
            id_cliente=6,
            protocolo="FOUT",
            tipo_falha="Biometria",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 12),
            data_analise=date(2026, 7, 12),
            id_cliente=6,
            protocolo="FMAP",
            tipo_falha="Mapeamento",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 12),
            data_analise=date(2026, 7, 12),
            id_cliente=6,
            protocolo="FSYS",
            tipo_falha="Sistema",
            source_file="test",
        )
        from apps.qualidade_operacional.services.queries import (
            apply_common_filters,
            apply_falha_filters,
        )

        aud = apply_common_filters(
            QualidadeAuditado.objects.all(),
            {"start_date": "2026-07-01", "end_date": "2026-07-31", "tipo_bucket": "Outros"},
            date_field="data",
        )
        fal = apply_falha_filters(
            QualidadeFalha.objects.all(),
            {"start_date": "2026-07-01", "end_date": "2026-07-31", "tipo_bucket": "Outros"},
        )
        self.assertTrue(aud.filter(protocolo="POUT").exists())
        self.assertFalse(aud.filter(protocolo="PMAP").exists())
        self.assertTrue(fal.filter(protocolo="FOUT").exists())
        self.assertFalse(fal.filter(protocolo="FMAP").exists())
        self.assertFalse(fal.filter(protocolo="FSYS").exists())

        auto = apply_common_filters(
            QualidadeAuditado.objects.all(),
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "tipo_conclusao": "Automático",
            },
            date_field="data",
        )
        self.assertTrue(auto.filter(protocolo="PMAP").exists())

        manual = apply_falha_filters(
            QualidadeFalha.objects.all(),
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "tipo_conclusao": "Manual",
            },
        )
        self.assertTrue(manual.filter(protocolo="FOUT").exists())
        self.assertTrue(manual.filter(tipo_falha="Manual").exists())
        self.assertFalse(manual.filter(protocolo="FMAP").exists())

    def test_multi_filters_id_cliente_and_tipo_conclusao(self):
        from apps.qualidade_operacional.services.queries import (
            apply_common_filters,
            apply_falha_filters,
        )

        QualidadeAuditado.objects.create(
            data=date(2026, 7, 15),
            id_cliente=7,
            protocolo="PCLI7",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 15),
            data_analise=date(2026, 7, 15),
            id_cliente=7,
            protocolo="FCLI7",
            tipo_falha="Manual",
            source_file="test",
        )
        aud = apply_common_filters(
            QualidadeAuditado.objects.all(),
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "id_cliente": ["6", "7"],
                "tipo_conclusao": ["Manual", "Automático"],
            },
            date_field="data",
        )
        self.assertTrue(aud.filter(protocolo="PCLI7").exists())
        self.assertTrue(aud.filter(id_cliente=6).exists())

        fal = apply_falha_filters(
            QualidadeFalha.objects.all(),
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "id_cliente": ["6", "7"],
                "tipo_conclusao": ["Manual"],
            },
        )
        self.assertTrue(fal.filter(protocolo="FCLI7").exists())
        self.assertFalse(fal.filter(tipo_falha="Automático").exists())

    def test_api_insights_ok(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/insights/",
            {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])
        self.assertIn("veredito", res.data)
        self.assertIn("resumo_executivo", res.data)
        self.assertIn("resumo_topicos", res.data)
        self.assertIn("situacao", res.data["resumo_topicos"])
        self.assertIn("semaforo", res.data)
        self.assertIn("top_oportunidades", res.data)
        self.assertIn("diagnostico", res.data)
        self.assertEqual(res.data.get("piores_agentes"), [])
        self.assertTrue(res.data["top_oportunidades"])
        self.assertIn("top_clientes", res.data)
        self.assertTrue(res.data["top_clientes"])
        self.assertEqual(res.data["top_clientes"][0]["id_cliente"], 6)
        # Labels sem ID numérico entre parênteses
        self.assertNotIn("(6)", res.data["top_clientes"][0]["label"])
        # Avaliação completa: todos os clientes (não só top 5), com conformes
        self.assertGreaterEqual(len(res.data["top_clientes"]), 1)
        first = res.data["top_clientes"][0]
        self.assertIn("conformes", first)
        self.assertEqual(
            first["conformes"],
            max(0, int(first["auditados"]) - int(first["falhas"])),
        )

    def test_insights_structural_no_agent_priority(self):
        from apps.qualidade_operacional.services.insights import build_insights

        data = build_insights(
            {"start_date": "2026-07-01", "end_date": "2026-07-31"}
        )
        self.assertEqual(data["piores_agentes"], [])
        self.assertTrue(data["resumo_executivo"])
        self.assertIn(data["semaforo"]["status"], {
            "ok", "atencao", "proximo_limite", "fora_meta", "indefinido"
        })
        self.assertLessEqual(len(data["top_oportunidades"]), 3)
        self.assertNotIn("Agente", {p["tipo"] for p in data["prioridades"]})
        top = data["top_oportunidades"][0]
        self.assertIn("impacto", top)
        self.assertIn("falha_share_pct", top)

    def test_breakdown_id_cliente(self):
        data = build_breakdown(
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "dim": "id_cliente",
                "metric": "quantidade",
            }
        )
        self.assertEqual(data["dim"], "id_cliente")
        self.assertTrue(data["rows"])
        self.assertEqual(data["rows"][0]["key"], "6")
        self.assertEqual(data["rows"][0]["falhas"], 6)

    def test_ranking_agent_label_uses_name(self):
        self.valid_agent.full_name = "Agente Noventa"
        self.valid_agent.save(update_fields=["full_name"])
        ranking = build_ranking(
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "by": "agente",
                "limit": 200,
            }
        )
        row = next(r for r in ranking["results"] if r["key"] == "c90000a")
        self.assertEqual(row["label"], "Agente Noventa")
        self.assertEqual(row["matricula"], "c90000a")

    def test_ranking_agente(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "by": "agente",
            "limit": 50,
        }
        ranking = build_ranking(params)
        self.assertGreaterEqual(ranking["count"], 1)
        self.assertEqual(ranking["eligible_agents"], 1)
        self.assertEqual([row["key"] for row in ranking["results"]], ["c90000a"])
        self.assertEqual(len(ranking["quartis"]), 4)
        self.assertIn("impacto_ponderado", ranking["results"][0])
        self.assertIn("eo_ponderado_pct", ranking["results"][0])
        self.assertIn("delta_pp", ranking["m1"])

    def test_ranking_does_not_attribute_processual_failure_to_agent(self):
        QualidadeFalha.objects.filter(matricula="c90000a").delete()
        for tipo_falha, protocolo in (("Manual", "MAN-1"), ("Processual", "PROC-1")):
            QualidadeFalha.objects.create(
                data=date(2026, 7, 15),
                data_analise=date(2026, 7, 15),
                id_cliente=6,
                id_workflow=144,
                tipo_analise="Auditoria Compliance",
                matricula="c90000a",
                protocolo=protocolo,
                tipo_falha=tipo_falha,
                source_file="test",
            )

        row = build_ranking(
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "by": "agente",
            }
        )["results"][0]

        self.assertEqual(row["key"], "c90000a")
        self.assertEqual(row["falhas"], 1)
        self.assertEqual(row["impacto_ponderado"], 1.0)

    def test_ranking_criticidade_counts_by_matricula(self):
        """Ranking expõe falhas Crítica / Não Crítica / Procedimento (não quartil)."""
        QualidadeFalha.objects.filter(matricula="c90000a").delete()
        specs = (
            ("Falha Crítica", "PC1"),
            ("Crítica", "PC2"),
            ("Não Crítica", "PNC1"),
            ("Procedimento", "PP1"),
            ("", "PNI1"),  # Não informada — fora das 3 colunas
        )
        for categoria, protocolo in specs:
            QualidadeFalha.objects.create(
                data=date(2026, 7, 15),
                data_analise=date(2026, 7, 15),
                id_cliente=6,
                id_workflow=144,
                tipo_analise="Auditoria Compliance",
                matricula="c90000a",
                protocolo=protocolo,
                categoria_falha=categoria,
                tipo_falha="Manual",
                lider="Lider Teste",
                localidade="Brasília",
                source_file="test",
            )
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "by": "agente",
            "limit": 50,
        }
        row = build_ranking(params)["results"][0]
        self.assertEqual(row["key"], "c90000a")
        self.assertEqual(row["falhas_criticas"], 2)
        self.assertEqual(row["falhas_nao_criticas"], 1)
        self.assertEqual(row["falhas_procedimento"], 1)
        self.assertEqual(row["falhas"], 5)

        leader = build_ranking({**params, "by": "lider"})["results"][0]
        self.assertEqual(leader["label"], "Lider Teste")
        self.assertEqual(leader["falhas_criticas"], 2)
        self.assertEqual(leader["falhas_nao_criticas"], 1)
        self.assertEqual(leader["falhas_procedimento"], 1)

    def test_build_kpis_criticidade_totals(self):
        """KPIs globais expõem totais por criticidade."""
        QualidadeFalha.objects.filter(matricula="c90000a").delete()
        specs = (
            ("Falha Crítica", "KPC1"),
            ("Crítica", "KPC2"),
            ("Não Crítica", "KPNC1"),
            ("Procedimento", "KPP1"),
        )
        for categoria, protocolo in specs:
            QualidadeFalha.objects.create(
                data=date(2026, 7, 15),
                data_analise=date(2026, 7, 15),
                id_cliente=6,
                id_workflow=144,
                tipo_analise="Auditoria Compliance",
                matricula="c90000a",
                protocolo=protocolo,
                categoria_falha=categoria,
                tipo_falha="Manual",
                source_file="test",
            )
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
        }
        kpis = build_kpis(params)
        self.assertEqual(kpis["falhas_criticas"], 2)
        self.assertEqual(kpis["falhas_nao_criticas"], 1)
        self.assertEqual(kpis["falhas_procedimento"], 1)

    def test_leader_scope_filters_auditados_and_falhas(self):
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "lider": "Lider Teste",
            "workforce_only": "1",
        }
        kpis = build_kpis(params)
        self.assertEqual(kpis["auditados"], 1)
        self.assertEqual(kpis["falhas"], 1)

        ranking = build_ranking({**params, "by": "lider"})
        self.assertEqual(ranking["count"], 1)
        leader = ranking["results"][0]
        self.assertEqual(leader["label"], "Lider Teste")
        self.assertEqual(leader["auditados"], 1)
        self.assertEqual(leader["falhas"], 1)
        self.assertEqual(leader["agentes"], 1)

        detail = self.client.get(
            "/api/v1/qualidade/operacional/auditados/", params
        )
        self.assertEqual(detail.data["count"], 1)

    def test_api_kpis_ok(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/kpis/",
            {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["eo_pct"], 94.0)

    def test_api_ranking_ok(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/ranking/",
            {"start_date": "2026-07-01", "end_date": "2026-07-31", "by": "lider"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])

    def test_dashboard_agents_consolidates_operational_payload(self):
        res = self.client.get(
            "/api/v1/qualidade/operacional/dashboard/",
            {
                "module": "agentes",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "by": "agente",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["module"], "agentes")
        self.assertIn("ranking", res.data)
        self.assertIn("serie", res.data)
        self.assertIn("workforce_dimensions", res.data)
        self.assertIn("localidade_hc", res.data["workforce_dimensions"])
        self.assertIn("turno", res.data["workforce_dimensions"])
        self.assertTrue(res.data["serie"].get("weighted"))
        self.assertIn("eo_ponderado_pct", res.data["kpis"])
        self.assertIn("impacto_ponderado", res.data["kpis"])
        self.assertEqual(
            set(res.data["operational"]),
            {"etapa", "criticidade", "id_cliente"},
        )
        self.assertIn("qualidade;dur=", res.headers["Server-Timing"])

    def test_dashboard_agents_keeps_line_protocol_and_workforce_totals_explicit(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 12),
            data_analise=date(2026, 7, 12),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="c90000a",
            protocolo="P0",
            etapa="Etapa B",
            tipo_conclusao="Manual",
            source_file="test",
        )
        cache.clear()
        params = {
            "module": "agentes",
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "date_axis": "auditoria",
            "by": "agente",
        }

        etapa = build_dashboard({**params, "grain": "etapa"})
        self.assertEqual(etapa["kpis"]["auditados"], 2)
        self.assertEqual(etapa["kpis"]["protocolos_auditados"], 1)
        self.assertEqual(sum(r["auditados"] for r in etapa["ranking"]["results"]), 2)
        coverage = etapa["workforce_coverage"]
        self.assertEqual(coverage["total_rows"], 101)
        self.assertEqual(coverage["eligible_rows"], 101)
        self.assertEqual(coverage["scoped_rows"], 2)
        self.assertEqual(coverage["unmatched_rows"], 99)
        self.assertEqual(coverage["scoped_matriculas"], 1)
        self.assertEqual(coverage["unmatched_matriculas"], 99)

        protocolo = build_dashboard({**params, "grain": "protocolo"})
        self.assertEqual(protocolo["kpis"]["auditados"], 1)
        self.assertEqual(protocolo["kpis"]["protocolos_auditados"], 1)
        self.assertEqual(
            sum(r["auditados"] for r in protocolo["ranking"]["results"]),
            1,
        )

    def test_dashboard_second_build_uses_versioned_cache(self):
        cache.clear()
        params = {
            "module": "resumo",
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "dim": "id_cliente",
            "metric": "quantidade",
        }
        first = build_dashboard(params)
        with self.assertNumQueries(0):
            second = build_dashboard(params)
        self.assertEqual(second["kpis"], first["kpis"])

    def test_contestacao_metrics_from_qualidade_tipo_analise(self):
        """Contestações vêm de falhas/auditados com tipo Contestação*, não do módulo atividades."""
        from apps.qualidade_operacional.services.contestacao_metrics import (
            build_contestacao_metrics,
            contestacao_by_matricula,
        )
        from apps.qualidade_operacional.services.normalize import (
            is_contestacao_tipo_analise,
        )

        self.assertTrue(is_contestacao_tipo_analise("Contestação Compliance"))
        self.assertTrue(is_contestacao_tipo_analise("Contestacao Externa"))
        self.assertFalse(is_contestacao_tipo_analise("Auditoria Compliance"))
        self.assertFalse(is_contestacao_tipo_analise("G Auditoria"))

        QualidadeAuditado.objects.create(
            data=date(2026, 7, 15),
            data_analise=date(2026, 7, 15),
            id_cliente=6,
            protocolo="PCNT1",
            tipo_analise="Contestação Interna",
            tipo_conclusao="Manual",
            matricula="c90000a",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 16),
            data_analise=date(2026, 7, 16),
            id_cliente=6,
            protocolo="PCNT2",
            tipo_analise="Contestação Externa",
            tipo_conclusao="Manual",
            matricula="c90001a",
            localidade_documento="SP",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 15),
            data_analise=date(2026, 7, 15),
            id_cliente=6,
            protocolo="PCNT1",
            tipo_analise="Contestação Interna",
            modulo="Contestação",
            tipo_falha="Manual",
            matricula="c90000a",
            localidade="Brasília",
            localidade_documento="DF",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 16),
            data_analise=date(2026, 7, 16),
            id_cliente=6,
            protocolo="PCNT2",
            tipo_analise="Contestação Externa",
            modulo="Contestação",
            tipo_falha="Manual",
            matricula="c90001a",
            localidade="São Carlos",
            localidade_documento="SP",
            source_file="test",
        )
        # Falha de auditoria (não deve entrar)
        QualidadeFalha.objects.create(
            data=date(2026, 7, 16),
            data_analise=date(2026, 7, 16),
            id_cliente=6,
            protocolo="PAUDX",
            tipo_analise="Auditoria Compliance",
            modulo="Auditoria",
            tipo_falha="Manual",
            matricula="c90000a",
            source_file="test",
        )

        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
            "date_axis": "auditoria",
        }
        metrics = build_contestacao_metrics(params)
        self.assertEqual(metrics["source"], "qualidade_auditado.tipo_analise+qualidade_falha")
        # Contestados = auditados com tipo Contestação* (não só falhas)
        self.assertEqual(metrics["protocolos_contestados"], 2)
        self.assertEqual(metrics["auditados_contestacao"], 2)
        self.assertEqual(metrics["eventos_contestacao"], 2)
        self.assertEqual(metrics["protocolos_com_falha_contestacao"], 2)
        # Procedentes = protocolos com falha em contestação
        self.assertEqual(metrics["procedentes"], 2)
        self.assertEqual(metrics["taxa_procedencia_pct"], 100.0)
        self.assertTrue(metrics["procedencia_disponivel"])
        self.assertEqual(metrics["date_field"], "data_recepcao_contestacao")

        by_mat = contestacao_by_matricula(params, ["c90000a", "c90001a"])
        self.assertEqual(by_mat["c90000a"], 1)
        self.assertEqual(by_mat["c90001a"], 1)

        scoped = build_contestacao_metrics({**params, "localidade": "SP"})
        self.assertEqual(scoped["eventos_contestacao"], 1)
        self.assertEqual(scoped["procedentes"], 1)

        dash = build_dashboard({**params, "module": "resumo", "dim": "id_cliente"})
        self.assertEqual(dash["kpis"]["protocolos_contestados"], 2)
        self.assertEqual(dash["kpis"]["contestacao"]["eventos_contestacao"], 2)
        self.assertEqual(dash["kpis"]["contestacao"]["auditados_contestacao"], 2)
        self.assertEqual(dash["kpis"]["procedentes"], 2)
        self.assertEqual(dash["kpis"]["taxa_procedencia_pct"], 100.0)
        points = dash["serie"]["points"]
        self.assertTrue(points)
        # Com falha em todos os contestados do período → improcedência 0% nos dias com contestação.
        contested_days = [p for p in points if (p.get("contestacoes") or 0) > 0]
        self.assertTrue(contested_days)
        for p in contested_days:
            self.assertIn("improcedencia_pct", p)
            self.assertEqual(p["improcedencia_pct"], 0.0)

    def test_client_dashboard_requires_cliente(self):
        from apps.qualidade_operacional.services.client_dashboard import (
            build_client_dashboard,
        )

        empty = build_client_dashboard(
            {"start_date": "2026-07-01", "end_date": "2026-07-31", "module": "cliente"}
        )
        self.assertTrue(empty["requires_cliente"])
        self.assertEqual(empty["module"], "cliente")

        payload = build_client_dashboard(
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "module": "cliente",
                "id_cliente": "6",
                "date_axis": "auditoria",
            }
        )
        self.assertFalse(payload["requires_cliente"])
        self.assertEqual(payload["identity"]["id_cliente"], 6)
        self.assertEqual(payload["health"]["status"], "em_definicao")
        self.assertIsNone(payload["health"]["score"])
        self.assertIn("kpi_cards", payload)
        self.assertIn("journey", payload)
        self.assertIn("workflows", payload)
        self.assertTrue(payload["workflows"]["ok"])
        self.assertEqual(
            payload["benchmark"].get("reference_label"), "Demais no filtro"
        )
        self.assertIsNone(payload["identity"].get("status_carteira"))
        self.assertEqual(payload["kpi_cards"]["eo"]["meta_pct"], 99.7)

        dash = build_dashboard(
            {
                "module": "cliente",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "id_cliente": "6",
            }
        )
        self.assertEqual(dash["module"], "cliente")

    def test_investigation_protocols_uses_max_weight_per_protocol(self):
        """Lista por protocolo: impacto = max dos pesos (não soma de linhas)."""
        from apps.qualidade_operacional.services.client_dashboard import (
            _investigation_protocols,
        )

        QualidadeFalha.objects.create(
            data=date(2026, 8, 1),
            data_analise=date(2026, 8, 1),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="G Auditoria",
            matricula="c90000a",
            protocolo="142685179",
            etapa="Sobreposição",
            categoria_falha="Crítica",
            nivel_dificuldade="Fácil",
            nivel_dificuldade_confer="Fácil",
            tipo_falha="Manual",
            lider="Lider Teste",
            localidade="Brasília",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 8, 1),
            data_analise=date(2026, 8, 1),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="G Auditoria",
            matricula="c90000a",
            protocolo="142685179",
            etapa="Análise Visual",
            categoria_falha="Crítica",
            nivel_dificuldade="Fácil",
            nivel_dificuldade_confer="Fácil",
            tipo_falha="Manual",
            lider="Lider Teste",
            localidade="Brasília",
            source_file="test",
        )
        rows = _investigation_protocols(
            QualidadeFalha.objects.filter(protocolo="142685179")
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["impacto_ponderado"], 3.5)

    def test_api_analytics_denied_without_perm(self):
        other = User.objects.create_user(
            username="no_eo", password="x", email="no_eo@example.com"
        )
        c = APIClient()
        c.force_authenticate(user=other)
        for path in ("dashboard", "kpis", "serie", "breakdown", "ranking", "insights"):
            res = c.get(f"/api/v1/qualidade/operacional/{path}/")
            self.assertEqual(res.status_code, 403, path)

@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class FacilitatorResponsibilityWindowTests(TestCase):
    def setUp(self):
        self.leader = Agent.objects.create(full_name="Lider", user_lan_id="lider")
        self.facilitator = Agent.objects.create(full_name="Facilitador", user_lan_id="fac")
        self.agent = Agent.objects.create(full_name="Operador", user_lan_id="op")
        self.fallback = {
            "matricula": "op",
            "agente": "Operador",
            "lider": "Lider",
            "lider_matricula": "lider",
            "time": "BRFLOW",
        }

    def _responsible(self, on_date):
        return responsibility_for_date(
            build_responsibility_index(), "op", on_date, self.fallback
        )

    def test_onboarding_brflow_includes_training_and_30_follow_up_days(self):
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="BRFLOW",
            job_activity="Integração",
            start_date=date(2026, 8, 1),
            active=True,
        )

        self.assertEqual(self._responsible(date(2026, 8, 1))["lider"], "Facilitador")
        self.assertEqual(self._responsible(date(2026, 9, 15))["lider"], "Facilitador")
        self.assertEqual(self._responsible(date(2026, 9, 16))["lider"], "Lider")

    def test_upgrade_brflow_covers_five_prior_days_and_thirty_follow_up_days(self):
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="BRFLOW",
            job_activity="Etapa anterior",
            start_date=date(2026, 7, 1),
            final_date=date(2026, 8, 19),
            active=False,
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="BRFLOW",
            job_activity="Nova etapa",
            start_date=date(2026, 8, 20),
            active=True,
        )

        self.assertEqual(self._responsible(date(2026, 8, 14))["lider"], "Lider")
        self.assertEqual(self._responsible(date(2026, 8, 15))["lider"], "Facilitador")
        self.assertEqual(self._responsible(date(2026, 9, 18))["lider"], "Facilitador")
        self.assertEqual(self._responsible(date(2026, 9, 19))["lider"], "Lider")

    def test_exit_from_reboarding_does_not_create_a_second_upgrade_window(self):
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="Operacional/Fraud",
            job_activity="Reintegração",
            start_date=date(2026, 6, 17),
            final_date=date(2026, 6, 25),
            active=False,
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="Operacional/Fraud",
            job_activity="Sobreposição",
            start_date=date(2026, 6, 26),
            active=True,
        )

        windows = build_responsibility_index()["op"]
        self.assertEqual([window["kind"] for window in windows], ["reboarding"])
        responsible = self._responsible(date(2026, 7, 15))
        self.assertEqual(responsible["lider"], "Facilitador")
        self.assertEqual(responsible["regra_responsabilidade"], "reboarding")

    def test_ranking_keeps_leader_and_facilitator_tables_separate(self):
        """Líder não lista facilitador; facilitador fica só no ranking dedicado."""
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="BRFLOW",
            job_activity="Integração",
            start_date=date(2026, 8, 1),
            active=True,
        )
        # Dentro da janela do facilitador
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="op",
            protocolo="PFAC",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        # Fora da janela (após 46 dias a partir de 01/08 → 15/09)
        QualidadeAuditado.objects.create(
            data=date(2026, 9, 20),
            data_analise=date(2026, 9, 20),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="op",
            protocolo="PLID",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        cache.clear()
        params = {
            "start_date": "2026-08-01",
            "end_date": "2026-09-30",
            "workforce_only": "1",
            "by": "agente",
        }
        leader_ranking = build_ranking({**params, "responsibility_scope": "lider"})
        fac_ranking = build_ranking({**params, "responsibility_scope": "facilitador"})

        leader_rows = leader_ranking["results"]
        self.assertEqual(len(leader_rows), 1)
        self.assertEqual(leader_rows[0]["lider"], "Lider")
        self.assertEqual(leader_rows[0].get("responsabilidade", "lider"), "lider")
        self.assertEqual(leader_rows[0]["auditados"], 1)
        self.assertNotIn(
            "Facilitador",
            {row["lider"] for row in leader_rows},
        )

        fac_rows = fac_ranking["results"]
        self.assertEqual(len(fac_rows), 1)
        self.assertEqual(fac_rows[0]["lider"], "Facilitador")
        self.assertEqual(fac_rows[0]["responsabilidade"], "facilitador")
        self.assertEqual(fac_rows[0]["auditados"], 1)

        dash = build_dashboard({**params, "module": "agentes"})
        self.assertNotIn(
            "Facilitador",
            {row["lider"] for row in dash["ranking"]["results"]},
        )
        self.assertIn(
            "Facilitador",
            {row["lider"] for row in dash["facilitator_ranking"]["results"]},
        )
        self.assertGreater(len(dash["facilitator_hierarchy"]["results"]), 0)
        self.assertEqual(
            sum(row["auditados"] for row in dash["facilitator_hierarchy"]["results"]),
            dash["facilitator_ranking"]["results"][0]["auditados"],
        )

    def test_facilitator_row_exposes_responsibility_periods(self):
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="BRFLOW",
            job_activity="Integração",
            start_date=date(2026, 8, 1),
            active=True,
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="op",
            protocolo="PFAC",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        cache.clear()
        fac_rows = build_ranking(
            {
                "start_date": "2026-08-01",
                "end_date": "2026-09-30",
                "workforce_only": "1",
                "by": "agente",
                "responsibility_scope": "facilitador",
            }
        )["results"]
        self.assertEqual(len(fac_rows), 1)
        periods = fac_rows[0].get("responsibility_periods") or []
        self.assertTrue(periods)
        self.assertEqual(periods[0]["kind"], "onboarding")
        self.assertEqual(periods[0]["start_date"], "2026-08-01")
        self.assertEqual(periods[0]["end_date"], "2026-09-15")
        self.assertEqual(fac_rows[0].get("facilitador_matricula"), "fac")

    def test_agent_under_two_facilitators_splits_metrics(self):
        fac_b = Agent.objects.create(full_name="Facilitador B", user_lan_id="fac_b")
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="BRFLOW",
            job_activity="Integração",
            start_date=date(2026, 4, 1),
            final_date=date(2026, 5, 16),
            active=False,
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=fac_b,
            team="BRFLOW",
            job_activity="Reintegração",
            start_date=date(2026, 6, 1),
            active=True,
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 4, 10),
            data_analise=date(2026, 4, 10),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="op",
            protocolo="P1",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 6, 10),
            data_analise=date(2026, 6, 10),
            id_cliente=6,
            id_workflow=144,
            tipo_analise="Auditoria Compliance",
            matricula="op",
            protocolo="P2",
            etapa="Etapa A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        cache.clear()
        fac_rows = build_ranking(
            {
                "start_date": "2026-04-01",
                "end_date": "2026-07-31",
                "workforce_only": "1",
                "by": "agente",
                "responsibility_scope": "facilitador",
            }
        )["results"]
        by_fac = {row["facilitador_matricula"]: row for row in fac_rows}
        self.assertEqual(set(by_fac), {"fac", "fac_b"})
        self.assertEqual(by_fac["fac"]["auditados"], 1)
        self.assertEqual(by_fac["fac_b"]["auditados"], 1)
        self.assertNotEqual(by_fac["fac"]["key"], by_fac["fac_b"]["key"])


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class AgentesOperationalScopeTests(TestCase):
    """Paridade tabela Agentes × hierarquia de líder (Manual + vínculo HC)."""

    def setUp(self):
        cache.clear()
        clear_responsibility_index_cache()
        self.leader = Agent.objects.create(full_name="Lider", user_lan_id="lider_op")
        self.agent = Agent.objects.create(full_name="Operador", user_lan_id="op_scope")
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Operacao Teste",
            start_date=date(2026, 7, 1),
            active=True,
        )

    def test_operational_scope_excludes_automatic_and_processual(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_scope",
            protocolo="P-MAN",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 11),
            data_analise=date(2026, 7, 11),
            id_cliente=6,
            id_workflow=144,
            matricula="op_scope",
            protocolo="P-AUTO",
            tipo_conclusao="Automático",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_scope",
            protocolo="F-MAN",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 11),
            data_analise=date(2026, 7, 11),
            id_cliente=6,
            id_workflow=144,
            matricula="op_scope",
            protocolo="F-PROC",
            tipo_falha="Processual",
            source_file="test",
        )
        from apps.qualidade_operacional.services.analytics import (
            compute_operational_detail_scope,
            diagnose_leader_hierarchy_scope,
            filtered_auditados,
            filtered_falhas,
            operational_agentes_scope_params,
        )
        from apps.qualidade_operacional.services.dashboard import build_dashboard

        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "by": "agente",
        }
        scoped = operational_agentes_scope_params(params)
        self.assertEqual(filtered_auditados(scoped).count(), 1)
        self.assertEqual(filtered_falhas(scoped).count(), 1)
        detail = compute_operational_detail_scope(params)
        self.assertEqual(detail["auditados"], 1)
        self.assertEqual(detail["falhas"], 1)

        dash = build_dashboard({**params, "module": "agentes"})
        self.assertEqual(dash["operational_detail_scope"]["auditados"], 1)
        self.assertEqual(dash["operational_detail_scope"]["falhas"], 1)
        self.assertEqual(dash["kpis"]["auditados"], 1)
        self.assertEqual(dash["kpis"]["falhas"], 1)

        diagnosis = diagnose_leader_hierarchy_scope(params)
        self.assertEqual(diagnosis["detail_scope"]["auditados"], 1)
        self.assertEqual(diagnosis["detail_vs_hierarchy_auditados_delta"], 0)

    def test_segment_start_end_narrows_detail_list(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 5),
            data_analise=date(2026, 7, 5),
            id_cliente=6,
            id_workflow=144,
            matricula="op_scope",
            protocolo="P-OLD",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 20),
            data_analise=date(2026, 7, 20),
            id_cliente=6,
            id_workflow=144,
            matricula="op_scope",
            protocolo="P-NEW",
            tipo_conclusao="Manual",
            source_file="test",
        )
        from apps.qualidade_operacional.services.analytics import (
            filtered_auditados,
            operational_agentes_scope_params,
        )

        scoped = operational_agentes_scope_params(
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "segment_start": "2026-07-15",
                "segment_end": "2026-07-31",
            }
        )
        self.assertEqual(filtered_auditados(scoped).count(), 1)
        self.assertTrue(
            filtered_auditados(scoped).filter(protocolo="P-NEW").exists()
        )


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
)
class FacilitatorOperationalScopeTests(TestCase):
    """Paridade tabela detalhe facilitador × escopo Manual + janelas HC."""

    def setUp(self):
        cache.clear()
        clear_responsibility_index_cache()
        self.leader = Agent.objects.create(full_name="Lider", user_lan_id="lider_fac")
        self.facilitator = Agent.objects.create(full_name="Facilitador", user_lan_id="fac")
        self.agent = Agent.objects.create(full_name="Operador", user_lan_id="op_fac")
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            facilitator=self.facilitator,
            team="BRFLOW",
            job_activity="Integração",
            start_date=date(2026, 8, 1),
            active=True,
        )

    def test_facilitator_operational_scope_manual_only(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac",
            protocolo="P-MAN",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 11),
            data_analise=date(2026, 8, 11),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac",
            protocolo="P-AUTO",
            tipo_conclusao="Automático",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac",
            protocolo="F-MAN",
            tipo_falha="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 8, 11),
            data_analise=date(2026, 8, 11),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac",
            protocolo="F-PROC",
            tipo_falha="Processual",
            source_file="test",
        )
        from apps.qualidade_operacional.services.analytics import (
            compute_operational_facilitator_detail_scope,
            filtered_auditados,
            filtered_falhas,
            operational_facilitator_scope_params,
        )

        params = {
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "by": "agente",
        }
        scoped = operational_facilitator_scope_params(params)
        self.assertEqual(filtered_auditados(scoped).count(), 1)
        self.assertEqual(filtered_falhas(scoped).count(), 1)
        detail = compute_operational_facilitator_detail_scope(params)
        self.assertEqual(detail["auditados"], 1)
        self.assertEqual(detail["falhas"], 1)

    def test_facilitator_detail_scope_in_dashboard(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac",
            protocolo="P-FAC",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac",
            protocolo="F-FAC",
            tipo_falha="Manual",
            source_file="test",
        )
        params = {
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "by": "agente",
        }
        dash = build_dashboard({**params, "module": "agentes"})
        self.assertEqual(dash["facilitator_detail_scope"]["auditados"], 1)
        self.assertEqual(dash["facilitator_detail_scope"]["falhas"], 1)

    def test_facilitador_matricula_narrows_detail_list(self):
        fac_b = Agent.objects.create(full_name="Facilitador B", user_lan_id="fac_b")
        agent_b = Agent.objects.create(full_name="Operador B", user_lan_id="op_fac_b")
        AgentHistory.objects.create(
            agent=agent_b,
            leader=self.leader,
            facilitator=fac_b,
            team="BRFLOW",
            job_activity="Integração",
            start_date=date(2026, 8, 1),
            active=True,
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac",
            protocolo="P-A",
            tipo_conclusao="Manual",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 10),
            id_cliente=6,
            id_workflow=144,
            matricula="op_fac_b",
            protocolo="P-B",
            tipo_conclusao="Manual",
            source_file="test",
        )
        from apps.qualidade_operacional.services.analytics import (
            filtered_auditados,
            operational_facilitator_scope_params,
        )

        scoped = operational_facilitator_scope_params(
            {
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
                "facilitador_matricula": "fac",
            }
        )
        self.assertEqual(filtered_auditados(scoped).count(), 1)
        self.assertTrue(filtered_auditados(scoped).filter(protocolo="P-A").exists())
