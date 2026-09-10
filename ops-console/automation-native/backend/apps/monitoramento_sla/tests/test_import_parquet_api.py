# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.monitoramento_sla.models import SlaUtilConsolidado
from apps.monitoramento_sla.services.import_consolidado_parquet import (
    build_month_plan,
    import_consolidado_parquet_months,
    month_stats_from_parquet,
)

User = get_user_model()


def _write_parquet(path: Path, rows: list[dict]) -> None:
    import duckdb

    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE t AS SELECT * FROM (
          SELECT
            CAST(NULL AS TIMESTAMP) AS data_cadastro,
            CAST(NULL AS INTEGER) AS id_cliente,
            CAST(NULL AS INTEGER) AS id_workflow,
            CAST(NULL AS INTEGER) AS id_nivel_hierarquico,
            CAST(NULL AS BIGINT) AS quantidade,
            CAST(NULL AS VARCHAR) AS sla_descricao_natural,
            CAST(NULL AS VARCHAR) AS sla_descricao_desvio_volume
        ) WHERE 1=0
        """
    )
    for row in rows:
        con.execute(
            """
            INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["data_cadastro"],
                row["id_cliente"],
                row["id_workflow"],
                row["id_nivel_hierarquico"],
                row["quantidade"],
                row["sla_descricao_natural"],
                row.get("sla_descricao_desvio_volume", "Dentro"),
            ],
        )
    con.execute(f"COPY t TO '{path.as_posix()}' (FORMAT PARQUET)")


class ImportConsolidadoParquetMonthTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parquet_path = Path(self.tmp.name) / "sample.parquet"
        _write_parquet(
            self.parquet_path,
            [
                {
                    "data_cadastro": "2026-01-15",
                    "id_cliente": 1,
                    "id_workflow": 10,
                    "id_nivel_hierarquico": 100,
                    "quantidade": 5,
                    "sla_descricao_natural": "Dentro",
                },
                {
                    "data_cadastro": "2026-02-10",
                    "id_cliente": 1,
                    "id_workflow": 10,
                    "id_nivel_hierarquico": 100,
                    "quantidade": 7,
                    "sla_descricao_natural": "Fora",
                },
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_month_plan_skip_and_replace(self):
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 1, 15),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            quantidade=5,
            sla_descricao_natural="Dentro",
            date_key_cadastro=20260115,
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 2, 10),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            quantidade=3,
            sla_descricao_natural="Fora",
            date_key_cadastro=20260210,
        )
        plan = build_month_plan(self.parquet_path)
        by_month = {row.competencia: row for row in plan}
        self.assertEqual(by_month["2026-01"].action, "skip")
        self.assertEqual(by_month["2026-02"].action, "replace")

    def test_import_only_replaces_changed_month(self):
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 1, 15),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            quantidade=5,
            sla_descricao_natural="Dentro",
            date_key_cadastro=20260115,
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 2, 10),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            quantidade=3,
            sla_descricao_natural="Fora",
            date_key_cadastro=20260210,
        )
        run, report = import_consolidado_parquet_months(
            self.parquet_path,
            ["2026-02"],
        )
        self.assertEqual(run.status, "ok")
        self.assertEqual(run.metrics.get("kind"), "parquet_import")
        self.assertEqual(run.metrics.get("phase"), "done")
        self.assertEqual(run.metrics.get("months_done"), 1)
        self.assertEqual(report["rows_written"], 1)
        self.assertEqual(SlaUtilConsolidado.objects.filter(data_cadastro__month=1).count(), 1)
        feb = SlaUtilConsolidado.objects.get(data_cadastro=date(2026, 2, 10))
        self.assertEqual(feb.quantidade, 7)

    def test_month_stats_from_parquet(self):
        stats = month_stats_from_parquet(self.parquet_path)
        self.assertEqual(stats["2026-01"].linhas, 1)
        self.assertEqual(stats["2026-01"].volume, 5)
        self.assertEqual(stats["2026-02"].volume, 7)

    def test_kpi_from_db_scoped_to_competencias(self):
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2026, 1, 15),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            quantidade=5,
            sla_descricao_natural="Dentro",
            date_key_cadastro=20260115,
        )
        SlaUtilConsolidado.objects.create(
            data_cadastro=date(2025, 12, 10),
            id_cliente=1,
            id_workflow=10,
            id_nh=100,
            quantidade=99,
            sla_descricao_natural="Fora",
            date_key_cadastro=20251210,
        )
        from apps.monitoramento_sla.services.import_consolidado_parquet import kpi_from_db

        scoped = kpi_from_db(competencias=["2026-01"])
        self.assertEqual(scoped["volume"], 5)
        self.assertEqual(scoped["linhas"], 1)


