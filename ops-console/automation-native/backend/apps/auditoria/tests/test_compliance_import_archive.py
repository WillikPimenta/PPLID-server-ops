from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaComplianceImportArquivo,
    QualidadeComplianceImportArquivo,
)
from apps.auditoria.services.compliance_import_archive import (
    compliance_import_retention_days,
    purge_compliance_import_arquivos,
)
from apps.auditoria.tests.test_reinspecao_import import build_reinspecao_workbook

User = get_user_model()


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_COMPLIANCE_IMPORT_RETENTION_DAYS=3,
)
class ComplianceImportArchiveTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="comp_archive_user",
            email="comp_archive@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    def _confirm_reinspecao_workbook(self, *, filename: str = "Modelo_Reinspecao_QI.xlsx"):
        uploaded = SimpleUploadedFile(
            filename,
            build_reinspecao_workbook(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        validate = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {"file": uploaded},
            format="multipart",
        )
        self.assertEqual(validate.status_code, 200, validate.data)
        confirm = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/confirmar/",
            {"import_token": validate.data["import_token"]},
            format="json",
        )
        self.assertEqual(confirm.status_code, 201, confirm.data)
        return confirm.data

    def test_confirm_archives_reinspecao_file(self):
        self._confirm_reinspecao_workbook()
        archived = QualidadeComplianceImportArquivo.objects.get()
        self.assertEqual(archived.contexto, QualidadeComplianceImportArquivo.CONTEXTO_REINSPECAO)
        self.assertEqual(archived.nome_arquivo_original, "Modelo_Reinspecao_QI.xlsx")
        self.assertEqual(archived.protocolos_importados, 4)
        self.assertEqual(archived.created_by_id, self.user.id)
        self.assertTrue(archived.arquivo.name.startswith("qualidade/compliance-imports/reinspecao/"))
        self.assertTrue(archived.arquivo.storage.exists(archived.arquivo.name))

    def test_confirm_archives_auditoria_compliance_file(self):
        csv_content = (
            "Protocolo;Tipo/Status conferencia;Matricula Inspetor;Nome Inspetor;Data/Hora da Conferência\n"
            "1001;Reclassificação;C11111Q;Agente Um;27/07/2026 14:35:20\n"
            "1002;Reclassificação;C11111Q;Agente Um;27/07/2026 14:35:20\n"
        ).encode("utf-8")
        validate = self.client.post(
            "/api/v1/qualidade/auditoria/compliance/importacao/validar/",
            {
                "file": SimpleUploadedFile("relatorio.csv", csv_content, content_type="text/csv"),
                "protocolos_por_agente": 1,
                "seed": 1,
            },
            format="multipart",
        )
        self.assertEqual(validate.status_code, 200, validate.data)
        confirm = self.client.post(
            "/api/v1/qualidade/auditoria/compliance/importacao/confirmar/",
            {"import_token": validate.data["import_token"]},
            format="json",
        )
        self.assertEqual(confirm.status_code, 201, confirm.data)

        archived = AuditoriaComplianceImportArquivo.objects.get()
        self.assertEqual(archived.nome_arquivo_original, "relatorio.csv")
        self.assertTrue(
            archived.arquivo.name.startswith("qualidade/auditoria-compliance-imports/")
        )

    def test_purge_removes_files_older_than_retention(self):
        self._confirm_reinspecao_workbook()
        archived = QualidadeComplianceImportArquivo.objects.get()
        storage_name = archived.arquivo.name

        QualidadeComplianceImportArquivo.objects.filter(pk=archived.pk).update(
            created_at=timezone.now() - timedelta(days=compliance_import_retention_days() + 1)
        )

        report = purge_compliance_import_arquivos(dry_run=False)
        self.assertEqual(report.deleted, 1)
        self.assertFalse(QualidadeComplianceImportArquivo.objects.filter(pk=archived.pk).exists())
        self.assertFalse(archived.arquivo.storage.exists(storage_name))

    def test_management_command_dry_run(self):
        self._confirm_reinspecao_workbook()
        archived = QualidadeComplianceImportArquivo.objects.get()
        QualidadeComplianceImportArquivo.objects.filter(pk=archived.pk).update(
            created_at=timezone.now() - timedelta(days=compliance_import_retention_days() + 1)
        )

        call_command("purge_qualidade_compliance_import_arquivos", "--dry-run")
        self.assertEqual(QualidadeComplianceImportArquivo.objects.count(), 1)
        self.assertTrue(archived.arquivo.storage.exists(archived.arquivo.name))

        call_command("purge_qualidade_compliance_import_arquivos")
        self.assertEqual(QualidadeComplianceImportArquivo.objects.count(), 0)
