from __future__ import annotations

from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    QualidadePendenteAuditoria,
)
from apps.auditoria.services.contestacao_import import (
    parse_contestacao_workbook,
    parse_import_filename_metadata,
)

User = get_user_model()


def build_sample_workbook(
  *,
  include_dados_sheet: bool = True,
  duplicate_protocol: bool = False,
  include_consideracoes_row: bool = False,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contestação de Resultado"
    sheet["B9"] = "PROTOCOLO"
    sheet["C9"] = "WORKFLOW"
    sheet["D9"] = "NÍVEL HIERÁRQUICO"
    sheet["E9"] = "N° DO CONTRATO"
    sheet["F9"] = "RESULTADO CONTESTADO"
    sheet["G9"] = "RESULTADO PÓS AUDITORIA"
    sheet["H9"] = "TIPO DE CONCLUSÃO"
    sheet["I9"] = "TIPO DE FALHA"
    sheet["J9"] = "CENÁRIO"
    sheet["K9"] = "DETALHAMENTO"
    sheet["L9"] = "CONCLUSÃO DA CONTESTAÇÃO"
    sheet["B10"] = "123456"
    sheet["C10"] = "Risk Manager - PICPAY"
    sheet["D10"] = "Risk Manager - PICPAY - Digitalizador"
    sheet["F10"] = "SEM RISCO APARENTE"
    sheet["B11"] = "123456" if duplicate_protocol else "123457"
    sheet["C11"] = "Risk Manager - PICPAY"
    sheet["D11"] = "Risk Manager - PICPAY - Digitalizador"
    sheet["F11"] = "DOCUMENTO AUSENTE"
    if include_consideracoes_row:
        sheet["B12"] = "CONSIDERAÇÕES"
        sheet["C12"] = "—"
        sheet["D12"] = "Risk Manager - PICPAY - Digitalizador"
    if include_dados_sheet:
        workbook.create_sheet("Dados")
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@override_settings(ACCESS_ENFORCEMENT=False)
class ContestacaoImportServiceTests(TestCase):
    def test_parse_workbook_finds_header_and_protocols(self):
        preview = parse_contestacao_workbook(build_sample_workbook())
        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.header_row, 9)
        self.assertEqual(preview.total_protocolos, 2)
        self.assertEqual(preview.workflow, "Risk Manager - PICPAY")
        self.assertEqual(len(preview.amostra), 2)

    def test_parse_flags_duplicate_protocols(self):
        preview = parse_contestacao_workbook(build_sample_workbook(duplicate_protocol=True))
        self.assertEqual(preview.protocolos_duplicados, ["123456"])

    def test_parse_ignores_consideracoes_row(self):
        preview = parse_contestacao_workbook(build_sample_workbook(include_consideracoes_row=True))
        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_protocolos, 2)
        self.assertEqual(preview.linhas_ignoradas, 1)
        self.assertTrue(all(row.protocolo != "CONSIDERAÇÕES" for row in preview.rows))

    def test_parse_import_filename_metadata(self):
        metadata = parse_import_filename_metadata("QI-8279 - PICPAY - 01072026.xlsx")
        self.assertEqual(metadata["cliente"], "PICPAY")
        self.assertEqual(metadata["link_demanda"], "")

    def test_parse_csv_finds_header_and_protocols(self):
        from apps.auditoria.services.contestacao_import import parse_contestacao_csv

        csv_content = (
            "PROTOCOLO;WORKFLOW;NÍVEL HIERÁRQUICO;RESULTADO CONTESTADO\n"
            "123456;Risk Manager - PICPAY;Risk Manager - PICPAY - Digitalizador;SEM RISCO APARENTE\n"
            "123457;Risk Manager - PICPAY;Risk Manager - PICPAY - Digitalizador;DOCUMENTO AUSENTE\n"
        ).encode("utf-8")
        preview = parse_contestacao_csv(csv_content)
        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.sheet_name, "CSV")
        self.assertEqual(preview.total_protocolos, 2)
        self.assertEqual(preview.workflow, "Risk Manager - PICPAY")