@override_settings(
    ACCESS_ENFORCEMENT=True,
    ESCALA_FLEX_OPEN_ACCESS=False,
    MONITORAMENTO_SLA_PARQUET_IMPORT_SYNC=True,
)
class ImportParquetApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sla_import_user",
            password="x",
            email="sla_import_user@example.com",
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.tmp = tempfile.TemporaryDirectory()
        self.parquet_path = Path(self.tmp.name) / "api_sample.parquet"
        _write_parquet(
            self.parquet_path,
            [
                {
                    "data_cadastro": "2026-03-01",
                    "id_cliente": 2,
                    "id_workflow": 20,
                    "id_nivel_hierarquico": 200,
                    "quantidade": 11,
                    "sla_descricao_natural": "Dentro",
                }
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_and_apply(self):
        with self.parquet_path.open("rb") as fh:
            upload = SimpleUploadedFile(
                "api_sample.parquet",
                fh.read(),
                content_type="application/octet-stream",
            )
        preview = self.client.post(
            "/api/v1/monitoramento-sla/import-parquet/preview/",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(preview.status_code, 201)
        self.assertTrue(preview.data["ok"])
        self.assertEqual(preview.data["month_plan"][0]["action"], "replace")
        upload_id = preview.data["upload_id"]

        apply_resp = self.client.post(
            "/api/v1/monitoramento-sla/import-parquet/apply/",
            {"upload_id": upload_id},
            format="json",
        )
        self.assertEqual(apply_resp.status_code, 202)
        self.assertTrue(apply_resp.data["ok"])
        run_id = apply_resp.data["run_id"]
        self.assertIn(apply_resp.data["status"], {"running", "ok"})

        detail = self.client.get(f"/api/v1/monitoramento-sla/import-parquet/runs/{run_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["status"], "ok")
        self.assertEqual(detail.data["metrics"]["phase"], "done")
        self.assertEqual(detail.data["rows_written"], 1)

        listing = self.client.get("/api/v1/monitoramento-sla/import-parquet/runs/?active=1")
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(listing.data["ok"])
        self.assertTrue(any(item["run_id"] == run_id for item in listing.data["results"]))

        self.assertEqual(SlaUtilConsolidado.objects.count(), 1)
        self.assertEqual(SlaUtilConsolidado.objects.first().quantidade, 11)

    def test_run_detail_deny_other_user(self):
        with self.parquet_path.open("rb") as fh:
            upload = SimpleUploadedFile("api_sample.parquet", fh.read())
        preview = self.client.post(
            "/api/v1/monitoramento-sla/import-parquet/preview/",
            {"file": upload},
            format="multipart",
        )
        apply_resp = self.client.post(
            "/api/v1/monitoramento-sla/import-parquet/apply/",
            {"upload_id": preview.data["upload_id"]},
            format="json",
        )
        run_id = apply_resp.data["run_id"]

        other = User.objects.create_user(
            username="other_sla_import",
            password="x",
            email="other_sla_import@example.com",
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        other.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        c = APIClient()
        c.force_authenticate(user=other)
        res = c.get(f"/api/v1/monitoramento-sla/import-parquet/runs/{run_id}/")
        self.assertEqual(res.status_code, 403)

    def test_deny_without_perm(self):
        other = User.objects.create_user(
            username="no_sla_import",
            password="x",
            email="no_sla_import@example.com",
        )
        c = APIClient()
        c.force_authenticate(user=other)
        with self.parquet_path.open("rb") as fh:
            upload = SimpleUploadedFile("api_sample.parquet", fh.read())
        res = c.post(
            "/api/v1/monitoramento-sla/import-parquet/preview/",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(res.status_code, 403)
