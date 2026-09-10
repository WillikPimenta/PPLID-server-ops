# -*- coding: utf-8 -*-
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import (
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DimCliente,
    DimEtapa,
    DimWorkflow,
)
from apps.dimensoes_processos.services.derivacao_etapa.sync import import_derivacao_etapa_csv

User = get_user_model()
BASE = "/api/v1/dimensoes-processos/derivacao-etapa"


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class DerivacaoEtapaApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="megazord_derivacao", password="x", email="megazord_derivacao@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        DimCliente.objects.create(id_cliente=100, nome="Cliente Teste")
        DimWorkflow.objects.create(id_workflow=200, nome="Workflow Teste")
        DimEtapa.objects.create(id_etapa=300, nome="Etapa Teste")

        self.tmp = tempfile.TemporaryDirectory()
        self.csv_dir = Path(self.tmp.name)
        (self.csv_dir / "FINALIZADO_20260501.csv").write_text(
            "Data;Cliente;WF;Etapas;Registros;Percentual\n"
            "01/05/2026;Cliente Teste;Workflow Teste;Etapa Teste;10;100,00\n",
            encoding="latin-1",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_status_empty(self):
        res = self.client.get(f"{BASE}/import-status/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_rows"], 0)

    def test_diaria_list_after_import(self):
        import_derivacao_etapa_csv(directory=self.csv_dir)
        res = self.client.get(f"{BASE}/diaria/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 1)

    @override_settings(DERIVACAO_ETAPA_PURGE_SYNC=True)
    def test_purge_clears_derivacao_base(self):
        import_derivacao_etapa_csv(directory=self.csv_dir)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 1)

        denied = self.client.post(f"{BASE}/purge/", {"confirm": "NAO"}, format="json")
        self.assertEqual(denied.status_code, 400)

        res = self.client.post(f"{BASE}/purge/", {"confirm": "APAGAR"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])
        self.assertEqual(res.data["status"], DerivacaoEtapaImportRun.STATUS_OK)
        self.assertEqual(res.data["deleted"]["diaria"], 1)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 0)
        self.assertEqual(
            DerivacaoEtapaImportRun.objects.filter(
                run_kind__in=(
                    DerivacaoEtapaImportRun.KIND_SCAN,
                    DerivacaoEtapaImportRun.KIND_IMPORT,
                )
            ).count(),
            0,
        )
        self.assertEqual(
            DerivacaoEtapaImportRun.objects.filter(
                run_kind=DerivacaoEtapaImportRun.KIND_PURGE,
                status=DerivacaoEtapaImportRun.STATUS_OK,
            ).count(),
            1,
        )

        status = self.client.get(f"{BASE}/import-status/")
        self.assertEqual(status.data["total_rows"], 0)

    @override_settings(DERIVACAO_ETAPA_IMPORT_STALE_MINUTES=15, DERIVACAO_ETAPA_PURGE_SYNC=True)
    def test_purge_recovers_stale_running_scan_before_delete(self):
        import_derivacao_etapa_csv(directory=self.csv_dir)
        stale = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_RUNNING,
        )
        DerivacaoEtapaImportRun.objects.filter(pk=stale.pk).update(
            started_at=timezone.now() - timedelta(minutes=30),
        )
        res = self.client.post(f"{BASE}/purge/", {"confirm": "APAGAR"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(res.data["metrics"]["stale_recovered"], 1)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 0)

    @override_settings(DERIVACAO_ETAPA_PURGE_SYNC=True)
    def test_purge_blocked_while_scan_import_active(self):
        DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_RUNNING,
        )
        res = self.client.post(f"{BASE}/purge/", {"confirm": "APAGAR"}, format="json")
        self.assertEqual(res.status_code, 409)

    @override_settings(DERIVACAO_ETAPA_PURGE_SYNC=True)
    def test_purge_run_detail_returns_progress_metrics(self):
        import_derivacao_etapa_csv(directory=self.csv_dir)
        res = self.client.post(f"{BASE}/purge/", {"confirm": "APAGAR"}, format="json")
        self.assertEqual(res.status_code, 200)
        run_id = res.data["run_id"]
        poll = self.client.get(f"{BASE}/purge/runs/{run_id}/")
        self.assertEqual(poll.status_code, 200)
        self.assertEqual(poll.data["status"], DerivacaoEtapaImportRun.STATUS_OK)
        self.assertEqual(poll.data["metrics"]["phase"], "done")
        self.assertEqual(poll.data["deleted"]["diaria"], 1)

    def test_post_import_uses_default_dir_setting(self):
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            scan = self.client.post(
                f"{BASE}/scan/",
                {"from_date": "2026-05-01", "to_date": "2026-05-01"},
            )
            res = self.client.post(f"{BASE}/import/", {"scan_run_id": scan.data["run_id"]})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])
        self.assertEqual(res.data["scan_run_id"], scan.data["run_id"])
        self.assertEqual(
            DerivacaoEtapaImportRun.objects.get(pk=res.data["run_id"]).reviewed_scan_id,
            scan.data["run_id"],
        )

    def test_post_scan(self):
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            res = self.client.post(
                f"{BASE}/scan/",
                {"from_date": "2026-05-01", "to_date": "2026-05-01"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])
        self.assertIn("resumo", res.data)
        run = DerivacaoEtapaImportRun.objects.get(pk=res.data["run_id"])
        self.assertEqual(run.period_from.isoformat(), "2026-05-01")
        self.assertEqual(run.period_to.isoformat(), "2026-05-01")
        self.assertTrue(run.source_fingerprint)

    def test_comparativo_resumo(self):
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            self.client.post(
                f"{BASE}/scan/",
                {"from_date": "2026-05-01", "to_date": "2026-05-01"},
            )
        res = self.client.get(f"{BASE}/comparativo/resumo/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("dimensoes", res.data)
        self.assertEqual(res.data["dimensoes"]["cliente"]["registros_total"], 10)
        self.assertEqual(res.data["dimensoes"]["cliente"]["taxa_registros_ok_pct"], 100.0)

    def test_comparativo_pending_filters_and_orders_operational_queue(self):
        (self.csv_dir / "FINALIZADO_20260501.csv").write_text(
            "Data;Cliente;WF;Etapas;Registros;Percentual\n"
            "01/05/2026;Cliente Teste;Workflow Teste;Etapa Teste;10;50,00\n"
            "01/05/2026;Cliente Desconhecido;Workflow Desconhecido;Etapa Desconhecida;900;50,00\n",
            encoding="latin-1",
        )
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            scan = self.client.post(
                f"{BASE}/scan/",
                {"from_date": "2026-05-01", "to_date": "2026-05-01"},
            )
        res = self.client.get(
            f"{BASE}/comparativo/",
            {"scan_run": scan.data["run_id"], "dimensao": "cliente", "status": "pending"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(res.data["results"][0]["registros_total"], 900)
        self.assertEqual(res.data["results"][0]["status"], "unmatched")
        self.assertIn("motivo", res.data["results"][0])

    def test_import_requires_reviewed_scan(self):
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            res = self.client.post(f"{BASE}/import/", {})
        self.assertEqual(res.status_code, 400)

    def test_import_rejects_source_changed_after_scan(self):
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            scan = self.client.post(
                f"{BASE}/scan/",
                {"from_date": "2026-05-01", "to_date": "2026-05-01"},
            )
            with (self.csv_dir / "FINALIZADO_20260501.csv").open("a", encoding="latin-1") as fh:
                fh.write("\n")
            res = self.client.post(f"{BASE}/import/", {"scan_run_id": scan.data["run_id"]})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 0)

    def test_scan_rejects_inverted_period(self):
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            res = self.client.post(
                f"{BASE}/scan/",
                {"from_date": "2026-05-02", "to_date": "2026-05-01"},
            )
        self.assertEqual(res.status_code, 400)

    def test_import_rejects_association_changed_after_scan(self):
        with self.settings(DERIVACAO_ETAPA_CSV_DIR=str(self.csv_dir)):
            scan = self.client.post(
                f"{BASE}/scan/",
                {"from_date": "2026-05-01", "to_date": "2026-05-01"},
            )
            alias = self.client.post(
                f"{BASE}/aliases/",
                {"dimensao": "cliente", "nome_origem": "Cliente Teste", "id_cliente": 100},
            )
            self.assertEqual(alias.status_code, 201)
            res = self.client.post(f"{BASE}/import/", {"scan_run_id": scan.data["run_id"]})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 0)

    def test_deny_without_perm(self):
        other = User.objects.create_user(
            username="no_megazord", password="x", email="no_megazord@example.com"
        )
        c = APIClient()
        c.force_authenticate(user=other)
        res = c.get(f"{BASE}/import-status/")
        self.assertEqual(res.status_code, 403)

    def _upload_batch(self):
        csv_path = self.csv_dir / "FINALIZADO_20260501.csv"
        with csv_path.open("rb") as fh:
            res = self.client.post(
                f"{BASE}/uploads/",
                {"files": fh},
                format="multipart",
            )
        self.assertEqual(res.status_code, 201)
        return res.data["batch"]["token"]

    def test_upload_scan_import_flow(self):
        token = self._upload_batch()
        scan = self.client.post(
            f"{BASE}/scan/",
            {
                "from_date": "2026-05-01",
                "to_date": "2026-05-01",
                "upload_batch_id": token,
            },
        )
        self.assertEqual(scan.status_code, 200)
        self.assertTrue(scan.data["ok"])
        scan_run = DerivacaoEtapaImportRun.objects.get(pk=scan.data["run_id"])
        self.assertEqual(scan_run.metrics.get("upload_batch_id"), token)

        res = self.client.post(f"{BASE}/import/", {"scan_run_id": scan.data["run_id"]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 1)

    def test_patch_diaria_manual_override(self):
        import_derivacao_etapa_csv(directory=self.csv_dir)
        row = DerivacaoEtapaDiaria.objects.first()
        res = self.client.patch(
            f"{BASE}/diaria/{row.pk}/",
            {"registros": 99, "override_notas": "Correção manual"},
        )
        self.assertEqual(res.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.registros, 99)
        self.assertTrue(row.manual_override)
        self.assertEqual(row.override_notas, "Correção manual")

    def test_patch_and_delete_alias(self):
        created = self.client.post(
            f"{BASE}/aliases/",
            {"dimensao": "cliente", "nome_origem": "Alias Teste", "id_cliente": 100},
        )
        self.assertEqual(created.status_code, 201)
        alias_id = created.data["id"]
        patched = self.client.patch(
            f"{BASE}/aliases/",
            {"id": alias_id, "notas": "Atualizado", "id_cliente": 100},
        )
        self.assertEqual(patched.status_code, 200)
        deleted = self.client.delete(f"{BASE}/aliases/", {"id": alias_id}, format="json")
        self.assertEqual(deleted.status_code, 200)
        list_res = self.client.get(f"{BASE}/aliases/", {"search": "Alias Teste"})
        self.assertEqual(list_res.data["count"], 0)
