# -*- coding: utf-8 -*-
import csv
import io
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_QUAL_GERENCIA, role_group_name
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeImportBatch,
)
from apps.qualidade_operacional.services.monthly_import import (
    run_import,
    run_restore,
    save_uploaded_tsv,
    validate_batch,
)

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def monthly_fixture(name: str, *, data: str = "15/07/2026", rows: int = 1) -> bytes:
    with (FIXTURES / name).open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        base = next(reader)
        fieldnames = reader.fieldnames or []
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for index in range(rows):
        row = dict(base)
        row["Data"] = data
        row["Protocolo"] = f"JUL-{index + 1}"
        writer.writerow(row)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


@override_settings(ACCESS_ENFORCEMENT=True, QUALIDADE_IMPORT_MAX_BYTES=10 * 1024 * 1024)
class MonthlyQualityImportTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)
        self.user = User.objects.create_user(username="quality_manager", password="x")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_GERENCIA))
        self.user.groups.add(group)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_batch(self, kind: str, content: bytes, filename: str) -> QualidadeImportBatch:
        batch = QualidadeImportBatch.objects.create(
            kind=kind,
            competencia=date(2026, 7, 1),
            filename=filename,
            uploaded_by=self.user,
        )
        upload = SimpleUploadedFile(filename, content, content_type="text/tab-separated-values")
        save_uploaded_tsv(batch, upload)
        return validate_batch(batch)

    def test_validates_both_monthly_layouts(self):
        auditados = self.make_batch(
            QualidadeImportBatch.KIND_AUDITADOS,
            monthly_fixture("tabela_auditado_com_tipo_de_conclusao_1.tsv", rows=2),
            "tabela_auditoria_2026_07.tsv",
        )
        falhas = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            monthly_fixture("tabela_falhas.tsv", rows=3),
            "tabela_falhas_2026_07.tsv",
        )
        self.assertEqual(auditados.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(auditados.rows_valid, 2)
        self.assertEqual(falhas.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(falhas.rows_valid, 3)
        self.assertEqual(falhas.rows_outside_period, 0)

    def test_falhas_validation_warns_duplicate_protocolo_matricula(self):
        with (FIXTURES / "tabela_falhas.tsv").open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")
            base = next(reader)
            fieldnames = reader.fieldnames or []
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for suffix in ("A", "B"):
            row = dict(base)
            row["Data"] = "15/07/2026"
            row["Protocolo"] = "DUP-CASE"
            row["Matrícula"] = "c10001a"
            row["Etapa"] = suffix
            writer.writerow(row)
        content = ("\ufeff" + output.getvalue()).encode("utf-8")
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            content,
            "tabela_falhas_dup_case.tsv",
        )
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(batch.rows_valid, 2)
        self.assertEqual(batch.duplicate_protocol_rows, 1)
        self.assertEqual(batch.rows_importable, 1)
        self.assertTrue(
            any("deduplicadas" in warning for warning in batch.warnings)
        )

    def test_falhas_validation_skips_when_db_has_older_record(self):
        from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

        QualidadeFalha.objects.create(
            protocolo="DUP-CASE",
            matricula="c10001a",
            case_key="dup-case|c10001a",
            data=date(2026, 7, 1),
            source_file=INTRANET_SOURCE_FILE,
        )
        with (FIXTURES / "tabela_falhas.tsv").open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")
            base = next(reader)
            fieldnames = reader.fieldnames or []
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        row = dict(base)
        row["Data"] = "15/07/2026"
        row["Protocolo"] = "DUP-CASE"
        row["Matrícula"] = "c10001a"
        writer.writerow(row)
        content = ("\ufeff" + output.getvalue()).encode("utf-8")
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            content,
            "tabela_falhas_conflict.tsv",
        )
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(batch.rows_importable, 0)
        self.assertEqual(len(batch.case_key_conflicts), 1)
        self.assertEqual(batch.case_key_conflicts[0]["action"], "skip")

    def test_falhas_import_preserves_older_intranet_record_and_imports_zero(self):
        from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

        intranet_falha = QualidadeFalha.objects.create(
            protocolo="DUP-CROSS-SOURCE",
            matricula="c10001a",
            case_key="dup-cross-source|c10001a",
            data=date(2026, 7, 1),
            source_file=INTRANET_SOURCE_FILE,
        )
        auditado = QualidadeAuditado.objects.create(
            protocolo="DUP-CROSS-SOURCE",
            matricula="c10001a",
            data=date(2026, 7, 1),
            source_file=INTRANET_SOURCE_FILE,
        )
        content = monthly_fixture("tabela_falhas.tsv", data="15/07/2026", rows=1)
        content = content.replace(b"JUL-1", b"DUP-CROSS-SOURCE").replace(
            b"c92629a", b"c10001a"
        )
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            content,
            "tabela_falhas_cross_source.tsv",
        )
        self.assertEqual(batch.rows_importable, 0)
        self.assertEqual(batch.case_key_conflicts[0]["action"], "skip")
        batch.status = QualidadeImportBatch.STATUS_PROCESSING
        batch.save(update_fields=["status"])

        run_import(str(batch.id))

        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.imported_rows, 0)
        self.assertTrue(QualidadeFalha.objects.filter(pk=intranet_falha.pk).exists())
        self.assertEqual(
            QualidadeFalha.objects.filter(case_key="dup-cross-source|c10001a").count(),
            1,
        )
        self.assertTrue(QualidadeAuditado.objects.filter(pk=auditado.pk).exists())

    def test_falhas_import_replaces_newer_intranet_record_with_older_tsv(self):
        from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

        intranet_falha = QualidadeFalha.objects.create(
            protocolo="DUP-CROSS-SOURCE",
            matricula="c10001a",
            case_key="dup-cross-source|c10001a",
            data=date(2026, 7, 20),
            source_file=INTRANET_SOURCE_FILE,
        )
        content = monthly_fixture("tabela_falhas.tsv", data="15/07/2026", rows=1)
        content = content.replace(b"JUL-1", b"DUP-CROSS-SOURCE").replace(
            b"c92629a", b"c10001a"
        )
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            content,
            "tabela_falhas_cross_source.tsv",
        )
        self.assertEqual(batch.rows_importable, 1)
        self.assertEqual(batch.case_key_conflicts[0]["action"], "replace")
        batch.status = QualidadeImportBatch.STATUS_PROCESSING
        batch.save(update_fields=["status"])

        run_import(str(batch.id))

        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.imported_rows, 1)
        self.assertFalse(QualidadeFalha.objects.filter(pk=intranet_falha.pk).exists())
        kept = QualidadeFalha.objects.get(case_key="dup-cross-source|c10001a")
        self.assertEqual(kept.data, date(2026, 7, 15))
        self.assertEqual(kept.source_file, "tabela_falhas_cross_source.tsv")

    def test_falhas_validation_ignores_tsv_from_month_that_will_be_replaced(self):
        old_tsv = QualidadeFalha.objects.create(
            protocolo="DUP-REPLACED-MONTH",
            matricula="c10001a",
            case_key="dup-replaced-month|c10001a",
            data=date(2026, 7, 1),
            source_file="falhas_julho_anterior.tsv",
        )
        content = monthly_fixture("tabela_falhas.tsv", data="15/07/2026", rows=1)
        content = content.replace(b"JUL-1", b"DUP-REPLACED-MONTH").replace(
            b"c92629a", b"c10001a"
        )
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            content,
            "tabela_falhas_julho_nova.tsv",
        )

        self.assertEqual(batch.rows_importable, 1)
        self.assertEqual(batch.case_key_conflicts, [])
        batch.status = QualidadeImportBatch.STATUS_PROCESSING
        batch.save(update_fields=["status"])
        run_import(str(batch.id))

        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.imported_rows, 1)
        self.assertFalse(QualidadeFalha.objects.filter(pk=old_tsv.pk).exists())
        kept = QualidadeFalha.objects.get(case_key="dup-replaced-month|c10001a")
        self.assertEqual(kept.data, date(2026, 7, 15))
        self.assertEqual(kept.source_file, "tabela_falhas_julho_nova.tsv")

    def test_auditados_duplicate_protocol_is_not_deduplicated_by_falha_rule(self):
        content = monthly_fixture(
            "tabela_auditado_com_tipo_de_conclusao_1.tsv",
            rows=2,
        )
        content = content.replace(b"JUL-1", b"AUD-SAME").replace(b"JUL-2", b"AUD-SAME")
        batch = self.make_batch(
            QualidadeImportBatch.KIND_AUDITADOS,
            content,
            "tabela_auditados_same_protocol.tsv",
        )

        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(batch.rows_valid, 2)
        self.assertEqual(batch.rows_importable, 2)
        self.assertEqual(batch.case_key_conflicts, [])

    def test_rejects_rows_outside_selected_month(self):
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            monthly_fixture("tabela_falhas.tsv", data="30/06/2026"),
            "tabela_falhas_2026_06.tsv",
        )
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_FAILED)
        self.assertEqual(batch.rows_outside_period, 1)
        self.assertFalse(batch.rows_valid)

    def test_import_replaces_only_month_and_backup_restores_it(self):
        old = QualidadeFalha.objects.create(
            data=date(2026, 7, 2), protocolo="OLD-JUL", source_file="old.tsv"
        )
        august = QualidadeFalha.objects.create(
            data=date(2026, 8, 2), protocolo="KEEP-AUG", source_file="old.tsv"
        )
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            monthly_fixture("tabela_falhas.tsv", rows=2),
            "tabela_falhas_2026_07.tsv",
        )
        batch.status = QualidadeImportBatch.STATUS_PROCESSING
        batch.save(update_fields=["status"])

        run_import(str(batch.id))
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.previous_rows, 1)
        self.assertEqual(batch.imported_rows, 2)
        self.assertSetEqual(
            set(QualidadeFalha.objects.filter(data__month=7).values_list("protocolo", flat=True)),
            {"JUL-1", "JUL-2"},
        )
        self.assertTrue(QualidadeFalha.objects.filter(pk=august.pk).exists())
        self.assertTrue(Path(batch.backup_path).exists())

        batch.status = QualidadeImportBatch.STATUS_RESTORING
        batch.save(update_fields=["status"])
        run_restore(str(batch.id))
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_RESTORED)
        self.assertEqual(
            list(QualidadeFalha.objects.filter(data__month=7).values_list("protocolo", flat=True)),
            [old.protocolo],
        )
        self.assertTrue(QualidadeFalha.objects.filter(pk=august.pk).exists())

    def test_upload_api_returns_volume_before_confirmation(self):
        QualidadeAuditado.objects.create(data=date(2026, 7, 1), protocolo="CURRENT")
        response = self.client.post(
            "/api/v1/qualidade/operacional/imports/",
            {
                "kind": "auditados",
                "competencia": "2026-07",
                "file": SimpleUploadedFile(
                    "tabela_auditoria_2026_07.tsv",
                    monthly_fixture("tabela_auditado_com_tipo_de_conclusao_1.tsv", rows=2),
                    content_type="text/tab-separated-values",
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.data["batch"]
        self.assertEqual(payload["status"], "validated")
        self.assertEqual(payload["previous_rows"], 1)
        self.assertEqual(payload["rows_valid"], 2)
        self.assertTrue(payload["can_confirm"])
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
    def test_confirm_api_claims_validated_batch_and_schedules_worker(self):
        batch = self.make_batch(
            QualidadeImportBatch.KIND_FALHAS,
            monthly_fixture("tabela_falhas.tsv"),
            "tabela_falhas_2026_07.tsv",
        )
        with patch("apps.qualidade_operacional.services.monthly_import._schedule") as schedule:
            response = self.client.post(
                f"/api/v1/qualidade/operacional/imports/{batch.id}/confirm/"
            )
        self.assertEqual(response.status_code, 202)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_PROCESSING)
        schedule.assert_called_once()


@override_settings(
    ACCESS_ENFORCEMENT=True,
    QUALIDADE_IMPORT_MAX_BYTES=10 * 1024 * 1024,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="hybrid",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-08",
)
class MonthlyCutoverImportTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)
        self.user = User.objects.create_user(username="quality_monthly_cutover", password="x")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_GERENCIA))
        self.user.groups.add(group)

    def test_august_competencia_passes_gate_with_pre_cutover_rows(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            competencia=date(2026, 8, 1),
            filename="tabela_falhas_2026_08.tsv",
            uploaded_by=self.user,
        )
        content = monthly_fixture("tabela_falhas.tsv", data="05/08/2026", rows=2)

        upload = SimpleUploadedFile(
            batch.filename,
            content,
            content_type="text/tab-separated-values",
        )
        save_uploaded_tsv(batch, upload)
        batch = validate_batch(batch)

        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_VALIDATED)
        self.assertEqual(batch.rows_valid, 2)
        self.assertNotIn(
            "Competência coberta pela fonte Intranet",
            batch.failure_detail or "",
        )

    def test_august_competencia_rejects_post_cutover_at_row_level(self):
        batch = QualidadeImportBatch.objects.create(
            kind=QualidadeImportBatch.KIND_FALHAS,
            competencia=date(2026, 8, 1),
            filename="tabela_falhas_2026_08_mixed.tsv",
            uploaded_by=self.user,
        )
        with (FIXTURES / "tabela_falhas.tsv").open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")
            base = next(reader)
            fieldnames = reader.fieldnames or []
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for index, data in enumerate(["05/08/2026", "10/08/2026"]):
            row = dict(base)
            row["Data"] = data
            row["Protocolo"] = f"AUG-{index + 1}"
            writer.writerow(row)
        content = ("\ufeff" + output.getvalue()).encode("utf-8")

        upload = SimpleUploadedFile(
            batch.filename,
            content,
            content_type="text/tab-separated-values",
        )
        save_uploaded_tsv(batch, upload)
        batch = validate_batch(batch)

        self.assertEqual(batch.status, QualidadeImportBatch.STATUS_FAILED)
        self.assertNotIn(
            "Competência coberta pela fonte Intranet",
            batch.failure_detail or "",
        )
        self.assertIn("período Intranet", batch.failure_detail or "")
