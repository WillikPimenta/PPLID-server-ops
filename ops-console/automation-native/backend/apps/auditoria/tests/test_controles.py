from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.auditoria.models import AuditoriaControleRegistro

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaControlesApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="controle_user",
            email="controle@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    def test_meta_and_crud_remocao_base_negativa(self):
        meta = self.client.get("/api/v1/qualidade/auditoria/controles/remocao_base_negativa/meta/")
        self.assertEqual(meta.status_code, 200)
        self.assertEqual(meta.data["title"], "Remoção - Base negativa")
        self.assertTrue(any(field["key"] == "cpf" for field in meta.data["fields"]))

        create = self.client.post(
            "/api/v1/qualidade/auditoria/controles/remocao_base_negativa/",
            {
                "dados": {
                    "cliente": "RISK MANAGER",
                    "cpf": "703.309.256-36",
                    "protocolo": "7386251",
                    "tipo_acao": "Exclusão",
                    "situacao": "Nova",
                    "data_abertura": "2026-07-10",
                }
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        pk = create.data["id"]
        self.assertEqual(create.data["dados"]["cliente"], "RISK MANAGER")
        self.assertEqual(create.data["situacao"], "Nova")
        self.assertIsNotNone(create.data["sla_seconds"])
        self.assertIsNotNone(create.data["sla_label"])

        listing = self.client.get("/api/v1/qualidade/auditoria/controles/remocao_base_negativa/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data["results"]), 1)

        patch = self.client.patch(
            f"/api/v1/qualidade/auditoria/controles/remocao_base_negativa/{pk}/",
            {"dados": {**create.data["dados"], "situacao": "Finalizada"}},
            format="json",
        )
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(patch.data["situacao"], "Finalizada")

        delete = self.client.delete(f"/api/v1/qualidade/auditoria/controles/remocao_base_negativa/{pk}/")
        self.assertEqual(delete.status_code, 204)
        self.assertEqual(AuditoriaControleRegistro.objects.count(), 0)

    def test_idas_bio_schema(self):
        meta = self.client.get("/api/v1/qualidade/auditoria/controles/solicitacoes_idas_bio/meta/")
        self.assertEqual(meta.status_code, 200)
        by_key = {field["key"]: field for field in meta.data["fields"]}
        self.assertEqual(by_key["demanda_origem"]["kind"], "link")
        self.assertEqual(by_key["demanda_idas"]["kind"], "link")
        self.assertEqual(by_key["solicitante"]["kind"], "select")
        self.assertEqual(by_key["solicitante"]["catalog"], "solicitantes_idas")
        self.assertEqual(by_key["workflow"]["kind"], "select")
        self.assertEqual(by_key["workflow"]["catalog"], "workflow")
        self.assertIn("solicitantes", meta.data)
        self.assertEqual(by_key["situacao"]["kind"], "select")
        self.assertIn("Em andamento", by_key["situacao"]["options"])

        create = self.client.post(
            "/api/v1/qualidade/auditoria/controles/solicitacoes_idas_bio/",
            {
                "dados": {
                    "cliente": "PAGSEGURO",
                    "demanda_idas": "IDAS-17435",
                    "solicitante": "ELTON MARQUES",
                    "situacao": "Em andamento",
                }
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        self.assertEqual(create.data["dados"]["demanda_idas"], "IDAS-17435")

    @patch("apps.auditoria.services.controles.agents_with_any_permission")
    def test_idas_bio_solicitantes_use_contestacao_interna_fraud(self, mock_agents):
        from apps.access.registry import (
            QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
            QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
        )
        from apps.auditoria.services.controles import TIPO_SOLICITACOES_IDAS_BIO, meta_for

        mock_agents.return_value = [{"value": "Ana Silva", "label": "Ana Silva"}]
        meta = meta_for(TIPO_SOLICITACOES_IDAS_BIO)
        mock_agents.assert_called_once_with(
            QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
            QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
        )
        self.assertEqual(meta["solicitantes"], [{"value": "Ana Silva", "label": "Ana Silva"}])

    def test_base_positiva_schema_matches_pattern(self):
        meta = self.client.get("/api/v1/qualidade/auditoria/controles/remocao_base_positiva/meta/")
        self.assertEqual(meta.status_code, 200)
        by_key = {field["key"]: field for field in meta.data["fields"]}
        self.assertEqual(by_key["tipo_acao"]["kind"], "select")
        self.assertEqual(by_key["tipo_acao"]["catalog"], "tipo_acao_controle")
        self.assertEqual(by_key["cpf"]["kind"], "cpf")
        self.assertEqual(by_key["motivo"]["kind"], "select")
        self.assertEqual(by_key["demanda_origem"]["kind"], "link")
        self.assertEqual(by_key["selfie_higienizada"]["kind"], "image")
        self.assertEqual(by_key["situacao"]["kind"], "select")

    def test_selfie_upload_base_negativa(self):
        buffer = BytesIO()
        Image.new("RGB", (8, 8), color=(20, 40, 60)).save(buffer, format="PNG")
        upload = SimpleUploadedFile("selfie.png", buffer.getvalue(), content_type="image/png")

        create = self.client.post(
            "/api/v1/qualidade/auditoria/controles/remocao_base_negativa/",
            {
                "dados": '{"cliente":"RISK MANAGER","situacao":"Nova"}',
                "selfie": upload,
            },
            format="multipart",
        )
        self.assertEqual(create.status_code, 201, create.data)
        self.assertTrue(create.data["selfie_url"])
        self.assertTrue(create.data["dados"]["selfie_higienizada"])

        pk = create.data["id"]
        remove = self.client.patch(
            f"/api/v1/qualidade/auditoria/controles/remocao_base_negativa/{pk}/",
            {
                "dados": '{"cliente":"RISK MANAGER","situacao":"Nova"}',
                "remove_selfie": "1",
            },
            format="multipart",
        )
        self.assertEqual(remove.status_code, 200, remove.data)
        self.assertIsNone(remove.data["selfie_url"])
        self.assertEqual(remove.data["dados"]["selfie_higienizada"], "")