@override_settings(ACCESS_ENFORCEMENT=False)
class ContestacaoImportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="qualidade_user",
            email="qualidade@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    def test_validate_csv_import_api(self):
        csv_content = (
            "PROTOCOLO;WORKFLOW;NÍVEL HIERÁRQUICO;RESULTADO CONTESTADO\n"
            "999001;WF;NH;OK\n"
        ).encode("utf-8-sig")
        upload = SimpleUploadedFile(
            "QI-1 - CLIENTE - 01012026.csv",
            csv_content,
            content_type="text/csv",
        )
        validate_response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/importacao/validar/",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(validate_response.status_code, 200, validate_response.data)
        self.assertEqual(validate_response.data["preview"]["total_protocolos"], 1)
        self.assertEqual(validate_response.data["preview"]["cliente"], "CLIENTE")

    def test_validate_and_confirm_import_flow(self):
        file_bytes = build_sample_workbook()
        upload = SimpleUploadedFile(
            "contestacao.xlsx",
            file_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        validate_response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/importacao/validar/",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(validate_response.status_code, 200)
        self.assertEqual(validate_response.data["preview"]["total_protocolos"], 2)
        self.assertIn("cliente", validate_response.data["preview"])
        self.assertIn("link_demanda", validate_response.data["preview"])
        self.assertNotIn("workflow", validate_response.data["preview"])
        self.assertNotIn("linhas_ignoradas", validate_response.data["preview"])
        token = validate_response.data["import_token"]

        confirm_response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/importacao/confirmar/",
            {
                "import_token": token,
                "nome": "Atividade teste",
                "cliente": "PICPAY",
                "link_demanda": "https://example.com/demanda/qi-8279",
                "data_recepcao": "2026-07-01",
            },
            format="json",
        )
        self.assertEqual(confirm_response.status_code, 201)
        atividade_id = confirm_response.data["id"]
        self.assertEqual(AuditoriaAtividade.objects.count(), 1)
        self.assertEqual(AuditoriaAtividade.objects.get(id=atividade_id).cliente, "PICPAY")
        self.assertEqual(
            AuditoriaAtividade.objects.get(id=atividade_id).link_demanda,
            "https://example.com/demanda/qi-8279",
        )
        self.assertEqual(AuditoriaAtividadeProtocolo.objects.filter(atividade_id=atividade_id).count(), 2)

        list_response = self.client.get("/api/v1/qualidade/auditoria/atividades/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data["results"]), 1)
        self.assertEqual(list_response.data["results"][0]["protocolos_tratamento_pendentes"], 2)

        detail_response = self.client.get(f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["nome"], "Atividade teste")
        self.assertEqual(detail_response.data["protocolos_tratamento_pendentes"], 2)

        protocolos_response = self.client.get(
            f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/protocolos/"
        )
        self.assertEqual(protocolos_response.status_code, 200)
        self.assertEqual(len(protocolos_response.data["results"]), 2)

    def test_update_atividade_nome(self):
        atividade = AuditoriaAtividade.objects.create(
            nome="Atividade original",
            arquivo_original=SimpleUploadedFile("contestacao.xlsx", b"xlsx"),
            nome_arquivo_original="contestacao.xlsx",
            workflow="Risk Manager - PICPAY",
            total_protocolos=2,
            created_by=self.user,
        )
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/",
            {
                "nome": "Atividade renomeada",
                "workflow": "Novo workflow",
                "nivel_hierarquico": "Novo nível",
                "cliente": "PICPAY",
                "link_demanda": "https://example.com/demanda",
                "data_recepcao": "2026-07-02",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["nome"], "Atividade renomeada")
        self.assertEqual(response.data["workflow"], "Novo workflow")
        self.assertEqual(response.data["nivel_hierarquico"], "Novo nível")
        self.assertEqual(response.data["cliente"], "PICPAY")
        self.assertEqual(response.data["link_demanda"], "https://example.com/demanda")
        self.assertTrue(str(response.data["data_recepcao"]).startswith("2026-07-02"))
        atividade.refresh_from_db()
        self.assertEqual(atividade.nome, "Atividade renomeada")
        self.assertEqual(atividade.cliente, "PICPAY")

    def test_update_atividade_requires_nome(self):
        atividade = AuditoriaAtividade.objects.create(
            nome="Atividade original",
            arquivo_original=SimpleUploadedFile("contestacao.xlsx", b"xlsx"),
            nome_arquivo_original="contestacao.xlsx",
            data_recepcao="2026-07-01",
            total_protocolos=1,
            created_by=self.user,
        )
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/",
            {"nome": "   ", "data_recepcao": "2026-07-01"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nome", response.data["errors"])

    def test_delete_atividade_cascades_protocolos(self):
        atividade = AuditoriaAtividade.objects.create(
            nome="Atividade para excluir",
            arquivo_original=SimpleUploadedFile("contestacao.xlsx", b"xlsx"),
            nome_arquivo_original="contestacao.xlsx",
            total_protocolos=1,
            created_by=self.user,
        )
        AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade,
            protocolo="123456",
            excel_row=10,
        )
        response = self.client.delete(f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(AuditoriaAtividade.objects.count(), 0)
        self.assertEqual(AuditoriaAtividadeProtocolo.objects.count(), 0)

    def test_delete_auditoria_removes_pending_bridge(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Auditoria para excluir",
            created_by=self.user,
        )
        pendente = QualidadePendenteAuditoria.objects.create(
            atividade=atividade,
            protocolo="AUD-ORFAO",
            created_by=self.user,
        )

        response = self.client.delete(f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(QualidadePendenteAuditoria.objects.filter(pk=pendente.pk).exists())

    def test_update_and_delete_blocked_when_concluida(self):
        atividade = AuditoriaAtividade.objects.create(
            nome="Atividade finalizada",
            arquivo_original=SimpleUploadedFile("contestacao.xlsx", b"xlsx"),
            nome_arquivo_original="contestacao.xlsx",
            data_recepcao="2026-07-01",
            total_protocolos=1,
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
            created_by=self.user,
        )
        patch_response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/",
            {
                "nome": "Tentativa de edição",
                "workflow": "",
                "nivel_hierarquico": "",
                "cliente": "",
                "link_demanda": "",
                "data_recepcao": "2026-07-01",
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, 400)
        self.assertIn("finalizadas", patch_response.data["detail"])

        delete_response = self.client.delete(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/"
        )
        self.assertEqual(delete_response.status_code, 400)
        self.assertIn("finalizadas", delete_response.data["detail"])
        self.assertTrue(AuditoriaAtividade.objects.filter(pk=atividade.id).exists())

    def test_create_and_delete_protocolo_updates_activity_metrics(self):
        file_bytes = build_sample_workbook()
        upload = SimpleUploadedFile(
            "contestacao.xlsx",
            file_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        validate_response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/importacao/validar/",
            {"file": upload},
            format="multipart",
        )
        token = validate_response.data["import_token"]
        confirm_response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/importacao/confirmar/",
            {
                "import_token": token,
                "nome": "Atividade protocolos",
                "data_recepcao": "2026-07-01",
            },
            format="json",
        )
        self.assertEqual(confirm_response.status_code, 201, confirm_response.data)
        atividade_id = confirm_response.data["id"]

        create_response = self.client.post(
            f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/protocolos/",
            {"protocolo": "999999", "resultado_contestado": "NOVO PROTOCOLO"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.data["protocolo"], "999999")

        detail_response = self.client.get(f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/")
        self.assertEqual(detail_response.data["total_protocolos"], 3)

        duplicate_response = self.client.post(
            f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/protocolos/",
            {"protocolo": "999999"},
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertIn("protocolo", duplicate_response.data["errors"])

        protocolo_id = create_response.data["id"]
        delete_response = self.client.delete(
            f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/protocolos/{protocolo_id}/",
        )
        self.assertEqual(delete_response.status_code, 204)

        detail_after_delete = self.client.get(f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/")
        self.assertEqual(detail_after_delete.data["total_protocolos"], 2)

    def test_manual_create_atividade_and_protocolo(self):
        create_response = self.client.post(
            "/api/v1/qualidade/auditoria/atividades/",
            {
                "nome": "Atividade manual",
                "workflow": "Risk Manager - PICPAY",
                "nivel_hierarquico": "Digitalizador",
                "cliente": "PICPAY",
                "data_recepcao": "2026-07-01",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        self.assertEqual(create_response.data["nome"], "Atividade manual")
        self.assertEqual(create_response.data["total_protocolos"], 0)
        self.assertEqual(create_response.data["status"], "pendente")
        self.assertIsNone(create_response.data["arquivo_original_url"])

        atividade_id = create_response.data["id"]
        protocolo_response = self.client.post(
            f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/protocolos/",
            {"protocolo": "888888", "resultado_contestado": "SEM RISCO"},
            format="json",
        )
        self.assertEqual(protocolo_response.status_code, 201, protocolo_response.data)
        self.assertEqual(protocolo_response.data["workflow"], "Risk Manager - PICPAY")

        detail_response = self.client.get(f"/api/v1/qualidade/auditoria/atividades/{atividade_id}/")
        self.assertEqual(detail_response.data["total_protocolos"], 1)
        self.assertEqual(detail_response.data["status"], "em_andamento")

