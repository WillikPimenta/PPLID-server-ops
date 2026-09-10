from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.dimensoes_processos.models import (
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DimCliente,
    DimEtapa,
    DimProduto,
    DimWorkflow,
    MetaEtapa,
)
from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilDetalhe

from apps.dimensoes_processos.services.capacity_scenarios import (
    _manual_derivation_rows,
    _received_by_workflow,
    normalize_scenario_id,
    resolve_derivation,
    resolve_workflow_volumes,
    scenario_config,
    scenario_public_payload,
)
from apps.dimensoes_processos.views_capacity import _scenario_from_request


class CapacityScenarioConfigTests(TestCase):
    def test_normalize_aliases(self):
        self.assertEqual(normalize_scenario_id("1"), "planejamento")
        self.assertEqual(normalize_scenario_id("operacao"), "operacao")
        self.assertEqual(normalize_scenario_id("operacional"), "planejamento_operacional")
        self.assertEqual(normalize_scenario_id("unknown"), "planejamento")

    def test_planejamento_operacional_config(self):
        config = scenario_config("planejamento_operacional")
        self.assertEqual(config.id, "planejamento_operacional")
        self.assertEqual(config.volume_mode, "projecao_sla")
        self.assertIn("Operacional", config.label)

    def test_public_payload_planejamento_uses_median(self):
        payload = scenario_public_payload(scenario_config("planejamento"))
        self.assertEqual(payload["derivation"]["mode"], "median_60d")
        self.assertIn("Mediana", payload["derivation"]["label"])
        self.assertIn("Documentoscopia", payload["volume"]["label"])

    def test_public_payload_contains_labels(self):
        payload = scenario_public_payload(scenario_config("operacao"))
        self.assertEqual(payload["volume"]["label"], "Recebido do dia (Documentoscopia)")
        self.assertIn("Do dia", payload["derivation"]["label"])
        self.assertEqual(
            payload["derivation"]["fallback_mode"],
            "latest_approved_reference",
        )


