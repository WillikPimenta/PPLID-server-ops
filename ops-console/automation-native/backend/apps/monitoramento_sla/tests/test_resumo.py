# -*- coding: utf-8 -*-
from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import DimCliente, DimNivelHierarquico, DimWorkflow, ProjecaoSla
from apps.monitoramento_sla.models import SlaUtilConsolidado
from apps.monitoramento_sla.services.resumo import (
    _fiscal_quarter_bounds_from_date,
    build_resumo,
)
from apps.monitoramento_sla.services.resumo_cache import bump_resumo_cache_version
from apps.monitoramento_sla.services.resumo_serve import (
    reset_resumo_gate_for_tests,
    resolve_gated_payload,
)

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class ResumoApiTests(TestCase):
    def setUp(self):
        reset_resumo_gate_for_tests()
        bump_resumo_cache_version()
        self.user = User.objects.create_user(
            username="sla_resumo", password="x", email="sla_resumo@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        DimCliente.objects.create(id_cliente=1, nome="Cli A")
        DimWorkflow.objects.create(id_workflow=10, nome="WF A")
        DimNivelHierarquico.objects.create(id_nh=100, nome="NH A")

        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 7, 1),
            hora_cadastro=time(9, 0),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            tipo_conclusao="Automático",
            sla_descricao_natural="Dentro",
            sla_descricao_ajustado="Dentro",
            quantidade=80,
            date_key_cadastro=20260701,
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 7, 1),
            hora_cadastro=time(10, 0),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            tipo_conclusao="Manual",
            sla_descricao_natural="Fora",
            sla_descricao_ajustado="Dentro",
            quantidade=20,
            date_key_cadastro=20260701,
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 6, 15),
            hora_cadastro=time(14, 0),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            tipo_conclusao="Automático",
            sla_descricao_natural="Dentro",
            sla_descricao_ajustado="Dentro",
            quantidade=50,
            date_key_cadastro=20260615,
        )

        ProjecaoSla.objects.create(
            cliente_id=1,
            workflow_id=10,
            nivel_hierarquico_id=100,
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            dias_semana="{0..6}",
            volume=10.0,
            sla_segundos=3600,
            sla_ajuste=7200,
            flag_ajuste_sla=0.1,
        )

    def test_resumo_requires_period(self):
        res = self.client.get("/api/v1/monitoramento-sla/resumo/")
        self.assertEqual(res.status_code, 400)

    def test_resumo_year_ok(self):
        res = self.client.get("/api/v1/monitoramento-sla/resumo/", {"year": 2026, "month": 7})
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertTrue(data["ok"])
        k = data["kpis"]
        self.assertEqual(k["total"], 150)
        self.assertEqual(k["dentro"], 130)
        self.assertEqual(k["fora"], 20)
        self.assertEqual(k["sla_pct"], 86.7)
        self.assertEqual(k["sla_ajustado_pct"], 100.0)
        self.assertEqual(k["workflows_atendimento"], 1)
        self.assertEqual(k["workflows_impactados"], 1)
        labels = {t["label"]: t["quantidade"] for t in k["tipo_conclusao"]}
        self.assertEqual(labels.get("Automático"), 130)
        self.assertEqual(labels.get("Manual"), 20)
        self.assertTrue(any(c["date"] == "2026-07-01" for c in data["calendario"]))
        self.assertTrue(any(s.get("key") or s.get("ym") for s in data["serie_temporal"]))
        self.assertIn("meta_sla", data["kpis"])
        self.assertIn("volume_atingimento_pct", data["kpis"])
        self.assertTrue(data["heatmap"])

    def test_build_resumo_volume_esperado(self):
        # Jul 1 only: 1 day × volume 10
        out = build_resumo(
            start=date(2026, 7, 1),
            end=date(2026, 7, 1),
            calendar_month=7,
        )
        self.assertEqual(out["kpis"]["volume"], 100)
        self.assertEqual(out["kpis"]["volume_esperado"], 10)

    def test_deny_without_perm(self):
        other = User.objects.create_user(
            username="no_resumo", password="x", email="no_resumo@example.com"
        )
        c = APIClient()
        c.force_authenticate(user=other)
        res = c.get("/api/v1/monitoramento-sla/resumo/", {"year": 2026})
        self.assertEqual(res.status_code, 403)

    def test_queue_timeout_returns_503(self):
        import threading

        reset_resumo_gate_for_tests()
        started = threading.Event()
        release = threading.Event()

        def slow_build():
            started.set()
            release.wait(timeout=5)
            return {"ok": True}

        results: list[tuple] = []

        def runner(idx: int):
            results.append(
                resolve_gated_payload(
                    route="resumo-gate-test",
                    params={"k": str(idx)},
                    builder=slow_build,
                    use_cache=False,
                )
            )

        with override_settings(MONITORAMENTO_SLA_QUEUE_WAIT_MS=200):
            reset_resumo_gate_for_tests()
            t1 = threading.Thread(target=runner, args=(1,))
            t2 = threading.Thread(target=runner, args=(2,))
            t1.start()
            self.assertTrue(started.wait(timeout=2))
            t2.start()
            t2.join(timeout=3)
            release.set()
            t1.join(timeout=3)

        codes = [r[1] for r in results]
        self.assertIn("queue_timeout", codes)


