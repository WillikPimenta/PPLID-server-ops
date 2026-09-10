from datetime import date, time
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.dimensoes_processos.services.capacity_hourly import (
    build_hourly_capacity,
    build_workflow_hourly_profiles,
    default_profile_window,
)
from apps.dimensoes_processos.models import CapacityHourlyProfileSnapshot
from apps.monitoramento_sla.models import SlaUtilConsolidado


class DefaultProfileWindowTests(TestCase):
    def test_last_30_days_inclusive(self):
        start, end = default_profile_window(date(2026, 8, 20), days=30)
        self.assertEqual(end, date(2026, 8, 20))
        self.assertEqual(start, date(2026, 7, 22))


class BuildWorkflowHourlyProfilesTests(TestCase):
    def setUp(self):
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 8, 1),
            hora_cadastro=time(6, 0),
            id_cliente=1,
            id_workflow=10,
            quantidade=60,
            date_key_cadastro=20260801,
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 8, 2),
            hora_cadastro=time(13, 0),
            id_cliente=1,
            id_workflow=10,
            quantidade=40,
            date_key_cadastro=20260802,
        )

    def test_profile_pct_sums_to_100(self):
        profiles, missing, quality = build_workflow_hourly_profiles(
            {(1, 10)},
            on_date=date(2026, 8, 20),
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 31),
        )
        self.assertEqual(missing, [])
        profile = profiles[(1, 10)]
        self.assertEqual(len(profile), 24)
        total_pct = sum(Decimal(row["share_pct"]) for row in profile)
        self.assertAlmostEqual(float(total_pct), 100.0, places=1)
        self.assertEqual(profile[6]["volume"], 60)
        self.assertEqual(profile[13]["volume"], 40)
        self.assertEqual(quality["source_counts"], {"recent_workflow": 1})

    def test_uniform_fallback_when_no_history(self):
        profiles, missing, quality = build_workflow_hourly_profiles(
            {(2, 99)},
            on_date=date(2026, 8, 20),
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 31),
        )
        self.assertEqual(len(missing), 1)
        profile = profiles[(2, 99)]
        self.assertEqual(profile[0]["share_pct"], "4.17")
        self.assertEqual(quality["source_counts"], {"uniform_24h": 1})

    def test_uses_materialized_quarterly_workflow_fallback(self):
        CapacityHourlyProfileSnapshot.objects.create(
            quarter_from=date(2026, 4, 1),
            quarter_to=date(2026, 6, 30),
            weekday=date(2026, 8, 20).weekday(),
            scope=CapacityHourlyProfileSnapshot.SCOPE_WORKFLOW,
            id_cliente=2,
            id_workflow=99,
            hourly_volumes=[0] * 8 + [75] + [0] * 15,
            trusted_volume=75,
            generated_at=timezone.now(),
        )
        profiles, missing, quality = build_workflow_hourly_profiles(
            {(2, 99)},
            on_date=date(2026, 8, 20),
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 20),
        )
        self.assertEqual(missing, [])
        self.assertEqual(profiles[(2, 99)][8]["share_pct"], "100.00")
        self.assertEqual(
            quality["source_counts"],
            {"quarterly_weekday_workflow": 1},
        )

    def test_unavailable_midnight_is_excluded_from_profile(self):
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 8, 3),
            hora_cadastro=time(0, 0),
            hora_cadastro_fonte=SlaUtilConsolidado.HORA_FONTE_INDISPONIVEL,
            id_cliente=1,
            id_workflow=10,
            quantidade=10_000,
            date_key_cadastro=20260803,
        )
        profiles, missing, quality = build_workflow_hourly_profiles(
            {(1, 10)},
            on_date=date(2026, 8, 20),
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 31),
        )
        self.assertEqual(missing, [])
        self.assertEqual(profiles[(1, 10)][0]["volume"], 0)
        self.assertEqual(profiles[(1, 10)][6]["volume"], 60)
        self.assertEqual(quality["unavailable_hour_volume"], 10_000)
        self.assertEqual(quality["status"], "estimated")