class CapacityScenarioCalculationTests(TestCase):
    @patch("apps.dimensoes_processos.services.capacity.filter_documentoscopia_workflows", side_effect=lambda t, m: (t, m))
    @patch("apps.dimensoes_processos.services.capacity._projection_by_workflow")
    @patch("apps.dimensoes_processos.services.capacity._median_derivation_rows")
    @patch("apps.dimensoes_processos.services.capacity._approved_import_run")
    def test_planejamento_uses_median_derivation(
        self,
        approved_run,
        median_rows,
        projection,
        _filter_doc,
    ):
        from apps.dimensoes_processos.services.capacity import calculate_daily_capacity

        approved_run.return_value = SimpleNamespace(
            pk=1, reviewed_scan_id=2, period_from=date(2026, 4, 1),
            period_to=date(2026, 8, 31), finished_at=None, triggered_by=None,
        )
        median_rows.return_value = (
            date(2026, 8, 19),
            [
                SimpleNamespace(
                    cliente_id=1,
                    workflow_id=10,
                    etapa_id=5,
                    percentual=Decimal("50"),
                    cliente=SimpleNamespace(id=1, nome="Cliente", operations=True),
                    workflow=SimpleNamespace(id=10, nome="WF", ind_considerar=True),
                    etapa=SimpleNamespace(id=5, nome="Etapa"),
                )
            ],
            {
                "derivation_method": "median_60d",
                "derivation_window_from": "2026-06-21",
                "derivation_window_to": "2026-08-19",
                "derivation_sample_days": 3,
            },
        )
        projection.return_value = (
            {(1, 10): Decimal("100")},
            {(1, 10): {"id_cliente": 1, "cliente_nome": "Cliente", "id_workflow": 10, "workflow_nome": "WF", "nh_count": 1}},
            [],
            [],
        )

        with patch("apps.dimensoes_processos.services.capacity._active_metas") as metas:
            meta = SimpleNamespace(meta_dia=Decimal("20"), servico_id=None, servico=None, pk=1)
            metas.return_value = {5: [meta]}
            payload = calculate_daily_capacity(
                date(2026, 8, 20),
                include_details=False,
                include_hourly=False,
                include_dax=False,
                scenario_id="planejamento",
            )

        self.assertEqual(payload["scenario"]["derivation"]["mode"], "median_60d")
        self.assertEqual(payload["analysis"]["derivation_method"], "median_60d")
        self.assertEqual(payload["summary"]["agents_required"], 3)
        median_rows.assert_called_once()

    @patch("apps.dimensoes_processos.services.capacity._projection_by_workflow")
    @patch("apps.dimensoes_processos.services.capacity._reference_derivation_rows")
    @patch("apps.dimensoes_processos.services.capacity._approved_import_run")
    def test_manual_meta_dimensions_stage_without_catalog_meta(self, approved_run, reference_rows, projection):
        from apps.dimensoes_processos.services.capacity import calculate_daily_capacity

        approved_run.return_value = SimpleNamespace(
            pk=1, reviewed_scan_id=2, period_from=date(2026, 4, 1),
            period_to=date(2026, 8, 31), finished_at=None, triggered_by=None,
        )
        reference_rows.return_value = (
            date(2026, 8, 5),
            [SimpleNamespace(cliente_id=1, workflow_id=10, etapa_id=5, percentual=Decimal("50"), cliente=SimpleNamespace(id=1, nome="Cliente", operations=True), workflow=SimpleNamespace(id=10, nome="WF", ind_considerar=True), etapa=SimpleNamespace(id=5, nome="Análise Visual"))],
        )
        projection.return_value = (
            {(1, 10): Decimal("100")},
            {(1, 10): {"id_cliente": 1, "cliente_nome": "Cliente", "id_workflow": 10, "workflow_nome": "WF", "nh_count": 1}}, [], [],
        )
        with patch("apps.dimensoes_processos.services.capacity._active_metas", return_value={}):
            payload = calculate_daily_capacity(
                date(2026, 8, 20), include_details=False, include_hourly=False,
                include_dax=False, scenario_id="manual",
                manual_overrides={"metas": [{"id_etapa": 5, "meta_dia": "25"}]},
            )
        self.assertEqual(payload["results"][0]["status"], "dimensionada")
        self.assertEqual(payload["summary"]["exact_fte_before_rounding"], "2.0000")

    @patch("apps.dimensoes_processos.services.capacity._projection_by_workflow")
    @patch("apps.dimensoes_processos.services.capacity._reference_derivation_rows")
    @patch("apps.dimensoes_processos.services.capacity._approved_import_run")
    def test_manual_meta_overrides_ambiguous_catalog(self, approved_run, reference_rows, projection):
        from apps.dimensoes_processos.services.capacity import calculate_daily_capacity

        approved_run.return_value = SimpleNamespace(
            pk=1, reviewed_scan_id=2, period_from=date(2026, 4, 1),
            period_to=date(2026, 8, 31), finished_at=None, triggered_by=None,
        )
        reference_rows.return_value = (
            date(2026, 8, 5),
            [SimpleNamespace(cliente_id=1, workflow_id=10, etapa_id=5, percentual=Decimal("50"), cliente=SimpleNamespace(id=1, nome="Cliente", operations=True), workflow=SimpleNamespace(id=10, nome="WF", ind_considerar=True), etapa=SimpleNamespace(id=5, nome="Análise Visual"))],
        )
        projection.return_value = ({(1, 10): Decimal("100")}, {(1, 10): {"id_cliente": 1, "cliente_nome": "Cliente", "id_workflow": 10, "workflow_nome": "WF", "nh_count": 1}}, [], [])
        candidates = [
            SimpleNamespace(meta_dia=Decimal("20"), servico_id=None, servico=None, pk=1),
            SimpleNamespace(meta_dia=Decimal("30"), servico_id=None, servico=None, pk=2),
        ]
        with patch("apps.dimensoes_processos.services.capacity._active_metas", return_value={5: candidates}):
            payload = calculate_daily_capacity(
                date(2026, 8, 20), include_details=False, include_hourly=False,
                include_dax=False, scenario_id="manual",
                manual_overrides={"metas": [{"id_etapa": 5, "meta_dia": "25"}]},
            )
        self.assertEqual(payload["results"][0]["status"], "dimensionada")
        self.assertFalse(any(item["code"] == "meta_ambigua_ou_invalida" for item in payload["blockers"]))


