from datetime import date, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access import registry as R
from apps.replicacao_d1.models import ReplicacaoD1PlanDeletion, ReplicacaoD1Run
from apps.replicacao_d1.services.plan_validation import (
    PlanImmutableError,
    PlanNotApprovedError,
    build_plan_validation,
    build_plan_warning_groups,
    ensure_plan_approved,
    persist_plan,
    review_plan,
)
from apps.replicacao_d1.services.source_batch import ingest_source_rows
from apps.replicacao_d1.services.source_batch import source_batch_from_rotina
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

User = get_user_model()


def _rows():
    return [
        {"Protocolo": "001", "Workflow": "WF A", "Data da Análise": "2026-08-09T09:00:00", "Matrícula": 0},
        {"Protocolo": "002", "Workflow": "WF A", "Data da Análise": "2026-08-09T10:00:00", "Matrícula": 1},
    ]


def _persist(run_id="20260810_120000"):
    batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
    return persist_plan(
        run_id=run_id,
        data_referencia_d1=date(2026, 8, 9),
        source_batch_id=batch.pk,
        config_hash="c" * 64,
        workflows=[{
            "workflow_config": "WF A",
            "workflow_d1": "WF A",
            "cliente": "Cliente A",
            "fila": "BRFLOW",
            "amostra_efetiva": 2,
            "disponivel_d1": 2,
            "protocolos_manuais": 1,
            "protocolos_automaticos": 1,
            "protocolos_retroativos": 1,
        }],
        protocolos=[
            {"protocolo": "001", "workflow_config": "WF A", "matricula_tipo": "manual", "selection_reason": "amostra_d1"},
            {"protocolo": "002", "workflow_config": "WF A", "matricula_tipo": "automatico", "selection_reason": "retroativo:2026-08-07"},
        ],
    )


class SourceAndPlanServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("approver", email="approver@test.local", password="x")

    def test_plan_summary_counts_retroactive_protocols(self):
        run = _persist()
        self.assertEqual(run.plan_summary.get("protocolos_retroativos"), 1)

    def test_persist_plan_normalizes_naive_data_analise(self):
        import warnings

        batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
        naive = datetime(2026, 8, 3, 8, 49, 30)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            run = persist_plan(
                run_id="20260811_120000",
                data_referencia_d1=date(2026, 8, 9),
                source_batch_id=batch.pk,
                config_hash="d" * 64,
                workflows=[{
                    "workflow_config": "WF A",
                    "workflow_d1": "WF A",
                    "cliente": "Cliente A",
                    "fila": "BRFLOW",
                    "amostra_efetiva": 1,
                    "disponivel_d1": 1,
                }],
                protocolos=[{
                    "protocolo": "777",
                    "workflow_config": "WF A",
                    "matricula_tipo": "manual",
                    "selection_reason": "retroativo:2026-08-03",
                    "data_analise": naive,
                }],
            )
        naive_warnings = [
            w for w in caught
            if issubclass(w.category, RuntimeWarning) and "naive datetime" in str(w.message)
        ]
        self.assertEqual(naive_warnings, [])
        proto = run.protocolos.get(protocolo="777")
        self.assertIsNotNone(proto.data_analise)
        self.assertTrue(timezone.is_aware(proto.data_analise))
        self.assertEqual(
            timezone.localtime(proto.data_analise).replace(tzinfo=None),
            naive,
        )

    def test_persist_plan_dedupes_duplicate_protocolo_across_workflows(self):
        batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
        run = persist_plan(
            run_id="20260813_225408",
            data_referencia_d1=date(2026, 8, 9),
            source_batch_id=batch.pk,
            config_hash="e" * 64,
            workflows=[
                {
                    "workflow_config": "WF A",
                    "workflow_d1": "WF A",
                    "cliente": "Cliente A",
                    "fila": "BRFLOW",
                    "amostra_efetiva": 1,
                    "disponivel_d1": 1,
                },
                {
                    "workflow_config": "WF B",
                    "workflow_d1": "WF B",
                    "cliente": "Cliente B",
                    "fila": "BRFLOW",
                    "amostra_efetiva": 1,
                    "disponivel_d1": 1,
                },
            ],
            protocolos=[
                {
                    "protocolo": "11743",
                    "protocolo_normalizado": "11743",
                    "workflow_config": "WF A",
                    "matricula_tipo": "manual",
                    "selection_reason": "amostra_d1",
                },
                {
                    "protocolo": "11743",
                    "protocolo_normalizado": "11743",
                    "workflow_config": "WF B",
                    "matricula_tipo": "manual",
                    "selection_reason": "retroativo:2026-08-03",
                },
            ],
        )
        self.assertEqual(run.protocolos.count(), 1)
        self.assertEqual(run.protocolos.get().workflow_config, "WF A")

    def test_source_is_idempotent_and_plan_is_immutable(self):
        first = ingest_source_rows(date(2026, 8, 9), _rows())
        second = ingest_source_rows(date(2026, 8, 9), _rows())
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.batch.pk, second.batch.pk)
        run = _persist()
        self.assertEqual(run.validation_status, ReplicacaoD1Run.VALIDATION_PENDING)
        self.assertEqual(run.protocolos.count(), 2)
        with self.assertRaises(PlanImmutableError):
            persist_plan(
                run_id=run.run_id,
                data_referencia_d1=run.data_referencia_d1,
                source_batch_id=run.source_batch_id,
                config_hash=run.config_hash,
                workflows=[{"workflow_config": "WF B"}],
                protocolos=[{"protocolo": "999", "workflow_config": "WF B"}],
            )

    def test_source_classifies_raw_corporate_matricula_like_rotina(self):
        rows = [
            {"Protocolo": "101", "Workflow": "WF A", "Data da Análise": "2026-08-09T09:00:00", "Matrícula": "A12345C@br.experian.com.br"},
            {"Protocolo": "102", "Workflow": "WF A", "Data da Análise": "2026-08-09T10:00:00", "Matrícula": "ET12345"},
            {"Protocolo": "103", "Workflow": "WF A", "Data da Análise": "2026-08-09T11:00:00", "Matrícula": "robot-service"},
            {"Protocolo": "104", "Workflow": "WF A", "Data da Análise": "2026-08-09T12:00:00", "Matrícula": None},
            {"Protocolo": "105", "Workflow": "WF A", "Data de An�lise": "2026-08-09T13:00:00", "matr�cula": "C12345Q"},
        ]
        batch = ingest_source_rows(date(2026, 8, 9), rows).batch
        kinds = list(batch.registros.order_by("source_row_number").values_list("matricula_tipo", flat=True))
        self.assertEqual(kinds, ["manual", "manual", "automatico", "automatico", "manual"])

    def test_source_batch_is_materialized_from_rotina_detalhado(self):
        RotinaDetalhadoBrutoRecord.objects.bulk_create([
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 9),
                protocolo=101,
                workflow="WF A",
                data_analise=date(2026, 8, 9),
                matricula="C12345Q",
            ),
            RotinaDetalhadoBrutoRecord(
                report_date=date(2026, 8, 9),
                protocolo=102,
                workflow="WF A",
                data_analise=date(2026, 8, 9),
                matricula="robot-service",
            ),
        ])

        first = source_batch_from_rotina(date(2026, 8, 9))
        second = source_batch_from_rotina(date(2026, 8, 9))

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.batch.pk, second.batch.pk)
        self.assertEqual(first.batch.rows_valid, 2)
        self.assertEqual(
            list(first.batch.registros.values_list("matricula_tipo", flat=True)),
            ["manual", "automatico"],
        )

    def test_source_batch_reports_missing_rotina_partition(self):
        with self.assertRaisesMessage(
            RotinaDetalhadoBrutoRecord.DoesNotExist,
            "Nenhum registro D-1 encontrado em rotina_detalhado_bruto_record para 2026-08-09.",
        ):
            source_batch_from_rotina(date(2026, 8, 9))

    def test_execution_is_blocked_until_approval(self):
        run = _persist()
        with self.assertRaises(PlanNotApprovedError):
            ensure_plan_approved(run.run_id)
        review_plan(
            run.run_id,
            action="approve",
            user=self.user,
            expected_plan_hash=run.plan_hash,
            expected_revision=run.plan_revision,
        )
        self.assertEqual(ensure_plan_approved(run.run_id).validation_status, "approved")

    def test_rejection_requires_reason(self):
        run = _persist()
        with self.assertRaises(ValueError):
            review_plan(
                run.run_id,
                action="reject",
                user=self.user,
                expected_plan_hash=run.plan_hash,
                expected_revision=run.plan_revision,
            )

    def test_persist_plan_ignores_workflow_without_selected_protocols(self):
        batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
        run = persist_plan(
            run_id="20260810_125959",
            data_referencia_d1=date(2026, 8, 9),
            source_batch_id=batch.pk,
            config_hash="d" * 64,
            workflows=[
                {"workflow_config": "WF A", "amostra_efetiva": 1},
                {"workflow_config": "WF SEM PROTOCOLO", "amostra_efetiva": 0},
            ],
            protocolos=[{"protocolo": "001", "workflow_config": "WF A"}],
        )
        self.assertEqual(run.workflows_total, 1)
        self.assertEqual(list(run.workflows.values_list("workflow_config", flat=True)), ["WF A"])

    def test_persist_plan_qtd_only_bio_without_protocols(self):
        batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
        run = persist_plan(
            run_id="20260810_130001",
            data_referencia_d1=date(2026, 8, 9),
            source_batch_id=batch.pk,
            config_hash="e" * 64,
            workflows=[
                {
                    "workflow_config": "WF Bio",
                    "workflow_d1": "WF Bio D1",
                    "cliente": "Cliente A",
                    "fila": "Bio",
                    "amostra_efetiva": 42,
                    "protocolos_planejados": 42,
                    "disponivel_d1": 100,
                }
            ],
            protocolos=[],
        )
        self.assertEqual(run.workflows_total, 1)
        self.assertEqual(run.protocolos_total, 42)
        self.assertEqual(run.protocolos.count(), 0)
        wf = run.workflows.get(workflow_config="WF Bio")
        self.assertEqual(wf.protocolos_planejados, 42)
        self.assertEqual(wf.fila, "Bio")

    def test_persist_plan_misto_obedece_modo_explicito_independente_da_fila(self):
        batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
        run = persist_plan(
            run_id="20260810_130004",
            data_referencia_d1=date(2026, 8, 9),
            source_batch_id=batch.pk,
            config_hash="h" * 64,
            workflows=[
                {
                    "workflow_config": "WF G qtd",
                    "fila": "G auditoria",
                    "modo_replicacao": "qtd",
                    "amostra_efetiva": 42,
                    "protocolos_planejados": 42,
                },
                {
                    "workflow_config": "WF Bio CSV",
                    "fila": "Bio",
                    "modo_replicacao": "protocolos",
                    "amostra_efetiva": 1,
                    "protocolos_planejados": 1,
                },
            ],
            protocolos=[
                {"protocolo": "001", "workflow_config": "WF Bio CSV"},
                {"protocolo": "002", "workflow_config": "WF G qtd"},
            ],
        )

        self.assertEqual(run.protocolos_total, 43)
        self.assertEqual(
            list(run.protocolos.values_list("workflow_config", "protocolo")),
            [("WF Bio CSV", "001")],
        )
        validation = build_plan_validation(run.run_id)
        modos = {
            row["workflow_config"]: row["modo_replicacao"]
            for row in validation["workflows"]
        }
        self.assertEqual(modos, {"WF Bio CSV": "protocolos", "WF G qtd": "qtd"})
        self.assertFalse(validation["validation_issues"])

    def test_build_plan_validation_accepts_qtd_workflow_without_protocol_rows(self):
        batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
        run = persist_plan(
            run_id="20260810_130002",
            data_referencia_d1=date(2026, 8, 9),
            source_batch_id=batch.pk,
            config_hash="f" * 64,
            workflows=[
                {
                    "workflow_config": "WF Redoc",
                    "workflow_d1": "WF Redoc D1",
                    "cliente": "Cliente A",
                    "fila": "Redoc",
                    "amostra_efetiva": 15,
                    "protocolos_planejados": 15,
                    "disponivel_d1": 50,
                }
            ],
            protocolos=[],
        )
        validation = build_plan_validation(run.run_id)
        issues = validation.get("validation_issues") or []
        self.assertFalse(
            any("workflows diverge" in issue.lower() for issue in issues),
            msg=str(issues),
        )
        self.assertFalse(
            any("diverge do total" in issue.lower() for issue in issues),
            msg=str(issues),
        )
        self.assertEqual(validation["kpis"]["protocolos_total"], 15)
        self.assertEqual(validation["run"]["protocolos_total"], 15)

    def test_build_plan_validation_repairs_stale_qtd_total(self):
        batch = ingest_source_rows(date(2026, 8, 9), _rows()).batch
        run = persist_plan(
            run_id="20260810_130003",
            data_referencia_d1=date(2026, 8, 9),
            source_batch_id=batch.pk,
            config_hash="g" * 64,
            workflows=[
                {
                    "workflow_config": "WF Bio",
                    "workflow_d1": "WF Bio D1",
                    "cliente": "Cliente A",
                    "fila": "Bio",
                    "amostra_efetiva": 1037,
                    "protocolos_planejados": 1037,
                    "disponivel_d1": 2551,
                },
                {
                    "workflow_config": "WF Redoc",
                    "workflow_d1": "WF Redoc D1",
                    "cliente": "Cliente A",
                    "fila": "Redoc",
                    "amostra_efetiva": 1228,
                    "protocolos_planejados": 1228,
                    "disponivel_d1": 7645,
                },
            ],
            protocolos=[],
        )
        ReplicacaoD1Run.objects.filter(pk=run.pk).update(protocolos_total=0, plan_summary={"protocolos_total": 0})
        run.refresh_from_db()
        validation = build_plan_validation(run.run_id)
        run.refresh_from_db()
        self.assertEqual(run.protocolos_total, 2265)
        self.assertFalse(validation.get("validation_issues"))
        self.assertEqual(validation["kpis"]["protocolos_total"], 2265)

    def test_warning_groups_hide_no_data_noise(self):
        groups = build_plan_warning_groups(
            [
                "WF A: sem registros no parquet D-1; CSV não gerado",
                "WF B: sem amostra calculada; CSV vazio para limpeza BRFlow",
                "2 workflow(s) com registros D-1 ainda sem cadastro foram excluídos do plano e registrados como pendentes.",
                "Cliente A: sem limite mensal de balanceamento",
            ]
        )
        self.assertEqual([group["key"] for group in groups], ["cadastro", "metas"])


