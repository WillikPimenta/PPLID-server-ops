from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import (
    CapacityDailySnapshot,
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DimCliente,
    DimEtapa,
    DimNivelHierarquico,
    DimProduto,
    DimWorkflow,
    MetaEtapa,
    ProjecaoSla,
)
from apps.dimensoes_processos.services.capacity import (
    calculate_capacity_period,
    calculate_daily_capacity,
)
from apps.dimensoes_processos.services.capacity_quarterly import (
    build_quarterly_weekday_reference,
)
from apps.dimensoes_processos.services.capacity_distribution import (
    calculate_capacity_distribution,
)
from apps.monitoramento_sla.models import SlaUtilConsolidado


User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class CapacityDailyApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="capacity_user", password="x")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(group)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.target_date = date(2026, 5, 25)  # segunda-feira
        self.reference_date = date(2026, 5, 24)
        self.client_dim = DimCliente.objects.create(id_cliente=1, nome="Cliente")
        self.produto_doc = DimProduto.objects.create(id_produto=1, tipo_produto="Documentoscopia")
        self.workflow_a = DimWorkflow.objects.create(
            id_workflow=10,
            nome="Workflow A",
            produto=self.produto_doc,
        )
        self.workflow_b = DimWorkflow.objects.create(
            id_workflow=11,
            nome="Workflow B",
            produto=self.produto_doc,
        )
        self.nh_a = DimNivelHierarquico.objects.create(id_nh=100, nome="NH A")
        self.nh_b = DimNivelHierarquico.objects.create(id_nh=101, nome="NH B")
        self.stage_manual = DimEtapa.objects.create(id_etapa=1000, nome="Análise")
        self.stage_auto = DimEtapa.objects.create(id_etapa=1001, nome="OCR")

        scan = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
            finished_at=timezone.now(),
            source_fingerprint="scan-fingerprint",
        )
        self.import_run = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
            finished_at=timezone.now(),
            reviewed_scan=scan,
            triggered_by=self.user,
        )

        DerivacaoEtapaDiaria.objects.create(
            data=self.reference_date,
            cliente=self.client_dim,
            workflow=self.workflow_a,
            etapa=self.stage_manual,
            registros=50,
            percentual="50.00",
            import_run=self.import_run,
        )
        DerivacaoEtapaDiaria.objects.create(
            data=self.reference_date,
            cliente=self.client_dim,
            workflow=self.workflow_b,
            etapa=self.stage_manual,
            registros=10,
            percentual="100.00",
            import_run=self.import_run,
        )
        DerivacaoEtapaDiaria.objects.create(
            data=self.reference_date,
            cliente=self.client_dim,
            workflow=self.workflow_a,
            etapa=self.stage_auto,
            registros=100,
            percentual="100.00",
            import_run=self.import_run,
        )

        ProjecaoSla.objects.create(
            cliente=self.client_dim,
            workflow=self.workflow_a,
            nivel_hierarquico=self.nh_a,
            data_inicio=date(2026, 5, 1),
            dias_semana="{0}",
            volume=60,
        )
        ProjecaoSla.objects.create(
            cliente=self.client_dim,
            workflow=self.workflow_a,
            nivel_hierarquico=self.nh_b,
            data_inicio=date(2026, 5, 1),
            dias_semana="{0}",
            volume=40,
        )
        ProjecaoSla.objects.create(
            cliente=self.client_dim,
            workflow=self.workflow_b,
            nivel_hierarquico=self.nh_a,
            data_inicio=date(2026, 5, 1),
            dias_semana="{0}",
            volume=10,
        )
        MetaEtapa.objects.create(
            etapa=self.stage_manual,
            data_inicio=date(2026, 5, 1),
            meta_dia=20,
        )

    def _fill_complete_q1_calendar(self):
        cursor = date(2026, 1, 1)
        rows = []
        while cursor <= date(2026, 3, 31):
            rows.append(
                SlaUtilConsolidado(
                    data_cadastro=cursor,
                    id_cliente=999,
                    id_workflow=999,
                    id_nh=999,
                    quantidade=1,
                    date_key_cadastro=int(cursor.strftime("%Y%m%d")),
                )
            )
            cursor += timedelta(days=1)
        SlaUtilConsolidado.objects.bulk_create(rows)

    def test_capacity_sums_nhs_and_rounds_after_consolidating_stage(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat(), "include_dax": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ready"])
        self.assertEqual(response.data["summary"]["projected_workflow_volume"], "110.00")
        self.assertEqual(response.data["summary"]["agents_required"], 3)
        self.assertEqual(response.data["analysis"]["import_run_id"], self.import_run.pk)
        self.assertEqual(response.data["analysis"]["reference_date"], "2026-05-24")
        self.assertEqual(response.data["analysis"]["derivation_method"], "median_60d")

        manual = next(row for row in response.data["results"] if row["id_etapa"] == 1000)
        self.assertEqual(manual["analises_previstas"], "60.00")
        self.assertEqual(manual["meta_dia"], "20.00")
        self.assertEqual(manual["fte"], "3.0000")
        self.assertEqual(manual["agentes_necessarios"], 3)
        self.assertEqual(manual["folga_capacidade"], "0.00")

        self.assertIn("dax", response.data)
        self.assertIn("comparative", response.data)
        self.assertEqual(response.data["comparative"]["legacy"]["agents_required"], 3)
        self.assertNotIn("delta", response.data["comparative"])
        self.assertFalse(response.data["comparative"]["comparison"]["comparable"])
        self.assertIn("capacity_esperada_total", response.data["dax"]["summary"])

        automatic = next(row for row in response.data["results"] if row["id_etapa"] == 1001)
        self.assertEqual(automatic["status"], "automatica")
        self.assertEqual(automatic["agentes_necessarios"], 0)

        self.assertEqual(manual["familia"], "Análise")
        self.assertEqual(automatic["familia"], "OCR")

        by_familia = {row["familia"]: row for row in response.data["by_familia"]}
        self.assertEqual(sum(row["agents"] for row in response.data["by_familia"]), response.data["summary"]["agents_required"])
        analise = by_familia["Análise"]
        self.assertEqual(analise["stage_count"], 1)
        self.assertEqual(analise["agents"], 3)
        self.assertEqual(analise["demand"], "60.00")
        self.assertEqual(analise["slack"], "0.00")
        ocr = by_familia["OCR"]
        self.assertEqual(ocr["automatic_demand"], "100.00")
        self.assertEqual(ocr["agents"], 0)

        by_client = response.data["by_client"]
        self.assertEqual(len(by_client), 1)
        client_row = by_client[0]
        self.assertAlmostEqual(float(client_row["agents_attributed"]), 3.0, places=4)
        self.assertEqual(client_row["top_familias"][0]["familia"], "Análise")

        by_workflow = {row["id_workflow"]: row for row in response.data["by_workflow"]}
        self.assertAlmostEqual(float(by_workflow[10]["agents_attributed"]), 2.5, places=4)
        self.assertAlmostEqual(float(by_workflow[11]["agents_attributed"]), 0.5, places=4)
        self.assertEqual(by_workflow[10]["stage_count"], 2)
        self.assertEqual(len(by_workflow[10]["stages"]), 2)
        self.assertEqual(by_workflow[11]["stage_count"], 1)
        stage_ids_wf_a = {row["id_etapa"] for row in by_workflow[10]["stages"]}
        self.assertEqual(stage_ids_wf_a, {1000, 1001})

        breakdown = response.data["breakdown"]
        self.assertGreaterEqual(len(breakdown), 3)
        self.assertFalse(response.data["breakdown_truncated"])
        manual_breakdown = next(
            row for row in breakdown if row["id_etapa"] == 1000 and row["id_workflow"] == 10
        )
        self.assertEqual(manual_breakdown["agents_share"], "2.5000")
        self.assertEqual(manual_breakdown["fte_share"], "2.5000")
        self.assertEqual(manual_breakdown["status"], "dimensionada")

        hourly = response.data["hourly"]
        self.assertEqual(len(hourly["by_hour"]), 24)
        self.assertIn("profile_from", hourly)
        self.assertIn("profile_to", hourly)
        total_projected = sum(float(row["projected_volume"]) for row in hourly["by_hour"])
        self.assertAlmostEqual(total_projected, float(response.data["summary"]["projected_workflow_volume"]), places=0)

    def test_exports_approved_derivations_and_expected_hourly_volume(self):
        derivacoes = self.client.get(
            "/api/v1/dimensoes-processos/capacity/export/derivacoes.csv",
            {"date": self.target_date.isoformat()},
        )
        self.assertEqual(derivacoes.status_code, 200)
        self.assertIn("text/csv", derivacoes["Content-Type"])
        body = derivacoes.content.decode("utf-8-sig")
        self.assertIn("Cliente,1,Workflow A,10,Análise,1000", body)
        self.assertIn("100.00,50.00,50.00,2.5000", body)

        hourly = self.client.get(
            "/api/v1/dimensoes-processos/capacity/export/volume-esperado-hora.csv",
            {"date": self.target_date.isoformat()},
        )
        self.assertEqual(hourly.status_code, 200)
        hourly_body = hourly.content.decode("utf-8-sig")
        self.assertIn("Data,Cliente,ID Cliente,Hora,Volume esperado", hourly_body)
        self.assertIn("Cliente,1,00:00,4.58", hourly_body)

    def test_capacity_explains_stage_rounding_without_changing_headcount(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        summary = response.data["summary"]
        self.assertEqual(summary["agents_required"], 3)
        self.assertEqual(summary["exact_fte_before_rounding"], "3.0000")
        self.assertEqual(summary["rounding_addition_headcount"], "0.0000")
        self.assertEqual(summary["fully_pooled_headcount"], 3)
        self.assertEqual(summary["family_pooled_headcount"], 3)
        self.assertEqual(summary["stages_below_one_fte"], 0)
        self.assertLessEqual(
            summary["stages_rounded_to_one"], summary["stages_below_one_fte"]
        )
        self.assertEqual(response.data["analysis"]["derivation_age_days"], 1)
        self.assertTrue(response.data["analysis"]["derivation_reused"])
        self.assertEqual(
            response.data["explainability"]["headcount_formula"],
            "sum_by_stage(ceil(stage_demand / stage_daily_goal))",
        )

    def test_quarterly_reference_uses_previous_complete_quarter_and_keeps_official_separate(self):
        self._fill_complete_q1_calendar()
        for observed_date, multiplier in (
            (date(2026, 1, 5), 1),
            (date(2026, 1, 6), 3),
        ):
            for workflow_id, nh_id, base in ((10, 100, 6), (10, 101, 4), (11, 100, 1)):
                SlaUtilConsolidado.objects.create(
                    data_cadastro=observed_date,
                    id_cliente=1,
                    id_workflow=workflow_id,
                    id_nh=nh_id,
                    quantidade=base * multiplier,
                    date_key_cadastro=int(observed_date.strftime("%Y%m%d")),
                )

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat(), "include_quarterly": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["projected_workflow_volume"], "110.00")
        quarterly = response.data["quarterly_reference"]
        self.assertFalse(quarterly["official_projection_replaced"])
        self.assertEqual(quarterly["window_from"], "2026-01-01")
        self.assertEqual(quarterly["window_to"], "2026-03-31")
        self.assertEqual(quarterly["projected_workflow_volume"], "27.5000")
        self.assertEqual(quarterly["exact_fte_before_rounding"], "0.7500")
        self.assertEqual(quarterly["agents_required"], 1)
        self.assertEqual(quarterly["fallback_grain_count"], 0)

    def test_quarterly_week_conserves_canonical_projection_and_excludes_duplicate_nh(self):
        self._fill_complete_q1_calendar()
        ProjecaoSla.objects.create(
            cliente=self.client_dim,
            workflow=self.workflow_a,
            nivel_hierarquico=self.nh_a,
            data_inicio=date(2026, 5, 1),
            dias_semana="{0}",
            volume=999,
        )
        cache = {}
        week = [self.target_date + timedelta(days=offset) for offset in range(7)]
        references = [
            build_quarterly_weekday_reference(
                day,
                cache=cache,
            )
            for day in week
        ]

        # NH A do workflow A esta duplicado e e excluido integralmente. Restam
        # NH B=40 e workflow B/NH A=10 na segunda-feira.
        reference_week_total = sum(
            (sum(item["workflow_totals"].values()) for item in references),
            start=0,
        )
        self.assertEqual(reference_week_total, 50)

    def test_quarterly_integrated_week_uses_union_of_workflows_from_all_seven_days(self):
        self._fill_complete_q1_calendar()
        workflow_tuesday = DimWorkflow.objects.create(
            id_workflow=12,
            nome="Workflow Tuesday",
            produto=self.produto_doc,
        )
        ProjecaoSla.objects.create(
            cliente=self.client_dim,
            workflow=workflow_tuesday,
            nivel_hierarquico=self.nh_a,
            data_inicio=date(2026, 5, 1),
            dias_semana="{1}",
            volume=70,
        )
        for workflow, etapa, percentual in (
            (self.workflow_a, self.stage_manual, "50.00"),
            (self.workflow_a, self.stage_auto, "100.00"),
            (self.workflow_b, self.stage_manual, "100.00"),
        ):
            DerivacaoEtapaDiaria.objects.create(
                data=date(2026, 5, 17),
                cliente=self.client_dim,
                workflow=workflow,
                etapa=etapa,
                registros=1,
                percentual=percentual,
                import_run=self.import_run,
            )

        cache = {}
        profile_cache = {}
        references = []
        for offset in range(7):
            payload = calculate_daily_capacity(
                date(2026, 5, 18) + timedelta(days=offset),
                include_details=False,
                include_dax=False,
                include_quarterly=True,
                quarterly_volume_cache=cache,
                hourly_profile_cache=profile_cache,
            )
            references.append(payload["quarterly_reference"])

        self.assertEqual(
            {row["weekly_projection_volume"] for row in references}, {"180.0000"}
        )
        self.assertEqual(
            sum(Decimal(row["projected_workflow_volume"]) for row in references),
            Decimal("180.0000"),
        )

    def test_quarterly_reference_is_unavailable_without_complete_calendar_quarter(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat(), "include_quarterly": "true"},
        )

        quarterly = response.data["quarterly_reference"]
        self.assertEqual(quarterly["availability"], "unavailable")
        self.assertEqual(quarterly["reason"], "no_complete_calendar_quarter")
        self.assertIsNone(quarterly["projected_workflow_volume"])

    def test_capacity_by_familia_uses_prefix_before_dash(self):
        prefixed_stage = DimEtapa.objects.create(id_etapa=1002, nome="Validação - Manual")
        DerivacaoEtapaDiaria.objects.create(
            data=self.reference_date,
            cliente=self.client_dim,
            workflow=self.workflow_a,
            etapa=prefixed_stage,
            registros=20,
            percentual="100.00",
            import_run=self.import_run,
        )
        MetaEtapa.objects.create(
            etapa=prefixed_stage,
            data_inicio=date(2026, 5, 1),
            meta_dia=10,
        )

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        prefixed = next(row for row in response.data["results"] if row["id_etapa"] == 1002)
        self.assertEqual(prefixed["familia"], "Validação")
        by_familia = {row["familia"]: row for row in response.data["by_familia"]}
        self.assertIn("Validação", by_familia)
        self.assertEqual(sum(row["agents"] for row in response.data["by_familia"]), response.data["summary"]["agents_required"])

    def test_capacity_by_client_agents_attributed_sum_matches_total(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        total_attributed = sum(float(row["agents_attributed"]) for row in response.data["by_client"])
        self.assertAlmostEqual(total_attributed, float(response.data["summary"]["agents_required"]), places=4)
        total_workflow_attributed = sum(
            float(row["agents_attributed"]) for row in response.data["by_workflow"]
        )
        self.assertAlmostEqual(total_workflow_attributed, float(response.data["summary"]["agents_required"]), places=4)

    def test_capacity_fte_aggregations_preserve_fractional_values(self):
        MetaEtapa.objects.filter(etapa=self.stage_manual).update(meta_dia=40)

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["exact_fte_before_rounding"], "1.5000")
        self.assertEqual(response.data["summary"]["agents_required"], 2)
        analise = next(row for row in response.data["by_familia"] if row["fte"] == "1.5000")
        self.assertEqual(analise["fte"], "1.5000")
        self.assertEqual(analise["share_pct"], "100.00")
        self.assertEqual(response.data["by_client"][0]["fte_attributed"], "1.5000")
        self.assertEqual(
            sum(float(row["fte_attributed"]) for row in response.data["by_workflow"]),
            1.5,
        )

    def test_capacity_blocks_ambiguous_active_meta(self):
        MetaEtapa.objects.create(
            etapa=self.stage_manual,
            data_inicio=date(2026, 5, 10),
            meta_dia=25,
        )

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ready"])
        manual = next(row for row in response.data["results"] if row["id_etapa"] == 1000)
        self.assertEqual(manual["status"], "meta_ambigua")
        self.assertIsNone(manual["agentes_necessarios"])
        self.assertIn(
            "meta_ambigua_ou_invalida",
            {blocker["code"] for blocker in response.data["blockers"]},
        )

    def test_capacity_prefers_import_covering_target_date(self):
        future_scan = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 26),
            finished_at=timezone.now(),
            source_fingerprint="future-scan",
        )
        future_import = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 26),
            finished_at=timezone.now(),
            reviewed_scan=future_scan,
            triggered_by=self.user,
        )
        DerivacaoEtapaDiaria.objects.filter(import_run=self.import_run).update(
            import_run=future_import,
        )
        DerivacaoEtapaDiaria.objects.create(
            data=date(2026, 5, 25),
            cliente=self.client_dim,
            workflow=self.workflow_a,
            etapa=self.stage_manual,
            registros=999,
            percentual="99.00",
            import_run=future_import,
        )

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["analysis"]["import_run_id"], future_import.pk)
        self.assertEqual(response.data["analysis"]["reference_date"], "2026-05-24")
        self.assertEqual(response.data["analysis"]["derivation_method"], "median_60d")

    def test_capacity_blocks_duplicate_projection_for_same_nh(self):
        ProjecaoSla.objects.create(
            cliente=self.client_dim,
            workflow=self.workflow_a,
            nivel_hierarquico=self.nh_a,
            data_inicio=date(2026, 5, 1),
            dias_semana="{0}",
            volume=60,
        )

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ready"])
        self.assertIn(
            "projecao_duplicada",
            {blocker["code"] for blocker in response.data["blockers"]},
        )

    def test_capacity_requires_an_approved_analysis_before_target_date(self):
        DerivacaoEtapaDiaria.objects.all().delete()
        DerivacaoEtapaImportRun.objects.filter(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT
        ).delete()
        DerivacaoEtapaImportRun.objects.all().delete()

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.data["ready"])
        self.assertIn("análise de derivação", response.data["detail"])

    def test_capacity_rejects_invalid_date(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": "25/05/2026"},
        )

        self.assertEqual(response.status_code, 400)

    def test_period_one_day_matches_daily_canonical_summary(self):
        daily = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )
        period = self.client.get(
            "/api/v1/dimensoes-processos/capacity/periodo/",
            {
                "date_from": self.target_date.isoformat(),
                "date_to": self.target_date.isoformat(),
            },
        )

        self.assertEqual(period.status_code, 200)
        # O HC continua disponivel, mas a ausencia de perfil horario torna o
        # periodo indicativo para decisao de escala.
        self.assertFalse(period.data["ready"])
        self.assertEqual(period.data["meta"]["day_count"], 1)
        self.assertEqual(
            period.data["series"][0]["daily_headcount"],
            daily.data["summary"]["agents_required"],
        )
        self.assertEqual(
            period.data["series"][0]["projected_volume"],
            daily.data["summary"]["projected_workflow_volume"],
        )
        self.assertEqual(
            period.data["series"][0]["peak_concurrent_fte"],
            daily.data["hourly"]["peak_capacity_hora"],
        )
        self.assertEqual(
            period.data["series"][0]["peak_hour"],
            daily.data["hourly"]["peak_hour"],
        )
        self.assertEqual(period.data["summary"]["ready_days"], 0)
        self.assertEqual(period.data["summary"]["issue_days"], 1)
        self.assertEqual(period.data["series"][0]["warnings_count"], 2)
        self.assertEqual(
            {row["code"] for row in period.data["quality"]["warnings"]},
            {"perfil_horario_ausente", "quarterly_snapshot_missing"},
        )
        self.assertEqual(
            period.data["meta"]["units"]["peak_concurrent_fte"],
            "fractional_people",
        )

    def test_period_rejects_invalid_order_and_more_than_31_days(self):
        reversed_period = self.client.get(
            "/api/v1/dimensoes-processos/capacity/periodo/",
            {"date_from": "2026-05-26", "date_to": "2026-05-25"},
        )
        oversized_period = self.client.get(
            "/api/v1/dimensoes-processos/capacity/periodo/",
            {"date_from": "2026-05-01", "date_to": "2026-06-01"},
        )

        self.assertEqual(reversed_period.status_code, 400)
        self.assertEqual(oversized_period.status_code, 400)

    def test_period_marks_unavailable_day_in_quality(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/periodo/",
            {"date_from": "2026-05-31", "date_to": "2026-06-01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["series"]), 2)
        self.assertTrue(response.data["series"][0]["ready"])
        self.assertFalse(response.data["series"][1]["ready"])
        self.assertIsNone(response.data["series"][1]["daily_headcount"])
        self.assertEqual(response.data["summary"]["issue_days"], 1)
        self.assertEqual(
            response.data["quality"]["blockers"][0]["code"],
            "capacity_unavailable",
        )

    def test_period_keeps_indicative_metrics_when_quality_has_blockers(self):
        ProjecaoSla.objects.create(
            cliente=self.client_dim,
            workflow=self.workflow_a,
            nivel_hierarquico=self.nh_a,
            data_inicio=date(2026, 5, 1),
            dias_semana="{0}",
            volume=60,
        )

        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/periodo/",
            {
                "date_from": self.target_date.isoformat(),
                "date_to": self.target_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ready"])
        self.assertIsNotNone(response.data["series"][0]["daily_headcount"])
        self.assertIsNotNone(response.data["series"][0]["projected_volume"])
        self.assertIsNotNone(response.data["series"][0]["peak_concurrent_fte"])
        self.assertGreater(response.data["series"][0]["blockers_count"], 0)

    def test_period_reads_fresh_materialized_day_with_two_guarded_queries(self):
        call_command(
            "sync_capacity_snapshots",
            date_from=self.target_date.isoformat(),
            date_to=self.target_date.isoformat(),
            ttl_minutes=60,
            verbosity=0,
        )

        # Uma query valida o fingerprint atual das fontes e outra le a geracao.
        with self.assertNumQueries(2):
            payload = calculate_capacity_period(self.target_date, self.target_date)

        self.assertEqual(payload["meta"]["snapshot"]["state"], "fresh")
        self.assertEqual(payload["meta"]["snapshot"]["fresh_days"], 1)
        self.assertEqual(payload["summary"]["max_daily_headcount"], 3)
        self.assertEqual(payload["summary"]["max_daily_exact_fte"], "3.0000")
        self.assertEqual(payload["summary"]["max_fully_pooled_headcount"], 3)
        self.assertEqual(payload["summary"]["max_family_pooled_headcount"], 3)
        self.assertEqual(payload["series"][0]["exact_fte_before_rounding"], "3.0000")
        self.assertEqual(payload["series"][0]["family_pooled_headcount"], 3)

    def test_manual_without_overrides_reuses_exact_planning_snapshot(self):
        call_command(
            "sync_capacity_snapshots",
            date_from=self.target_date.isoformat(),
            date_to=self.target_date.isoformat(),
            ttl_minutes=60,
            verbosity=0,
        )

        planning = calculate_capacity_period(self.target_date, self.target_date)
        manual = calculate_capacity_period(
            self.target_date,
            self.target_date,
            scenario_id="manual",
            manual_overrides={},
        )

        self.assertEqual(manual["meta"]["snapshot"]["state"], "fresh")
        self.assertEqual(manual["meta"]["scenario"]["id"], "manual")
        self.assertEqual(manual["summary"], planning["summary"])
        self.assertEqual(manual["series"], planning["series"])

    def test_force_live_bypasses_snapshot_for_comparable_simulation_baseline(self):
        call_command(
            "sync_capacity_snapshots",
            date_from=self.target_date.isoformat(),
            date_to=self.target_date.isoformat(),
            ttl_minutes=60,
            verbosity=0,
        )
        snapshot = CapacityDailySnapshot.objects.get(calculation_date=self.target_date)
        changed = snapshot.payload
        changed["series"]["exact_fte_before_rounding"] = "999.0000"
        snapshot.payload = changed
        snapshot.save(update_fields=["payload"])

        cached = calculate_capacity_period(self.target_date, self.target_date)
        live = calculate_capacity_period(
            self.target_date, self.target_date,
            scenario_id="manual", manual_overrides={}, force_live=True,
        )

        self.assertEqual(cached["summary"]["max_daily_exact_fte"], "999.0000")
        self.assertEqual(live["meta"]["snapshot"]["state"], "fallback_live")
        self.assertEqual(live["summary"]["max_daily_exact_fte"], "3.0000")

    def test_snapshot_sync_is_idempotent_and_expired_snapshot_falls_back_live(self):
        kwargs = {
            "date_from": self.target_date.isoformat(),
            "date_to": self.target_date.isoformat(),
            "ttl_minutes": 60,
            "verbosity": 0,
        }
        call_command("sync_capacity_snapshots", **kwargs)
        first = CapacityDailySnapshot.objects.get(calculation_date=self.target_date)
        first_generation = first.generation_id
        call_command("sync_capacity_snapshots", **kwargs)
        snapshot = CapacityDailySnapshot.objects.get(calculation_date=self.target_date)

        self.assertEqual(CapacityDailySnapshot.objects.count(), 1)
        self.assertNotEqual(snapshot.generation_id, first_generation)

        snapshot.valid_until = timezone.now() - timedelta(seconds=1)
        snapshot.save(update_fields=["valid_until"])
        payload = calculate_capacity_period(self.target_date, self.target_date)
        self.assertEqual(payload["meta"]["snapshot"]["state"], "fallback_live")
        self.assertEqual(payload["meta"]["snapshot"]["stale_dates"], [self.target_date.isoformat()])
        self.assertEqual(
            payload["meta"]["snapshot"]["fallback_live_dates"],
            [self.target_date.isoformat()],
        )
        self.assertIsNotNone(payload["series"][0]["daily_headcount"])
        self.assertEqual(payload["summary"]["max_daily_exact_fte"], "3.0000")

    @patch("apps.dimensoes_processos.services.capacity.build_dax_capacity_response")
    def test_daily_operational_path_does_not_load_dax_by_default(self, dax_mock):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("dax", response.data)
        dax_mock.assert_not_called()

    @patch(
        "apps.dimensoes_processos.services.capacity.build_dax_capacity_response",
        side_effect=RuntimeError("DAX indisponivel"),
    )
    def test_daily_dax_failure_is_not_serialized_as_valid_zero(self, _mock_dax):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/diario/",
            {"date": self.target_date.isoformat(), "include_dax": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["dax"]["availability"], "unavailable")
        self.assertIsNone(response.data["dax"]["summary"]["capacity_esperada_total"])
        self.assertIsNone(response.data["comparative"])

    def test_distribution_date_only_lists_workflows_without_hourly_cube(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {"date": self.target_date.isoformat(), "page_size": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["mode"], "workflow_list")
        self.assertIsNone(response.data["hourly_profile"])
        self.assertEqual(response.data["pagination"]["total"], 2)
        self.assertEqual(len(response.data["workflows"]), 1)
        self.assertNotIn("stages", response.data["workflows"][0])

    def test_distribution_workflow_conserves_daily_values_and_has_24_hours(self):
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {"date": self.target_date.isoformat(), "id_workflow": 10},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["mode"], "distribution_detail")
        summary = response.data["summary"]
        self.assertEqual(summary["projected_ingress_volume"], "100.00")
        self.assertEqual(summary["derived_stage_demand"], "150.00")
        self.assertEqual(summary["dimensioned_stage_demand"], "50.00")
        self.assertEqual(summary["exact_daily_fte"], "2.5000")
        self.assertEqual(summary["dedicated_stage_headcount"], 3)
        self.assertEqual(summary["attributed_dedicated_headcount"], "2.5000")
        self.assertEqual(len(response.data["hourly_profile"]["by_hour"]), 24)
        self.assertEqual(response.data["pagination"]["total"], 2)
        self.assertIn("hourly", response.data["non_additivity"])

        total_hourly_demand = sum(
            Decimal(row["demand"])
            for row in response.data["hourly_profile"]["by_hour"]
        )
        # Somente a etapa humana dimensionada entra no FTE horario.
        self.assertAlmostEqual(total_hourly_demand, Decimal("50"), delta=Decimal("0.10"))
        total_hourly_fte = sum(
            Decimal(row["fte"])
            for row in response.data["hourly_profile"]["by_hour"]
        )
        self.assertAlmostEqual(total_hourly_fte, Decimal("13.7500"), delta=Decimal("0.001"))
        self.assertEqual(summary["expected_fte_hours"], "13.7500")
        self.assertEqual(summary["hourly_fte_total"], "13.7500")
        self.assertEqual(summary["hourly_fte_delta"], "0.0000")
        self.assertTrue(summary["hourly_fte_reconciled"])

    def test_distribution_stage_filter_and_attribution_conserve_official_hc(self):
        workflow_a = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {"date": self.target_date.isoformat(), "id_workflow": 10, "id_etapa": 1000},
        )
        workflow_b = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {"date": self.target_date.isoformat(), "id_workflow": 11, "id_etapa": 1000},
        )

        attributed = Decimal(
            workflow_a.data["summary"]["attributed_dedicated_headcount"]
        ) + Decimal(workflow_b.data["summary"]["attributed_dedicated_headcount"])
        self.assertEqual(attributed, Decimal("3.0000"))
        self.assertEqual(workflow_a.data["summary"]["dedicated_stage_headcount"], 3)
        self.assertEqual(workflow_b.data["summary"]["dedicated_stage_headcount"], 3)

    def test_distribution_is_bounded_and_stays_within_query_budget(self):
        with CaptureQueriesContext(connection) as captured:
            payload = calculate_capacity_distribution(
                self.target_date,
                id_workflow=10,
            )

        self.assertLessEqual(len(captured), 9)
        self.assertLess(len(str(payload).encode("utf-8")), 250_000)

        invalid = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {"date": self.target_date.isoformat(), "page_size": 101},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_hourly_drilldown_exposes_explicit_metric_contract_and_reconciles_fte(self):
        SlaUtilConsolidado.objects.create(
            data_cadastro=self.target_date,
            hora_cadastro=time(0, 0),
            hora_cadastro_fonte=SlaUtilConsolidado.HORA_FONTE_REAL,
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            quantidade=12,
            date_key_cadastro=int(self.target_date.strftime("%Y%m%d")),
        )
        response = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {"date": self.target_date.isoformat(), "hour": 0, "level": "family"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["mode"], "hourly_drilldown")
        contract = response.data["metric_contract"]
        self.assertEqual(contract["status"], "simultaneous_fte")
        self.assertEqual(contract["rounding_scope"], "none_for_fte")
        self.assertIsNone(contract["integer_reference"])
        self.assertFalse(contract["unique_people_supported"])
        self.assertEqual(
            response.data["reconciliation"]["expected"]["delta"], "0.0000"
        )
        self.assertFalse(response.data["reconciliation"]["people_reference_additive"])
        self.assertIn("expected_hourly_fte", response.data["units"])
        self.assertIn("received_hourly_fte", response.data["units"])

        analysis = next(row for row in response.data["rows"] if row["familia"] == "Análise")
        self.assertIsNotNone(analysis["goal_per_day"])
        self.assertIsNotNone(analysis["received_volume"])
        self.assertIsNotNone(analysis["received_hourly_fte"])
        self.assertIn("expression", analysis["calculation_memory"])
        self.assertIn("expected_volume", analysis["sources"])
        self.assertTrue(analysis["fallback"])

    def test_hourly_drilldown_family_workflow_stage_preserves_context(self):
        workflows = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {
                "date": self.target_date.isoformat(),
                "hour": 0,
                "level": "workflow",
                "familia": "Análise",
            },
        )
        self.assertEqual(workflows.status_code, 200)
        workflow = next(row for row in workflows.data["rows"] if row["id_workflow"] == 10)
        self.assertEqual(workflow["familia"], "Análise")
        self.assertEqual(workflows.data["breadcrumb"][1]["level"], "family")

        stages = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {
                "date": self.target_date.isoformat(),
                "hour": 0,
                "level": "stage",
                "familia": "Análise",
                "id_cliente": workflow["id_cliente"],
                "id_workflow": workflow["id_workflow"],
            },
        )
        self.assertEqual(stages.status_code, 200)
        self.assertEqual(stages.data["rows"][0]["id_etapa"], 1000)
        self.assertEqual(stages.data["rows"][0]["people_reference"], 1)

    def test_hourly_drilldown_validates_hierarchy_and_exports_current_slice(self):
        missing_family = self.client.get(
            "/api/v1/dimensoes-processos/capacity/distribuicao/",
            {"date": self.target_date.isoformat(), "hour": 0, "level": "workflow"},
        )
        self.assertEqual(missing_family.status_code, 400)

        exported = self.client.get(
            "/api/v1/dimensoes-processos/capacity/export/distribuicao-horaria.csv",
            {"date": self.target_date.isoformat(), "hour": 0, "level": "family"},
        )
        self.assertEqual(exported.status_code, 200)
        body = exported.content.decode("utf-8-sig")
        self.assertIn("FTE simultaneo esperado", body)
        self.assertIn("Análise", body)

    def test_simulation_comparison_returns_all_variants_in_one_response(self):
        response = self.client.post(
            "/api/v1/dimensoes-processos/capacity/simulacao/compare/",
            {
                "overrides": {
                    "workflows": [
                        {
                            "id_cliente": 1,
                            "id_workflow": 10,
                            "percentual_variacao": 10,
                        }
                    ],
                    "metas": [{"id_etapa": 1000, "meta_dia": 20}],
                }
            },
            format="json",
            QUERY_STRING=(
                f"date_from={self.target_date.isoformat()}&"
                f"date_to={self.target_date.isoformat()}&scenario=manual"
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data["results"]),
            {"baseline", "volume", "derivation", "full"},
        )
        self.assertEqual(
            response.data["results"]["baseline"]["meta"]["scenario"]["id"],
            "manual",
        )
        self.assertNotEqual(
            response.data["results"]["baseline"]["summary"]["max_daily_exact_fte"],
            response.data["results"]["full"]["summary"]["max_daily_exact_fte"],
        )

    def test_serialized_fte_aggregations_reconcile_to_daily_total(self):
        payload = calculate_daily_capacity(
            self.target_date,
            include_details=True,
            include_hourly=True,
            include_dax=False,
        )
        target = Decimal(payload["summary"]["exact_fte_before_rounding"])

        self.assertEqual(
            sum(
                Decimal(row["fte"])
                for row in payload["results"]
                if row["fte"] is not None
            ),
            target,
        )
        self.assertEqual(
            sum(Decimal(row["fte"]) for row in payload["by_familia"]),
            target,
        )
        self.assertEqual(
            sum(Decimal(row["fte_attributed"]) for row in payload["by_client"]),
            target,
        )
        self.assertEqual(
            sum(Decimal(row["fte_attributed"]) for row in payload["by_workflow"]),
            target,
        )

    def test_fresh_period_and_compact_daily_stay_within_regression_budgets(self):
        call_command(
            "sync_capacity_snapshots",
            date_from=self.target_date.isoformat(),
            date_to=self.target_date.isoformat(),
            ttl_minutes=60,
            verbosity=0,
        )
        with CaptureQueriesContext(connection) as period_queries:
            period = calculate_capacity_period(self.target_date, self.target_date)

        self.assertEqual(period["meta"]["snapshot"]["state"], "fresh")
        self.assertLessEqual(len(period_queries), 2)
        self.assertLess(len(str(period).encode("utf-8")), 100_000)

        with CaptureQueriesContext(connection) as daily_queries:
            compact = calculate_daily_capacity(
                self.target_date,
                include_details=False,
                include_hourly=True,
                include_dax=False,
            )

        self.assertLessEqual(len(daily_queries), 10)
        self.assertLess(len(str(compact).encode("utf-8")), 200_000)