class CapacityScenarioManualDerivationTests(TestCase):
    def test_manual_derivation_uses_full_reference_not_only_overridden_workflows(self):
        baseline_rows = [
            SimpleNamespace(cliente_id=1, workflow_id=10, etapa_id=5),
            SimpleNamespace(cliente_id=2, workflow_id=20, etapa_id=6),
        ]
        reference_date, rows = _manual_derivation_rows(
            date(2026, 8, 20),
            {
                "workflows": [
                    {"id_cliente": 1, "id_workflow": 10, "volume": "150"},
                ],
            },
            reference_resolver=lambda _on_date: (date(2026, 8, 5), baseline_rows),
        )
        self.assertEqual(reference_date, date(2026, 8, 5))
        self.assertEqual(len(rows), 2)

    def test_manual_derivation_overlays_one_row_and_preserves_the_rest(self):
        baseline_rows = [
            SimpleNamespace(cliente_id=1, workflow_id=10, etapa_id=5, percentual=Decimal("20"), cliente=SimpleNamespace(nome="C1"), workflow=SimpleNamespace(nome="W1"), etapa=SimpleNamespace(nome="Visual")),
            SimpleNamespace(cliente_id=2, workflow_id=20, etapa_id=6, percentual=Decimal("30"), cliente=SimpleNamespace(nome="C2"), workflow=SimpleNamespace(nome="W2"), etapa=SimpleNamespace(nome="Docs")),
        ]
        _, rows = _manual_derivation_rows(
            date(2026, 8, 20),
            {"derivations": [{"id_cliente": 1, "id_workflow": 10, "id_etapa": 5, "percentual": "25"}]},
            reference_resolver=lambda _on_date: (date(2026, 8, 5), baseline_rows),
        )
        by_key = {(row.cliente_id, row.workflow_id, row.etapa_id): row.percentual for row in rows}
        self.assertEqual(by_key[(1, 10, 5)], Decimal("25"))
        self.assertEqual(by_key[(2, 20, 6)], Decimal("30"))


class CapacityOperationVolumeTests(TestCase):
    def test_manual_percent_adjusts_each_daily_baseline_instead_of_freezing_volume(self):
        projection = lambda _date: (
            {(1, 10): Decimal("100")},
            {(1, 10): {"id_cliente": 1, "id_workflow": 10}},
            [], [],
        )
        totals, _, _, _ = resolve_workflow_volumes(
            date(2026, 8, 24), scenario_config("manual"),
            projection_resolver=projection,
            manual_overrides={"workflows": [{
                "id_cliente": 1, "id_workflow": 10, "percentual_variacao": "10",
            }]},
        )

        self.assertEqual(totals[(1, 10)], Decimal("110"))

    def test_consolidated_is_canonical_and_detail_is_fallback(self):
        target = date(2026, 8, 20)
        SlaUtilConsolidado.objects.create(
            data_cadastro=target,
            id_cliente=1,
            id_workflow=10,
            cliente_nome="C",
            workflow_nome="W",
            quantidade=100,
            date_key_cadastro=20260820,
        )
        SlaUtilDetalhe.objects.create(
            protocolo="p1",
            source_key="p1-10-1",
            data_cadastro=target,
            id_cliente=1,
            id_workflow=10,
            id_nh=1,
            cliente_nome="C",
            workflow_nome="W",
            date_key_cadastro=20260820,
        )
        SlaUtilDetalhe.objects.create(
            protocolo="p2",
            source_key="p2-20-1",
            data_cadastro=target,
            id_cliente=2,
            id_workflow=20,
            id_nh=1,
            cliente_nome="C2",
            workflow_nome="W2",
            date_key_cadastro=20260820,
        )
        totals, metadata = _received_by_workflow(target)
        self.assertEqual(totals[(1, 10)], Decimal("100"))
        self.assertEqual(totals[(2, 20)], Decimal("1"))
        self.assertEqual(metadata[(1, 10)]["cliente_nome"], "C")
        self.assertEqual(metadata[(2, 20)]["workflow_nome"], "W2")

    def test_received_by_workflow_enriches_names_from_catalog(self):
        target = date(2026, 8, 21)
        produto = DimProduto.objects.create(id_produto=50, tipo_produto="Documentoscopia")
        DimCliente.objects.create(id_cliente=501, nome="Cliente Catálogo")
        DimWorkflow.objects.create(
            id_workflow=502,
            nome="Workflow Catálogo",
            produto=produto,
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=target,
            id_cliente=501,
            id_workflow=502,
            cliente_nome="",
            workflow_nome="",
            quantidade=25,
            date_key_cadastro=20260821,
        )
        _, metadata = _received_by_workflow(target)
        self.assertEqual(metadata[(501, 502)]["cliente_nome"], "Cliente Catálogo")
        self.assertEqual(metadata[(501, 502)]["workflow_nome"], "Workflow Catálogo")