@override_settings(ACCESS_ENFORCEMENT=True)
class PlanValidationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.reader = User.objects.create_user("reader", email="reader@test.local", password="x")
        self.configurator = User.objects.create_user("configurator", email="configurator@test.local", password="x")
        self.approver = User.objects.create_user("approver_api", email="approver-api@test.local", password="x")
        self.run = _persist("20260810_130000")

    @staticmethod
    def _permissions(user, code, **_kwargs):
        if code == R.PLANEJAMENTO_AUTOMACAO_VIEW:
            return True
        if user.username == "approver_api" and code == R.PLANEJAMENTO_AUTOMACAO_APPROVE:
            return True
        return user.username == "configurator" and code == R.PLANEJAMENTO_AUTOMACAO_CONFIGURE

    @patch("apps.access.permissions.user_has_permission", side_effect=_permissions)
    def test_reader_can_validate_but_cannot_approve(self, _mock):
        self.client.force_authenticate(self.reader)
        detail = self.client.get(f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/validation/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["kpis"]["protocolos_total"], 2)
        approve = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/approve/",
            {"plan_hash": self.run.plan_hash, "plan_revision": 1},
            format="json",
        )
        self.assertEqual(approve.status_code, 403)
        delete = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/delete/",
            {
                "plan_hash": self.run.plan_hash,
                "plan_revision": 1,
                "confirmation_run_id": self.run.run_id,
                "reason": "Plano gerado incorretamente",
            },
            format="json",
        )
        self.assertEqual(delete.status_code, 403)

    @patch("apps.access.permissions.user_has_permission", side_effect=_permissions)
    def test_protocol_list_applies_ordering_before_pagination(self, _mock):
        self.client.force_authenticate(self.reader)
        url = f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/protocolos/"

        descending = self.client.get(url, {"ordering": "-protocolo", "page_size": 1})
        self.assertEqual(descending.status_code, 200)
        self.assertEqual(descending.data["results"][0]["protocolo"], "002")

        ascending = self.client.get(url, {"ordering": "matricula_tipo"})
        self.assertEqual(ascending.status_code, 200)
        self.assertEqual(
            [item["matricula_tipo"] for item in ascending.data["results"]],
            ["automatico", "manual"],
        )

    @patch("apps.access.permissions.user_has_permission", side_effect=_permissions)
    def test_approver_can_approve_with_optimistic_lock(self, _mock):
        self.client.force_authenticate(self.approver)
        response = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/approve/",
            {"plan_hash": self.run.plan_hash, "plan_revision": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["validation_status"], "approved")
        repeated = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/approve/",
            {"plan_hash": self.run.plan_hash, "plan_revision": 1},
            format="json",
        )
        self.assertEqual(repeated.status_code, 409)

    @patch("apps.access.permissions.user_has_permission", side_effect=_permissions)
    def test_approver_can_archive_unexecuted_plan_with_audit(self, _mock):
        self.client.force_authenticate(self.approver)
        source_batch_id = self.run.source_batch_id
        response = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/delete/",
            {
                "plan_hash": self.run.plan_hash,
                "plan_revision": self.run.plan_revision,
                "confirmation_run_id": self.run.run_id,
                "reason": "Plano gerado com parâmetros incorretos",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["deleted"])
        self.assertEqual(response.data["deletion_mode"], "archived")
        self.assertTrue(ReplicacaoD1Run.objects.filter(run_id=self.run.run_id).exists())
        self.assertEqual(self.run.protocolos.count(), 2)
        deletion = ReplicacaoD1PlanDeletion.objects.get(run_id=self.run.run_id)
        self.assertEqual(deletion.deleted_by, self.approver)
        self.assertEqual(deletion.protocolos_total, 2)
        self.assertEqual(deletion.workflows_total, 1)
        self.assertEqual(deletion.source_batch_id, source_batch_id)
        self.assertTrue(deletion.audit_snapshot["operational_data_preserved"])
        plans = self.client.get("/api/v1/replicacao-d1/config/plans/")
        self.assertEqual(plans.status_code, 200)
        self.assertFalse(any(item["run_id"] == self.run.run_id for item in plans.data["results"]))
        detail = self.client.get(f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/validation/")
        self.assertEqual(detail.status_code, 404)
        with self.assertRaises(PlanNotApprovedError):
            ensure_plan_approved(self.run.run_id)
        with self.assertRaises(PlanImmutableError):
            _persist(self.run.run_id)

    @patch("apps.access.permissions.user_has_permission", side_effect=_permissions)
    def test_configurator_can_archive_superseded_running_plan(self, _mock):
        ReplicacaoD1Run.objects.filter(pk=self.run.pk).update(
            status_canonical=ReplicacaoD1Run.STATUS_RUNNING,
            validation_status=ReplicacaoD1Run.VALIDATION_SUPERSEDED,
            started_at=timezone.now(),
        )
        self.run.refresh_from_db()
        self.client.force_authenticate(self.configurator)
        detail = self.client.get(f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/validation/")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["can_delete"])
        response = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/delete/",
            {
                "plan_hash": self.run.plan_hash,
                "plan_revision": self.run.plan_revision,
                "confirmation_run_id": self.run.run_id,
                "reason": "Plano antigo já substituído",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ReplicacaoD1PlanDeletion.objects.filter(run_id=self.run.run_id).exists())

    @patch("apps.access.permissions.user_has_permission", side_effect=_permissions)
    def test_approved_plan_cannot_be_deleted(self, _mock):
        self.client.force_authenticate(self.approver)
        approve = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/approve/",
            {"plan_hash": self.run.plan_hash, "plan_revision": self.run.plan_revision},
            format="json",
        )
        self.assertEqual(approve.status_code, 200)
        response = self.client.post(
            f"/api/v1/replicacao-d1/config/plans/{self.run.run_id}/delete/",
            {
                "plan_hash": self.run.plan_hash,
                "plan_revision": self.run.plan_revision,
                "confirmation_run_id": self.run.run_id,
                "reason": "Tentativa indevida",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(ReplicacaoD1Run.objects.filter(run_id=self.run.run_id).exists())
        self.assertFalse(ReplicacaoD1PlanDeletion.objects.filter(run_id=self.run.run_id).exists())