class BuildHourlyCapacityTests(SimpleTestCase):
    @staticmethod
    def _profile(shares_by_hour):
        return [
            {
                "hour": hour,
                "volume": 0,
                "share": str(shares_by_hour.get(hour, Decimal("0"))),
                "share_pct": "0.00",
            }
            for hour in range(24)
        ]

    def assert_hourly_invariant(self, hourly):
        hourly_sum = sum(
            (Decimal(row["fte"]) for row in hourly["by_hour"]),
            Decimal("0"),
        )
        expected = Decimal(hourly["daily_exact_fte"]) * Decimal("5.5")
        self.assertEqual(hourly_sum, expected)
        self.assertEqual(Decimal(hourly["hourly_fte_total"]), expected)
        self.assertEqual(hourly["hourly_fte_delta"], "0.0000")
        self.assertTrue(hourly["hourly_fte_reconciled"])

    def test_calculates_simultaneous_fte_with_hourly_goal(self):
        profiles = {
            (1, 10): [
                {"hour": 0, "volume": 0, "share": "0.6", "share_pct": "60.00"},
                {"hour": 1, "volume": 0, "share": "0.4", "share_pct": "40.00"},
            ]
            + [
                {"hour": h, "volume": 0, "share": "0", "share_pct": "0.00"}
                for h in range(2, 24)
            ],
        }
        hourly = build_hourly_capacity(
            on_date=date(2026, 8, 20),
            workflow_totals={(1, 10): Decimal("100")},
            workflow_meta={
                (1, 10): {
                    "id_cliente": 1,
                    "id_workflow": 10,
                    "cliente_nome": "Cliente",
                    "workflow_nome": "WF",
                }
            },
            demand_by_stage={1000: Decimal("100")},
            demand_by_stage_wf={1000: {(1, 10): Decimal("100")}},
            stage_results=[
                {
                    "id_etapa": 1000,
                    "status": "dimensionada",
                    "meta_dia": "20",
                    "fte": "5.0000",
                    "agentes_necessarios": 5,
                }
            ],
            profiles=profiles,
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 20),
            workflows_without_profile=[],
        )
        hour0 = hourly["by_hour"][0]
        hour1 = hourly["by_hour"][1]
        self.assertEqual(hour0["projected_volume"], "60.00")
        self.assertEqual(hour1["projected_volume"], "40.00")
        self.assertEqual(hour0["fte"], "16.5000")
        self.assertEqual(hour1["fte"], "11.0000")
        self.assertEqual(hourly["daily_exact_fte"], "5.0000")
        self.assertEqual(hourly["productive_hours_per_fte"], "5.5")
        self.assertEqual(hourly["expected_fte_hours"], "27.5000")
        self.assertEqual(hourly["hourly_fte_total"], "27.5000")
        self.assertEqual(hourly["hourly_fte_delta"], "0.0000")
        self.assertTrue(hourly["hourly_fte_reconciled"])
        self.assertEqual(hourly["peak_hourly_fte"], "16.5000")

    def test_preaggregates_exact_fte_for_distinct_workflow_profiles(self):
        workflow_a = (1, 10)
        workflow_b = (1, 20)
        hourly = build_hourly_capacity(
            on_date=date(2026, 8, 20),
            workflow_totals={workflow_a: Decimal("20"), workflow_b: Decimal("30")},
            workflow_meta={},
            demand_by_stage={100: Decimal("20"), 200: Decimal("30")},
            demand_by_stage_wf={
                100: {workflow_a: Decimal("20")},
                200: {workflow_b: Decimal("30")},
            },
            stage_results=[
                {
                    "id_etapa": 100,
                    "status": "dimensionada",
                    "meta_dia": "10",
                    "fte": "2.0000",
                    "agentes_necessarios": 2,
                },
                {
                    "id_etapa": 200,
                    "status": "dimensionada",
                    "meta_dia": "30",
                    "fte": "1.0000",
                    "agentes_necessarios": 1,
                },
            ],
            profiles={
                workflow_a: self._profile({3: Decimal("0.25"), 4: Decimal("0.75")}),
                workflow_b: self._profile({3: Decimal("0.80"), 4: Decimal("0.20")}),
            },
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 20),
            workflows_without_profile=[],
        )

        expected_fte = [Decimal("0.0000")] * 24
        expected_fte[3] = Decimal("7.1500")
        expected_fte[4] = Decimal("9.3500")
        self.assertEqual(
            [Decimal(row["fte"]) for row in hourly["by_hour"]],
            expected_fte,
        )
        self.assertEqual(hourly["by_hour"][3]["demand"], "29.00")
        self.assertEqual(hourly["by_hour"][4]["demand"], "21.00")
        self.assertEqual(hourly["daily_exact_fte"], "3.0000")
        self.assert_hourly_invariant(hourly)

    def test_automatic_and_invalid_stages_do_not_contribute_to_fte(self):
        valid_workflow = (1, 10)
        automatic_workflow = (1, 20)
        invalid_workflow = (1, 30)
        hourly = build_hourly_capacity(
            on_date=date(2026, 8, 20),
            workflow_totals={
                valid_workflow: Decimal("10"),
                automatic_workflow: Decimal("1000"),
                invalid_workflow: Decimal("1000"),
            },
            workflow_meta={},
            demand_by_stage={
                100: Decimal("10"),
                200: Decimal("1000"),
                300: Decimal("1000"),
            },
            demand_by_stage_wf={
                100: {valid_workflow: Decimal("10")},
                200: {automatic_workflow: Decimal("1000")},
                300: {invalid_workflow: Decimal("1000")},
            },
            stage_results=[
                {
                    "id_etapa": 100,
                    "status": "dimensionada",
                    "meta_dia": "10",
                    "fte": "1.0000",
                    "agentes_necessarios": 1,
                },
                {
                    "id_etapa": 200,
                    "status": "automatica",
                    "meta_dia": None,
                    "fte": None,
                    "agentes_necessarios": 0,
                },
                {
                    "id_etapa": 300,
                    "status": "sem_meta",
                    "meta_dia": "0",
                    "fte": None,
                    "agentes_necessarios": 0,
                },
            ],
            profiles={
                valid_workflow: self._profile({8: Decimal("1")}),
                automatic_workflow: self._profile({8: Decimal("1")}),
                invalid_workflow: self._profile({8: Decimal("1")}),
            },
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 20),
            workflows_without_profile=[],
        )

        self.assertEqual(hourly["dimensioned_workflow_count"], 1)
        self.assertEqual(hourly["by_hour"][8]["projected_volume"], "10.00")
        self.assertEqual(hourly["by_hour"][8]["demand"], "10.00")
        self.assertEqual(hourly["by_hour"][8]["fte"], "5.5000")
        self.assert_hourly_invariant(hourly)

    def test_absent_profile_preserves_reconciliation_and_missing_contract(self):
        workflow = (7, 70)
        hourly = build_hourly_capacity(
            on_date=date(2026, 8, 20),
            workflow_totals={workflow: Decimal("10")},
            workflow_meta={
                workflow: {
                    "cliente_nome": "Cliente sem perfil",
                    "workflow_nome": "Workflow sem perfil",
                }
            },
            demand_by_stage={100: Decimal("10")},
            demand_by_stage_wf={100: {workflow: Decimal("10")}},
            stage_results=[
                {
                    "id_etapa": 100,
                    "status": "dimensionada",
                    "meta_dia": "10",
                    "fte": "1.0000",
                    "agentes_necessarios": 1,
                }
            ],
            profiles={},
            profile_from=date(2026, 8, 1),
            profile_to=date(2026, 8, 20),
            workflows_without_profile=[
                {"id_cliente": workflow[0], "id_workflow": workflow[1]}
            ],
        )

        self.assertEqual(hourly["workflows_without_profile_count"], 1)
        self.assertEqual(
            hourly["workflows_without_profile"][0],
            {
                "id_cliente": 7,
                "id_workflow": 70,
                "cliente_nome": "Cliente sem perfil",
                "workflow_nome": "Workflow sem perfil",
                "fallback": "uniform",
            },
        )
        self.assertEqual(hourly["peak_hour"], 0)
        self.assertEqual(hourly["by_hour"][0]["fte"], "5.5000")
        self.assertEqual(
            [row["fte"] for row in hourly["by_hour"][1:]],
            ["0.0000"] * 23,
        )
        self.assert_hourly_invariant(hourly)