class CapacityOperationIntegrationTests(TestCase):
    def setUp(self):
        self.target_date = date(2026, 5, 25)
        self.reference_date = date(2026, 5, 24)
        self.client = DimCliente.objects.create(id_cliente=901, nome="Cliente OperaÃ§Ã£o")
        self.produto = DimProduto.objects.create(id_produto=90, tipo_produto="Documentoscopia")
        self.workflow = DimWorkflow.objects.create(
            id_workflow=902,
            nome="Workflow OperaÃ§Ã£o",
            produto=self.produto,
        )
        self.stage = DimEtapa.objects.create(id_etapa=903, nome="AnÃ¡lise Operacional")
        scan = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
            finished_at=timezone.now(),
        )
        self.import_run = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
            finished_at=timezone.now(),
            reviewed_scan=scan,
        )
        DerivacaoEtapaDiaria.objects.create(
            data=self.reference_date,
            cliente=self.client,
            workflow=self.workflow,
            etapa=self.stage,
            percentual=Decimal("100"),
            registros=40,
            import_run=self.import_run,
        )
        MetaEtapa.objects.create(
            data_inicio=date(2026, 5, 1),
            etapa=self.stage,
            meta_dia=Decimal("20"),
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=self.target_date,
            id_cliente=self.client.pk,
            id_workflow=self.workflow.pk,
            cliente_nome=self.client.nome,
            workflow_nome=self.workflow.nome,
            quantidade=40,
            date_key_cadastro=20260525,
        )

    def test_operacao_uses_real_received_fields_and_explicit_approved_fallback(self):
        from apps.dimensoes_processos.services.capacity import calculate_daily_capacity

        payload = calculate_daily_capacity(
            self.target_date,
            include_details=False,
            include_hourly=False,
            include_dax=False,
            scenario_id="operacao",
        )

        self.assertEqual(payload["scenario"]["id"], "operacao")
        self.assertEqual(
            payload["scenario"]["derivation"]["fallback_mode"],
            "latest_approved_reference",
        )
        self.assertTrue(payload["analysis"]["derivation_reused"])
        self.assertEqual(payload["analysis"]["reference_date"], self.reference_date.isoformat())
        self.assertEqual(payload["summary"]["projected_workflow_volume"], "40.00")
        self.assertEqual(payload["summary"]["exact_fte_before_rounding"], "2.0000")

    def test_operacao_payload_exposes_client_workflow_stage_names(self):
        from apps.dimensoes_processos.services.capacity import calculate_daily_capacity

        SlaUtilConsolidado.objects.filter(data_cadastro=self.target_date).update(
            cliente_nome="",
            workflow_nome="",
        )
        payload = calculate_daily_capacity(
            self.target_date,
            include_details=True,
            include_hourly=False,
            include_dax=False,
            scenario_id="operacao",
        )

        self.assertEqual(payload["by_client"][0]["cliente_nome"], self.client.nome)
        self.assertEqual(payload["by_workflow"][0]["workflow_nome"], self.workflow.nome)
        self.assertTrue(payload["by_workflow"][0]["stages"])
        self.assertEqual(
            payload["by_workflow"][0]["stages"][0]["etapa_nome"],
            self.stage.nome,
        )
        self.assertEqual(payload["breakdown"][0]["cliente_nome"], self.client.nome)
        self.assertEqual(payload["breakdown"][0]["workflow_nome"], self.workflow.nome)

    def test_operacao_prefers_exact_day_derivation(self):
        exact_stage = DimEtapa.objects.create(id_etapa=904, nome="Etapa Exata")
        MetaEtapa.objects.create(
            data_inicio=date(2026, 5, 1),
            etapa=exact_stage,
            meta_dia=Decimal("10"),
        )
        DerivacaoEtapaDiaria.objects.create(
            data=self.target_date,
            cliente=self.client,
            workflow=self.workflow,
            etapa=exact_stage,
            percentual=Decimal("100"),
            registros=40,
            import_run=self.import_run,
        )

        reference_date, rows = resolve_derivation(
            self.target_date,
            scenario_config("operacao"),
            reference_resolver=lambda _date: self.fail("fallback nÃ£o deveria ser chamado"),
        )

        self.assertEqual(reference_date, self.target_date)
        self.assertEqual([row.etapa_id for row in rows], [exact_stage.pk])


