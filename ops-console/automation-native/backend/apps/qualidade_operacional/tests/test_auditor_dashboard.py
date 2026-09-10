from datetime import date

from django.core.cache import cache
from django.test import TestCase

from apps.qualidade_operacional.models import QualidadeAuditado
from apps.qualidade_operacional.services.dashboard import build_dashboard


class AuditorDashboardTests(TestCase):
    def setUp(self):
        cache.clear()

    def _audit(
        self,
        *,
        status: str,
        auditor: str = "",
        agente: str = "agente01",
        protocolo: str,
    ) -> None:
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 10),
            data_analise=date(2026, 8, 11),
            id_cliente=6,
            id_workflow=144,
            matricula=agente,
            matricula_auditor=auditor,
            protocolo=protocolo,
            status=status,
            tipo_analise="Auditoria Compliance",
            etapa="Conferência",
            source_file="test",
        )

    def _params(self, **extra):
        return {
            "module": "auditores",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            **extra,
        }

    def test_only_withdrawn_failures_penalize_auditor(self):
        self._audit(status="retirada", auditor="AUD01", protocolo="RET-1")
        self._audit(status="mantida", auditor="AUD01", protocolo="MAN-1")
        self._audit(status="ativa", auditor="AUD01", protocolo="ATI-1")

        dashboard = build_dashboard(self._params())

        self.assertEqual(dashboard["module"], "auditores")
        self.assertEqual(dashboard["kpis"]["erros_auditoria"], 1)
        self.assertEqual(dashboard["ranking"]["results"][0]["matricula_auditor"], "aud01")
        self.assertEqual(dashboard["ranking"]["results"][0]["erros"], 1)

    def test_groups_identified_and_unidentified_auditors(self):
        self._audit(status="retirada", auditor="AUD01", protocolo="RET-1")
        self._audit(status="retirada", auditor="aud01", protocolo="RET-2")
        self._audit(status="retirada", auditor="", protocolo="RET-3")
        self._audit(status="retirada", auditor="   ", protocolo="RET-4")

        dashboard = build_dashboard(self._params())
        rows = {row["key"]: row for row in dashboard["ranking"]["results"]}

        self.assertEqual(rows["aud01"]["erros"], 2)
        self.assertEqual(rows["__nao_identificado__"]["erros"], 2)
        self.assertFalse(rows["__nao_identificado__"]["identificado"])
        self.assertEqual(dashboard["kpis"]["auditores_identificados"], 1)
        self.assertEqual(dashboard["kpis"]["erros_nao_identificados"], 2)

    def test_agent_and_auditor_filters_keep_their_own_semantics(self):
        self._audit(
            status="retirada", auditor="aud01", agente="agente01", protocolo="RET-1"
        )
        self._audit(
            status="retirada", auditor="aud02", agente="agente02", protocolo="RET-2"
        )

        dashboard = build_dashboard(self._params(matricula="agente02"))

        self.assertEqual(dashboard["kpis"]["erros_auditoria"], 1)
        self.assertEqual(dashboard["ranking"]["results"][0]["key"], "aud02")

        cache.clear()
        dashboard = build_dashboard(self._params(matricula_auditor="aud01"))
        self.assertEqual(dashboard["kpis"]["erros_auditoria"], 1)
        self.assertEqual(dashboard["ranking"]["results"][0]["key"], "aud01")

    def test_cold_build_is_one_query_and_warm_build_is_cached(self):
        self._audit(status="retirada", auditor="aud01", protocolo="RET-1")
        params = self._params()

        with self.assertNumQueries(1):
            first = build_dashboard(params)
        with self.assertNumQueries(0):
            second = build_dashboard(params)

        self.assertEqual(second, first)
