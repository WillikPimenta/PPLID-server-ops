from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaCatalogItem,
    AuditoriaFalhaCadastro,
    AuditoriaMotivoFalha,
    QualidadeConfiguracaoAlteracao,
)
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="auditor",
            email="auditor@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    def test_catalogs_endpoint(self):
        response = self.client.get("/api/v1/qualidade/auditoria/catalogs/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("modulo", response.data)
        self.assertIn("tipo_falha", response.data)
        self.assertIn("usuario", response.data)
        self.assertIn("novo_resultado", response.data)
        self.assertIn("workflow", response.data)
        self.assertIn("nivel_hierarquico", response.data)
        self.assertIn("irregularidades_confer", response.data)
        self.assertIsInstance(response.data["workflow"], list)
        self.assertIsInstance(response.data["nivel_hierarquico"], list)
        self.assertIn("G Auditoria", response.data["modulo"])
        self.assertIn("Automático", [item["value"] for item in response.data["tipo_falha"]])

    def test_create_falha_cadastro(self):
        payload = {
            "protocolo": "087501293",
            "brflow_raw": "Confederação\t649.376.310-84",
            "brflow_parsed": {"cliente": "Confederação", "protocolo": "087501293"},
            "modulo": "G Auditoria",
            "demanda_url": "https://example.com/demanda/1",
            "tipo_falha": "Automático",
            "usuario": "",
            "resultado_cliente": "Sem risco aparente",
            "novo_resultado": "Sem risco aparente",
            "sinalizacao": "Sinalização incorreta",
            "motivo_falha": "Motivo teste",
            "etapa_falha": "Risk Manager - Processo Automático",
            "nivel_dificuldade": "Médio",
            "tipo_documento": "Rg - Estadual",
            "uf_documento": "SP",
        }
        response = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["protocolo"], "087501293")
        self.assertEqual(response.data["tipo_falha"], "Automático")
        record = AuditoriaFalhaCadastro.objects.get(protocolo="087501293")
        self.assertEqual(record.auditor_responsavel_id, self.user.id)

    def test_create_persists_tipo_registro(self):
        payload = {
            "protocolo": "087501294",
            "tipo_falha": "Automático",
            "usuario": "",
            "etapa_falha": "Risk Manager - Processo Automático",
            "tipo_registro": "contestacao",
        }
        response = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["tipo_registro"], "contestacao")

    def test_create_rejects_invalid_demanda_url(self):
        payload = {
            "protocolo": "123",
            "tipo_falha": "Automático",
            "usuario": "c91763a",
            "demanda_url": "not-a-url",
        }
        response = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("demanda_url", response.data["errors"])

    def test_create_automatico_does_not_require_usuario(self):
        payload = {
            "protocolo": "999888777",
            "tipo_falha": "Automático",
            "usuario": "",
            "etapa_falha": "Risk Manager - Processo Automático",
        }
        response = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["usuario"], "")

    def test_create_non_automatico_requires_usuario(self):
        payload = {
            "protocolo": "999888776",
            "tipo_falha": "Biometria",
            "usuario": "",
        }
        response = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("usuario", response.data["errors"])

    def test_create_colaborador_requires_and_persists_observacao(self):
        payload = {
            "protocolo": "999888775",
            "tipo_falha": "Colaborador",
            "usuario": "c12345a",
        }
        missing = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertIn("observacao", missing.data["errors"])

        payload["observacao"] = "Conduta identificada durante a análise."
        created = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        record = AuditoriaFalhaCadastro.objects.get(protocolo="999888775")
        self.assertEqual(record.observacao, payload["observacao"])

    def test_create_requires_protocolo(self):
        payload = {
            "tipo_falha": "Automático",
            "usuario": "",
        }
        response = self.client.post("/api/v1/qualidade/auditoria/falhas/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("protocolo", response.data["errors"])


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaConfigApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="config_admin",
            email="config@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _xlsx(headers, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        output = BytesIO()
        workbook.save(output)
        return SimpleUploadedFile(
            "catalogo.xlsx",
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_config_meta_includes_controle_catalogs(self):
        response = self.client.get("/api/v1/qualidade/auditoria/config/meta/")
        self.assertEqual(response.status_code, 200)
        slugs = {item["slug"] for item in response.data["results"]}
        self.assertIn("tipo-acao", slugs)
        self.assertIn("motivo-base-negativa", slugs)
        self.assertIn("irregularidades-confer", slugs)
        self.assertIn("duvidas-suporte-operacional", slugs)
        support_questions = next(
            item
            for item in response.data["results"]
            if item["slug"] == "duvidas-suporte-operacional"
        )
        self.assertGreaterEqual(support_questions["count"], 26)

    def test_config_meta_lists_catalogs(self):
        response = self.client.get("/api/v1/qualidade/auditoria/config/meta/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 9)
        slugs = [item["slug"] for item in response.data["results"]]
        self.assertIn("cenarios", slugs)

    def test_config_crud_modulo(self):
        list_response = self.client.get("/api/v1/qualidade/auditoria/config/modulos/")
        self.assertEqual(list_response.status_code, 200)
        self.assertIn("G Auditoria", [item["value"] for item in list_response.data["results"]])

        create_response = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/",
            {"value": "Modulo Teste"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        item_id = create_response.data["id"]

        patch_response = self.client.patch(
            f"/api/v1/qualidade/auditoria/config/modulos/{item_id}/",
            {"active": False},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertFalse(patch_response.data["active"])

        logs = list(QualidadeConfiguracaoAlteracao.objects.order_by("id"))
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0].tabela_origem, "auditoria_catalog_item")
        self.assertEqual(logs[0].registro_id, str(item_id))
        self.assertEqual(logs[0].usuario, self.user)
        self.assertEqual(logs[0].mudancas["value"], {"de": None, "para": "Modulo Teste"})
        self.assertEqual(logs[1].mudancas, {"active": {"de": True, "para": False}})

    def test_config_cenario_logs_de_para(self):
        create_response = self.client.post(
            "/api/v1/qualidade/auditoria/config/cenarios/",
            {
                "motivo": "Cenario auditavel",
                "criticidade": "Critica",
                "segmentos": "RISCO",
                "subsegmento": "SELFIE",
                "active": True,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)

        item_id = create_response.data["id"]
        patch_response = self.client.patch(
            f"/api/v1/qualidade/auditoria/config/cenarios/{item_id}/",
            {"criticidade": "Nao Critica", "active": False},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)

        log = QualidadeConfiguracaoAlteracao.objects.order_by("-id").first()
        self.assertEqual(log.tabela_origem, "auditoria_motivo_falha")
        self.assertEqual(log.registro_id, str(item_id))
        self.assertEqual(
            log.mudancas,
            {
                "criticidade": {"de": "Critica", "para": "Nao Critica"},
                "active": {"de": True, "para": False},
            },
        )

    def test_config_patch_without_changes_does_not_create_log(self):
        created = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/",
            {"value": "Sem mudanca", "sort_order": 999, "active": True},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        QualidadeConfiguracaoAlteracao.objects.all().delete()
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/config/modulos/{created.data['id']}/",
            {
                "value": created.data["value"],
                "label": created.data["label"],
                "sort_order": created.data["sort_order"],
                "active": created.data["active"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(QualidadeConfiguracaoAlteracao.objects.exists())

    def test_config_change_history_lists_and_undoes_update(self):
        created = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/",
            {"value": "Modulo reversivel", "active": True},
            format="json",
        )
        item_id = created.data["id"]
        patched = self.client.patch(
            f"/api/v1/qualidade/auditoria/config/modulos/{item_id}/",
            {"active": False},
            format="json",
        )
        self.assertEqual(patched.status_code, 200)
        update_log = QualidadeConfiguracaoAlteracao.objects.order_by("-id").first()

        listed = self.client.get("/api/v1/qualidade/auditoria/config/alteracoes/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["results"][0]["id"], update_log.pk)
        self.assertTrue(listed.data["results"][0]["pode_desfazer"])

        undone = self.client.post(
            f"/api/v1/qualidade/auditoria/config/alteracoes/{update_log.pk}/desfazer/"
        )
        self.assertEqual(undone.status_code, 200)
        item = AuditoriaCatalogItem.objects.get(pk=item_id)
        self.assertTrue(item.active)
        reverse_log = QualidadeConfiguracaoAlteracao.objects.get(pk=undone.data["reversao_id"])
        self.assertEqual(reverse_log.reverte_id, update_log.pk)
        self.assertEqual(reverse_log.mudancas, {"active": {"de": False, "para": True}})

    def test_config_change_undoes_creation_by_removing_record(self):
        created = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/",
            {"value": "Modulo temporario", "active": True},
            format="json",
        )
        item_id = created.data["id"]
        creation_log = QualidadeConfiguracaoAlteracao.objects.get()

        undone = self.client.post(
            f"/api/v1/qualidade/auditoria/config/alteracoes/{creation_log.pk}/desfazer/"
        )
        self.assertEqual(undone.status_code, 200)
        self.assertFalse(AuditoriaCatalogItem.objects.filter(pk=item_id).exists())

    def test_config_change_undo_rejects_later_changes(self):
        created = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/",
            {"value": "Modulo concorrente", "active": True},
            format="json",
        )
        item_id = created.data["id"]
        self.client.patch(
            f"/api/v1/qualidade/auditoria/config/modulos/{item_id}/",
            {"active": False},
            format="json",
        )
        update_log = QualidadeConfiguracaoAlteracao.objects.order_by("-id").first()
        AuditoriaCatalogItem.objects.filter(pk=item_id).update(active=True)

        undone = self.client.post(
            f"/api/v1/qualidade/auditoria/config/alteracoes/{update_log.pk}/desfazer/"
        )
        self.assertEqual(undone.status_code, 409)
        self.assertIn("alteracoes posteriores", undone.data["detail"])

    def test_config_change_undo_reports_unique_value_conflict(self):
        original = AuditoriaMotivoFalha.objects.create(
            motivo="Cenario original",
            criticidade="Critica",
            segmentos="RISCO",
            subsegmento="SELFIE",
            sort_order=701,
            active=True,
        )
        updated = self.client.patch(
            f"/api/v1/qualidade/auditoria/config/cenarios/{original.pk}/",
            {"motivo": "Cenario alterado"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        change = QualidadeConfiguracaoAlteracao.objects.order_by("-id").first()
        conflict = self.client.post(
            "/api/v1/qualidade/auditoria/config/cenarios/",
            {
                "motivo": "Cenario original",
                "criticidade": "Critica",
                "segmentos": "RISCO",
                "subsegmento": "SELFIE",
                "sort_order": 702,
                "active": True,
            },
            format="json",
        )
        self.assertEqual(conflict.status_code, 201, conflict.data)

        undone = self.client.post(
            f"/api/v1/qualidade/auditoria/config/alteracoes/{change.pk}/desfazer/"
        )

        self.assertEqual(undone.status_code, 409)
        self.assertIn("ja esta sendo utilizado por outro registro", undone.data["detail"])
        original.refresh_from_db()
        self.assertEqual(original.motivo, "Cenario alterado")
        self.assertFalse(change.reversoes.exists())

    def test_config_change_undo_removes_scenario_recreated_automatically_by_seed(self):
        original = AuditoriaMotivoFalha.objects.create(
            motivo="Cenario padrao",
            criticidade="Critica",
            segmentos="RISCO",
            subsegmento="SELFIE",
            sort_order=711,
            active=True,
        )
        updated = self.client.patch(
            f"/api/v1/qualidade/auditoria/config/cenarios/{original.pk}/",
            {"motivo": "Cenario padrao alterado"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        change = QualidadeConfiguracaoAlteracao.objects.order_by("-id").first()
        automatic_duplicate = AuditoriaMotivoFalha.objects.create(
            motivo="Cenario padrao",
            criticidade=original.criticidade,
            segmentos=original.segmentos,
            subsegmento=original.subsegmento,
            sort_order=999,
            active=original.active,
        )

        undone = self.client.post(
            f"/api/v1/qualidade/auditoria/config/alteracoes/{change.pk}/desfazer/"
        )

        self.assertEqual(undone.status_code, 200, undone.data)
        original.refresh_from_db()
        self.assertEqual(original.motivo, "Cenario padrao")
        self.assertFalse(AuditoriaMotivoFalha.objects.filter(pk=automatic_duplicate.pk).exists())

    def test_config_import_excel_creates_catalog_rows_and_logs(self):
        response = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/importar/",
            {
                "file": self._xlsx(
                    ["Ordem", "Valor", "Ativo"],
                    [[801, "Modulo Excel 1", "Sim"], [802, "Modulo Excel 2", "Nao"]],
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data, {"created": 2, "skipped": 0})
        self.assertTrue(
            AuditoriaCatalogItem.objects.filter(
                catalog=AuditoriaCatalogItem.CATALOG_MODULO,
                value="Modulo Excel 2",
                active=False,
            ).exists()
        )
        self.assertEqual(QualidadeConfiguracaoAlteracao.objects.count(), 2)

    def test_config_import_preview_classifies_rows_without_writing(self):
        AuditoriaCatalogItem.objects.create(
            catalog=AuditoriaCatalogItem.CATALOG_MODULO,
            value="Modulo Existente",
            label="Modulo Existente",
            sort_order=800,
            active=True,
        )
        response = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/importar/pre-visualizar/",
            {
                "file": self._xlsx(
                    ["Ordem", "Valor", "Ativo"],
                    [
                        [801, "Modulo novo", "Sim"],
                        [802, "Modulo existente", "Nao"],
                        [803, "Modulo invalido", "Talvez"],
                    ],
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["total"], 3)
        self.assertEqual(response.data["summary"]["new"], 1)
        self.assertEqual(response.data["summary"]["duplicates"], 1)
        self.assertEqual(response.data["summary"]["errors"], 1)
        self.assertFalse(response.data["can_confirm"])
        self.assertEqual([row["status"] for row in response.data["rows"]], ["novo", "duplicado", "erro"])
        self.assertFalse(AuditoriaCatalogItem.objects.filter(value="Modulo novo").exists())
        self.assertEqual(QualidadeConfiguracaoAlteracao.objects.count(), 0)

    def test_config_scenarios_import_preview_does_not_write(self):
        response = self.client.post(
            "/api/v1/qualidade/auditoria/config/cenarios/importar/pre-visualizar/",
            {
                "file": self._xlsx(
                    ["Ordem", "Alerta", "Criticidade", "Segmentos", "Subsegmento", "Ativo"],
                    [[991, "Cenario somente previa", "Critica", "RISCO", "SELFIE", "Sim"]],
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["total"], 1)
        self.assertEqual(response.data["summary"]["new"], 1)
        self.assertEqual(response.data["summary"]["duplicates"], 0)
        self.assertEqual(response.data["summary"]["errors"], 0)
        self.assertTrue(response.data["can_confirm"])
        self.assertFalse(AuditoriaMotivoFalha.objects.filter(motivo="Cenario somente previa").exists())
        self.assertEqual(QualidadeConfiguracaoAlteracao.objects.count(), 0)

    def test_config_scenarios_replace_previews_and_synchronizes_without_deleting(self):
        AuditoriaMotivoFalha.objects.all().delete()
        updated_item = AuditoriaMotivoFalha.objects.create(
            motivo="Cenario atualizado", criticidade="Antiga", segmentos="RISCO",
            subsegmento="SELFIE", sort_order=20, active=True,
        )
        reactivated_item = AuditoriaMotivoFalha.objects.create(
            motivo="Cenario reativado", criticidade="Critica", segmentos="RISCO",
            subsegmento="SELFIE", sort_order=21, active=False,
        )
        absent_item = AuditoriaMotivoFalha.objects.create(
            motivo="Cenario ausente", criticidade="Critica", segmentos="RISCO",
            subsegmento="SELFIE", sort_order=22, active=True,
        )
        headers = ["Alerta", "Criticidade", "Segmentos", "Subsegmento"]
        rows = [
            ["Cenario atualizado", "Critica", "RISCO", "SELFIE"],
            ["Cenario reativado", "Critica", "RISCO", "SELFIE"],
            ["Cenario novo", "Procedimento", "RISCO", "SELFIE"],
        ]
        preview = self.client.post(
            "/api/v1/qualidade/auditoria/config/cenarios/importar/pre-visualizar/",
            {"file": self._xlsx(headers, rows), "mode": "replace"},
            format="multipart",
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["summary"]["new"], 1)
        self.assertEqual(preview.data["summary"]["updated"], 1)
        self.assertEqual(preview.data["summary"]["reactivated"], 1)
        self.assertEqual(preview.data["summary"]["deactivated"], 1)
        self.assertEqual(preview.data["summary"]["actions"], 4)
        self.assertTrue(preview.data["can_confirm"])
        self.assertTrue(AuditoriaMotivoFalha.objects.get(pk=absent_item.pk).active)
        self.assertFalse(QualidadeConfiguracaoAlteracao.objects.exists())

        imported = self.client.post(
            "/api/v1/qualidade/auditoria/config/cenarios/importar/",
            {"file": self._xlsx(headers, rows), "mode": "replace"},
            format="multipart",
        )
        self.assertEqual(imported.status_code, 201, imported.data)
        self.assertEqual(
            imported.data,
            {"created": 1, "updated": 1, "reactivated": 1, "deactivated": 1, "skipped": 0},
        )
        updated_item.refresh_from_db()
        reactivated_item.refresh_from_db()
        absent_item.refresh_from_db()
        self.assertEqual(updated_item.criticidade, "Critica")
        self.assertTrue(reactivated_item.active)
        self.assertFalse(absent_item.active)
        self.assertTrue(AuditoriaMotivoFalha.objects.filter(motivo="Cenario novo", active=True).exists())
        self.assertEqual(QualidadeConfiguracaoAlteracao.objects.count(), 4)

    def test_config_scenario_edit_is_not_recreated_by_seed(self):
        original = AuditoriaMotivoFalha.objects.order_by("id").first()
        previous_value = original.motivo
        count_before = AuditoriaMotivoFalha.objects.count()
        updated = self.client.patch(
            f"/api/v1/qualidade/auditoria/config/cenarios/{original.pk}/",
            {"motivo": f"{previous_value} alterado"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)

        listed = self.client.get("/api/v1/qualidade/auditoria/config/cenarios/")

        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(AuditoriaMotivoFalha.objects.count(), count_before)
        self.assertFalse(AuditoriaMotivoFalha.objects.filter(motivo=previous_value).exists())

    def test_config_bulk_update_changes_multiple_rows_and_logs_each_one(self):
        first = AuditoriaCatalogItem.objects.create(
            catalog=AuditoriaCatalogItem.CATALOG_MODULO,
            value="Lote 1",
            label="Lote 1",
            sort_order=901,
            active=True,
        )
        second = AuditoriaCatalogItem.objects.create(
            catalog=AuditoriaCatalogItem.CATALOG_MODULO,
            value="Lote 2",
            label="Lote 2",
            sort_order=902,
            active=True,
        )
        response = self.client.post(
            "/api/v1/qualidade/auditoria/config/modulos/alteracao-em-massa/",
            {
                "items": [
                    {"id": first.pk, "value": "Lote 1 alterado", "label": "Lote 1 alterado", "sort_order": 911, "active": False},
                    {"id": second.pk, "value": "Lote 2 alterado", "label": "Lote 2 alterado", "sort_order": 912, "active": False},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 2)
        self.assertEqual(QualidadeConfiguracaoAlteracao.objects.count(), 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.value, "Lote 1 Alterado")
        self.assertFalse(second.active)

    def test_config_scenarios_import_and_bulk_update(self):
        imported = self.client.post(
            "/api/v1/qualidade/auditoria/config/cenarios/importar/",
            {
                "file": self._xlsx(
                    ["Ordem", "Cenário", "Criticidade", "Segmentos", "Subsegmento", "Ativo"],
                    [[991, "Cenario Excel", "Critica", "RISCO", "SELFIE", "Sim"]],
                )
            },
            format="multipart",
        )
        self.assertEqual(imported.status_code, 201)
        scenario = AuditoriaMotivoFalha.objects.get(motivo="Cenario Excel")

        updated = self.client.post(
            "/api/v1/qualidade/auditoria/config/cenarios/alteracao-em-massa/",
            {
                "items": [
                    {
                        "id": scenario.pk,
                        "motivo": "Cenario Excel alterado",
                        "criticidade": "Nao Critica",
                        "segmentos": "RISCO",
                        "subsegmento": "SELFIE",
                        "sort_order": 992,
                        "active": False,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["updated"], 1)
        scenario.refresh_from_db()
        self.assertEqual(scenario.motivo, "Cenario Excel alterado")
        self.assertFalse(scenario.active)

    def test_catalogs_include_backoffice_i_users(self):
        agent = Agent.objects.create(user_lan_id="back001", full_name="Agente Backoffice Teste", active=True)
        AgentHistory.objects.create(
            agent=agent,
            job_title="Agente Backoffice I",
            team="Operacional",
            start_date="2026-01-01",
            active=True,
        )
        response = self.client.get("/api/v1/qualidade/auditoria/catalogs/")
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.data["usuario"]]
        values = [item["value"] for item in response.data["usuario"]]
        self.assertIn("SISTEMA", labels)
        self.assertIn("SISTEMA", values)
        self.assertIn("Agente Backoffice Teste", labels)

    def test_catalogs_read_from_database_after_seed(self):
        AuditoriaCatalogItem.objects.filter(
            catalog=AuditoriaCatalogItem.CATALOG_MODULO,
            value="G Auditoria",
        ).update(active=False)
        AuditoriaCatalogItem.objects.create(
            catalog=AuditoriaCatalogItem.CATALOG_MODULO,
            value="Módulo Customizado",
            label="Módulo Customizado",
            sort_order=0,
            active=True,
        )
        response = self.client.get("/api/v1/qualidade/auditoria/catalogs/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Módulo Customizado", response.data["modulo"])
        self.assertNotIn("G Auditoria", response.data["modulo"])

    def test_cenarios_seed_and_catalogs(self):
        response = self.client.get("/api/v1/qualidade/auditoria/config/cenarios/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 190)
        first = response.data["results"][0]
        self.assertIn("motivo", first)
        self.assertIn("criticidade", first)
        self.assertIn("segmentos", first)
        self.assertIn("subsegmento", first)
        self.assertEqual(response.data["title"], "Cenários")

        catalogs = self.client.get("/api/v1/qualidade/auditoria/catalogs/")
        self.assertEqual(catalogs.status_code, 200)
        self.assertGreaterEqual(len(catalogs.data["motivo_falha"]), 190)
        self.assertIn(first["motivo"], catalogs.data["motivo_falha"])