class PlanejamentoMedianIntegrationTests(TestCase):
    def setUp(self):
        self.target_date = date(2026, 8, 20)
        self.client = DimCliente.objects.create(id_cliente=801, nome="Cliente Doc")
        self.produto_doc = DimProduto.objects.create(id_produto=1, tipo_produto="Documentoscopia")
        self.produto_other = DimProduto.objects.create(id_produto=2, tipo_produto="Compliance")
        self.workflow_doc = DimWorkflow.objects.create(
            id_workflow=810,
            nome="WF Documentoscopia",
            produto=self.produto_doc,
        )
        self.workflow_other = DimWorkflow.objects.create(
            id_workflow=811,
            nome="WF Compliance",
            produto=self.produto_other,
        )
        self.stage = DimEtapa.objects.create(id_etapa=815, nome="Análise Visual")
        scan = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 6, 1),
            period_to=date(2026, 8, 31),
            finished_at=timezone.now(),
        )
        self.import_run = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 6, 1),
            period_to=date(2026, 8, 31),
            finished_at=timezone.now(),
            reviewed_scan=scan,
        )
        for day, pct in (
            (date(2026, 8, 10), Decimal("10")),
            (date(2026, 8, 15), Decimal("50")),
            (date(2026, 8, 18), Decimal("90")),
            (self.target_date, Decimal("99")),
        ):
            DerivacaoEtapaDiaria.objects.create(
                data=day,
                cliente=self.client,
                workflow=self.workflow_doc,
                etapa=self.stage,
                percentual=pct,
                registros=10,
                import_run=self.import_run,
            )
            DerivacaoEtapaDiaria.objects.create(
                data=day,
                cliente=self.client,
                workflow=self.workflow_other,
                etapa=self.stage,
                percentual=Decimal("100"),
                registros=10,
                import_run=self.import_run,
            )
        from apps.dimensoes_processos.models import DimNivelHierarquico, ProjecaoSla

        nh = DimNivelHierarquico.objects.create(id_nh=816, nome="NH")
        ProjecaoSla.objects.create(
            cliente=self.client,
            workflow=self.workflow_doc,
            nivel_hierarquico=nh,
            data_inicio=date(2026, 8, 1),
            dias_semana="{3}",
            volume=100,
        )
        ProjecaoSla.objects.create(
            cliente=self.client,
            workflow=self.workflow_other,
            nivel_hierarquico=nh,
            data_inicio=date(2026, 8, 1),
            dias_semana="{3}",
            volume=200,
        )
        MetaEtapa.objects.create(
            data_inicio=date(2026, 8, 1),
            etapa=self.stage,
            meta_dia=Decimal("20"),
        )

    def test_planejamento_uses_median_and_documentoscopia_scope(self):
        from apps.dimensoes_processos.services.capacity import calculate_daily_capacity

        payload = calculate_daily_capacity(
            self.target_date,
            include_details=True,
            include_hourly=False,
            include_dax=False,
            scenario_id="planejamento",
        )

        self.assertEqual(payload["analysis"]["derivation_method"], "median_60d")
        self.assertEqual(payload["summary"]["projected_workflow_volume"], "100.00")
        derivations = {row["percentual"] for row in payload["derivations"]}
        self.assertEqual(derivations, {"50.0000"})
        self.assertEqual(payload["summary"]["exact_fte_before_rounding"], "2.5000")


