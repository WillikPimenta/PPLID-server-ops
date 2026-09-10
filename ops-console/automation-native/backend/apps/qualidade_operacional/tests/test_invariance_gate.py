# -*- coding: utf-8 -*-
"""Gate de invariância funcional para otimizações do Indicador de Qualidade.

Os oráculos deste arquivo são calculados a partir das populações filtradas e
das chaves dos fatos, não a partir do payload que está sendo validado. Isso
evita aceitar uma troca silenciosa de registros só porque o total permaneceu
igual.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import force_authenticate

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.analytics import (
    _count_qs,
    build_serie,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.contestacao_metrics import (
    _contestacao_auditados_qs,
    _contestacao_falhas_qs,
)
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.performance_cache import (
    bump_quality_cache_version,
)
from apps.qualidade_operacional.views import AuditadosView, FalhasView
from apps.workforce.models import Agent, AgentHistory


@override_settings(
    QUALIDADE_SOURCE_MODE="legacy",
    QUALIDADE_INTRANET_SOURCE_ENABLED=False,
    QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=False,
)
class QualidadeInvarianceGateTests(TestCase):
    maxDiff = None

    def setUp(self):
        cache.clear()
        bump_quality_cache_version()
        self.user = get_user_model().objects.create_superuser(
            username="qo-invariance-gate",
            email="qo-invariance@example.test",
            password="unused",
        )
        leader = Agent.objects.create(
            full_name="Líder Gate", user_lan_id="leader_gate", active=True
        )
        operator = Agent.objects.create(
            full_name="Operador Gate", user_lan_id="operator_gate", active=True
        )
        AgentHistory.objects.create(
            agent=operator,
            leader=leader,
            team="Operação Gate",
            start_date=date(2026, 1, 1),
            active=True,
        )

        self._audit("P-1", date(2026, 7, 10), "Contestação Externa", "Etapa A")
        self._audit("P-1", date(2026, 7, 11), "Contestação Externa", "Etapa B")
        self._audit("P-2", date(2026, 6, 10), "Análise Direcionada", "Etapa A")
        self._audit(
            "P-3",
            date(2026, 7, 15),
            "Auditoria Compliance",
            "Etapa A",
            status="retirada",
            matricula_auditor="auditor_gate",
        )
        self._audit("P-4", date(2026, 7, 20), "Contestação Compliance", "Etapa C")

        self._failure("P-1", date(2026, 7, 10), "Contestação Externa", "Etapa A", "case-p1-a")
        self._failure("P-1", date(2026, 7, 11), "Contestação Externa", "Etapa B", "case-p1-b")
        self._failure("P-2", date(2026, 6, 10), "Análise Direcionada", "Etapa A", "case-p2")
        self._failure(
            "P-3",
            date(2026, 7, 15),
            "Auditoria Compliance",
            "Etapa A",
            "case-p3-deadline",
            analysis_date=date(2026, 7, 15) - timedelta(days=120),
        )
        self._failure("P-4", date(2026, 7, 20), "Contestação Compliance", "Etapa C", "case-p4")

        self.base = {
            "start_date": "2026-06-01",
            "end_date": "2026-07-31",
            "date_axis": "auditoria",
            "dim": "id_cliente",
            "metric": "quantidade",
        }

    @staticmethod
    def _audit(
        protocolo,
        event_date,
        tipo_analise,
        etapa,
        *,
        status="ativa",
        matricula_auditor="",
    ):
        return QualidadeAuditado.objects.create(
            data=event_date,
            data_analise=event_date,
            data_recepcao_contestacao=event_date,
            id_cliente=6,
            id_workflow=144,
            protocolo=protocolo,
            tipo_analise=tipo_analise,
            tipo_conclusao="Manual",
            etapa=etapa,
            matricula="operator_gate",
            matricula_auditor=matricula_auditor,
            status=status,
            source_file="invariance-gate",
        )

    @staticmethod
    def _failure(
        protocolo,
        event_date,
        tipo_analise,
        etapa,
        case_key,
        *,
        analysis_date=None,
    ):
        return QualidadeFalha.objects.create(
            data=event_date,
            data_analise=analysis_date or event_date,
            data_recepcao_contestacao=event_date,
            id_cliente=6,
            id_workflow=144,
            protocolo=protocolo,
            case_key=case_key,
            tipo_analise=tipo_analise,
            tipo_falha="Manual",
            categoria_falha="Não Crítica",
            etapa=etapa,
            matricula="operator_gate",
            source_file="invariance-gate",
        )

    @staticmethod
    def _identity(qs, *, kind):
        fields = ("id", "protocolo") if kind == "auditados" else ("id", "protocolo", "case_key")
        return sorted(tuple(row[field] for field in fields) for row in qs.values(*fields))

    def _list_payload(self, kind, params):
        request = RequestFactory().get(
            f"/api/v1/qualidade/operacional/{kind}/",
            {**params, "page": 1, "page_size": 200},
        )
        force_authenticate(request, user=self.user)
        view = AuditadosView if kind == "auditados" else FalhasView
        response = view.as_view()(request)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_resumo_matrix_matches_population_oracles_and_warm_payload(self):
        """Etapa/protocolo, complete/official e prazo on/off não podem trocar fatos."""
        complete = {**self.base, "grain": "etapa", "metric_mode": "complete"}
        official = {**complete, "metric_mode": "official"}
        self.assertEqual(filtered_auditados(complete).count(), 5)
        self.assertEqual(filtered_auditados(official).count(), 4)
        self.assertEqual(filtered_falhas(complete).count(), 5)
        self.assertEqual(filtered_falhas(official).count(), 4)
        self.assertEqual(
            filtered_falhas({**complete, "exclude_out_of_deadline": "true"}).count(),
            4,
        )
        self.assertEqual(_count_qs(filtered_auditados(complete), "protocolo"), 4)

        for grain in ("etapa", "protocolo"):
            for metric_mode in ("complete", "official"):
                for deadline in ("false", "true"):
                    params = {
                        **self.base,
                        "module": "resumo",
                        "grain": grain,
                        "metric_mode": metric_mode,
                        "exclude_out_of_deadline": deadline,
                    }
                    with self.subTest(
                        grain=grain, metric_mode=metric_mode, deadline=deadline
                    ):
                        aud_qs = filtered_auditados(params)
                        fal_qs = filtered_falhas(params)
                        expected_aud = _count_qs(aud_qs, grain)
                        expected_fal = _count_qs(fal_qs, grain)
                        aud_identity = self._identity(aud_qs, kind="auditados")
                        fal_identity = self._identity(fal_qs, kind="falhas")

                        cache.clear()
                        bump_quality_cache_version()
                        cold = build_dashboard(params)
                        warm = build_dashboard(params)

                        self.assertEqual(warm, cold)
                        self.assertEqual(cold["kpis"]["auditados"], expected_aud)
                        self.assertEqual(cold["kpis"]["falhas"], expected_fal)
                        self.assertEqual(
                            self._identity(filtered_auditados(params), kind="auditados"),
                            aud_identity,
                        )
                        self.assertEqual(
                            self._identity(filtered_falhas(params), kind="falhas"),
                            fal_identity,
                        )

    def test_modules_keep_their_declared_populations(self):
        params = {**self.base, "grain": "etapa", "metric_mode": "complete"}

        resumo = build_dashboard({**params, "module": "resumo"})
        contestacao = build_dashboard({**params, "module": "contestacao"})
        cliente = build_dashboard({**params, "module": "cliente", "id_cliente": "6"})
        agentes = build_dashboard({**params, "module": "agentes"})
        auditores = build_dashboard({**params, "module": "auditores"})

        self.assertEqual(resumo["kpis"]["auditados"], filtered_auditados(params).count())
        self.assertEqual(resumo["kpis"]["falhas"], filtered_falhas(params).count())
        self.assertEqual(contestacao["kpis"]["auditados"], _contestacao_auditados_qs(params).count())
        self.assertEqual(contestacao["kpis"]["falhas"], _contestacao_falhas_qs(params).count())
        self.assertEqual(
            contestacao["kpis"]["contestacao"]["eventos_contestacao"],
            contestacao["kpis"]["falhas"],
        )
        self.assertEqual(
            resumo["kpis"]["contestacao"]["eventos_contestacao"],
            contestacao["kpis"]["falhas"],
        )
        self.assertLessEqual(
            resumo["kpis"]["contestacao"]["eventos_contestacao"],
            resumo["kpis"]["falhas"],
        )
        contestacao_metrics = resumo["kpis"]["contestacao"]
        self.assertEqual(
            contestacao_metrics["auditados_contestacao"],
            contestacao_metrics["procedentes"] + contestacao_metrics["improcedentes"],
        )
        self.assertEqual(
            contestacao_metrics["procedentes"],
            contestacao_metrics["eventos_contestacao"],
        )
        self.assertLessEqual(contestacao_metrics["taxa_procedencia_pct"], 100.0)
        self.assertGreater(contestacao["kpis"]["auditados"], 0)
        self.assertGreater(contestacao["kpis"]["falhas"], 0)
        self.assertEqual(cliente["kpis"]["auditados"], filtered_auditados({**params, "id_cliente": "6"}).count())
        self.assertEqual(cliente["kpis"]["falhas"], filtered_falhas({**params, "id_cliente": "6"}).count())

        attributed = sum(
            int(row.get("auditados") or 0)
            for row in agentes["leader_hierarchy"]["results"]
            if row.get("key") != "__leader_temporal_unattributed__"
        )
        attributed_failures = sum(
            int(row.get("falhas") or 0)
            for row in agentes["leader_hierarchy"]["results"]
            if row.get("key") != "__leader_temporal_unattributed__"
        )
        self.assertGreater(attributed, 0)
        self.assertGreater(attributed_failures, 0)
        self.assertEqual(agentes["kpis"]["auditados"], attributed)
        self.assertEqual(agentes["kpis"]["falhas"], attributed_failures)

        withdrawn = QualidadeAuditado.objects.filter(
            status="retirada", data__range=(date(2026, 6, 1), date(2026, 7, 31))
        )
        self.assertEqual(auditores["kpis"]["erros_auditoria"], withdrawn.count())

        for module, payload in (
            ("resumo", resumo),
            ("contestacao", contestacao),
            ("cliente", cliente),
            ("agentes", agentes),
            ("auditores", auditores),
        ):
            with self.subTest(module=module):
                self.assertEqual(build_dashboard({**params, "module": module, **({"id_cliente": "6"} if module == "cliente" else {})}), payload)

    def test_series_lists_ids_and_case_keys_close_with_filtered_facts(self):
        params = {
            **self.base,
            "grain": "etapa",
            "metric_mode": "complete",
            "exclude_out_of_deadline": "true",
        }
        aud_qs = filtered_auditados(params)
        fal_qs = filtered_falhas(params)
        serie = build_serie(params)

        self.assertEqual(sum(int(point["auditados"]) for point in serie["points"]), aud_qs.count())
        self.assertEqual(sum(int(point["falhas"]) for point in serie["points"]), fal_qs.count())

        aud_list = self._list_payload("auditados", params)
        fal_list = self._list_payload("falhas", params)
        self.assertEqual(aud_list["count"], aud_qs.count())
        self.assertEqual(fal_list["count"], fal_qs.count())
        self.assertEqual(
            sorted(row["id"] for row in aud_list["results"]),
            sorted(aud_qs.values_list("id", flat=True)),
        )
        # A API de detalhe não expõe case_key hoje. Protegemos a identidade da
        # lista pelos IDs e a identidade de caso diretamente no fato canônico.
        self.assertEqual(
            sorted(row["id"] for row in fal_list["results"]),
            sorted(fal_qs.values_list("id", flat=True)),
        )
        expected_case_keys = sorted(fal_qs.values_list("id", "case_key"))
        self.assertTrue(all(case_key for _row_id, case_key in expected_case_keys))
        self.assertEqual(
            sorted(filtered_falhas(params).values_list("id", "case_key")),
            expected_case_keys,
        )