class FiscalQuarterTests(TestCase):
    def test_bounds_april_start(self):
        # Q1 FY2026 = Abr–Jun 2026
        s, e, key, label = _fiscal_quarter_bounds_from_date(date(2026, 4, 10))
        self.assertEqual(s, date(2026, 4, 1))
        self.assertEqual(e, date(2026, 6, 30))
        self.assertEqual(key, "2026-FQ1")
        self.assertEqual(label, "Q1 2026")

        # Q2 FY2026 = Jul–Set 2026 (julho não é Q3 calendário)
        s, e, key, label = _fiscal_quarter_bounds_from_date(date(2026, 7, 15))
        self.assertEqual(s, date(2026, 7, 1))
        self.assertEqual(e, date(2026, 9, 30))
        self.assertEqual(key, "2026-FQ2")
        self.assertEqual(label, "Q2 2026")

        # Q4 FY2026 = Jan–Mar 2027
        s, e, key, label = _fiscal_quarter_bounds_from_date(date(2027, 1, 5))
        self.assertEqual(s, date(2027, 1, 1))
        self.assertEqual(e, date(2027, 3, 31))
        self.assertEqual(key, "2026-FQ4")
        self.assertEqual(label, "Q4 2026")

    def test_serie_quarter_groups_fiscal(self):
        DimCliente.objects.create(id_cliente=2, nome="Cli B")
        DimWorkflow.objects.create(id_workflow=20, nome="WF B")
        DimNivelHierarquico.objects.create(id_nh=200, nome="NH B")
        for d, qtd in [
            (date(2026, 7, 1), 10),
            (date(2026, 8, 1), 20),
            (date(2026, 4, 1), 5),
        ]:
            SlaUtilConsolidado.objects.create(
                data_cadastro=d,
                hora_cadastro=time(9, 0),
                id_cliente=2,
                id_workflow=20,
                id_nh=200,
                tipo_conclusao="Automático",
                sla_descricao_natural="Dentro",
                sla_descricao_ajustado="Dentro",
                quantidade=qtd,
                date_key_cadastro=int(d.strftime("%Y%m%d")),
            )
        out = build_resumo(
            start=date(2026, 4, 1),
            end=date(2026, 9, 30),
            id_cliente=2,
            granularity="quarter",
        )
        keys = [s["key"] for s in out["serie_temporal"]]
        self.assertEqual(keys, ["2026-FQ1", "2026-FQ2"])
        by_key = {s["key"]: s for s in out["serie_temporal"]}
        self.assertEqual(by_key["2026-FQ1"]["recebidos"], 5)
        self.assertEqual(by_key["2026-FQ2"]["recebidos"], 30)
        self.assertEqual(by_key["2026-FQ2"]["label"], "Q2 2026")