class CapacityManualParityIntegrationTests(TestCase):
    def setUp(self):
        self.target_date = date(2026, 5, 25)
        self.reference_date = date(2026, 5, 24)
        self.client = DimCliente.objects.create(id_cliente=911, nome="Cliente Manual")
        self.produto_doc = DimProduto.objects.create(id_produto=3, tipo_produto="Documentoscopia")
        self.workflow = DimWorkflow.objects.create(
            id_workflow=912,
            nome="Workflow Manual",
            produto=self.produto_doc,
        )
        self.stage = DimEtapa.objects.create(id_etapa=913, nome="Etapa Manual")
        from apps.dimensoes_processos.models import DimNivelHierarquico, ProjecaoSla

        nh = DimNivelHierarquico.objects.create(id_nh=914, nome="NH Manual")
        scan = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
            finished_at=timezone.now(),
        )
        run = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
            finished_at=timezone.now(),
            reviewed_scan=scan,
        )
        DerivacaoEtapaDiaria.objects.create(
            data=self.reference_date,
            cliente=self.client,
            workflow=self.workflow,
            etapa=self.stage,
            percentual=Decimal("100"),
            registros=100,
            import_run=run,
        )
        ProjecaoSla.objects.create(
            cliente=self.client,
            workflow=self.workflow,
            nivel_hierarquico=nh,
            data_inicio=date(2026, 5, 1),
            dias_semana="{0}",
            volume=100,
        )
        MetaEtapa.objects.create(
            data_inicio=date(2026, 5, 1),
            etapa=self.stage,
            meta_dia=Decimal("20"),
        )

    def test_manual_without_overrides_uses_reference_derivation(self):
        from apps.dimensoes_processos.services.capacity import calculate_daily_capacity

        common = {
            "include_details": False,
            "include_hourly": False,
            "include_dax": False,
        }
        planning = calculate_daily_capacity(
            self.target_date,
            scenario_id="planejamento",
            **common,
        )
        manual = calculate_daily_capacity(
            self.target_date,
            scenario_id="manual",
            manual_overrides={},
            **common,
        )

        self.assertEqual(planning["analysis"]["derivation_method"], "median_60d")
        self.assertNotIn("derivation_method", manual["analysis"])
        self.assertEqual(manual["summary"], planning["summary"])


class CapacityOverrideValidationTests(TestCase):
    def test_accepts_workflow_percent_adjustment(self):
        request = SimpleNamespace(
            query_params={"scenario": "manual"},
            data={"overrides": {"workflows": [{
                "id_cliente": 1, "id_workflow": 10, "percentual_variacao": "10",
            }]}},
        )

        _, parsed = _scenario_from_request(request)

        self.assertEqual(parsed["workflows"][0]["percentual_variacao"], "10")

    def test_accepts_overrides_from_post_body(self):
        overrides = {"metas": [{"id_etapa": 1, "meta_dia": "25"}]}
        request = SimpleNamespace(
            query_params={"scenario": "manual"},
            data={"overrides": overrides},
        )

        scenario_id, parsed = _scenario_from_request(request)

        self.assertEqual(scenario_id, "manual")
        self.assertEqual(parsed, overrides)

    def test_rejects_non_finite_values(self):
        for raw in ("NaN", "Infinity", "-Infinity"):
            request = SimpleNamespace(query_params={
                "scenario": "manual",
                "overrides": '{"metas":[{"id_etapa":1,"meta_dia":"%s"}]}' % raw,
            })
            with self.subTest(raw=raw), self.assertRaisesMessage(ValueError, "valor ou chave invalida"):
                _scenario_from_request(request)
