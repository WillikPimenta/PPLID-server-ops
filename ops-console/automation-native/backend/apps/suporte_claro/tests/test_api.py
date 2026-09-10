# -*- coding: utf-8 -*-
import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

User = get_user_model()

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@override_settings(
    MEDIA_ROOT="/tmp/pplid_suporte_claro_test_media",
    JIRA_BASE_URL="https://jira.test.local",
)
class SuporteClaroApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="agent001", password="pass12345", email="agent001@test.local"
        )
        self.other = User.objects.create_user(
            username="agent002", password="pass12345", email="agent002@test.local"
        )
        self.client.force_authenticate(user=self.user)
        self.url = "/api/v1/suporte-claro/registros/"
        self.received_at = timezone.now().replace(microsecond=0).isoformat()
        self._jira_patcher = patch(
            "apps.suporte_claro.views.create_and_link_jira_for_registro",
            return_value={"ok": True, "issue_key": "TEST-1", "skipped": False},
        )
        self._jira_patcher.start()
        self.addCleanup(self._jira_patcher.stop)

    def _payload(self, protocolo="123456", **extra):
        titulo = extra.pop("titulo", f"Demanda {protocolo}")
        data = {
            "titulo": titulo,
            "protocolo": protocolo,
            "received_at": self.received_at,
            **extra,
        }
        return data

    def _png_file(self, name: str = "foto.png") -> SimpleUploadedFile:
        return SimpleUploadedFile(name, PNG_1X1, content_type="image/png")

    def test_create_registro_with_image(self):
        response = self.client.post(
            self.url,
            self._payload(
                protocolo="123456",
                irregularidade="Divergência no SLA",
                avaliacao="Aguardando retorno do cliente",
                sent_by="Maria Silva",
                origem="teams",
                status="aberto",
                images=self._png_file(),
            ),
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("Suporte N1 registrado", data["message"])
        registro = data["registro"]
        self.assertEqual(registro["titulo"], "Demanda 123456")
        self.assertEqual(registro["protocolo"], "123456")
        self.assertEqual(len(registro["protocolos"]), 1)
        self.assertEqual(registro["protocolos"][0]["numero"], "123456")
        self.assertEqual(registro["status"], "aberto")
        self.assertEqual(registro["created_by_username"], "agent001")
        self.assertEqual(len(registro["anexos"]), 1)
        self.assertTrue(registro["anexos"][0]["url"])

    def test_create_uses_informed_customer_response_time(self):
        from django.utils.dateparse import parse_datetime

        now = timezone.now().replace(microsecond=0)
        received_at = now - timedelta(hours=1)
        retorno_at = now - timedelta(minutes=20)
        response = self.client.post(
            self.url,
            self._payload(
                protocolo="RET-001",
                origem="teams",
                avaliacao="Cliente respondido antes do cadastro no portal.",
                received_at=received_at.isoformat(),
                retorno_at=retorno_at.isoformat(),
            ),
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        returned_at = response.json()["registro"]["retorno_at"]
        self.assertEqual(parse_datetime(returned_at), retorno_at)

    def test_create_without_titulo_returns_400(self):
        response = self.client.post(
            self.url,
            {"protocolo": "999", "received_at": self.received_at, "origem": "teams"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("titulo", response.json()["detail"].lower())

    def test_create_with_multiple_protocolos(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Multiplos protocolos",
                "protocolos": json.dumps(
                    [
                        {"numero": "111", "comentario": "Primeiro"},
                        {"numero": "222", "comentario": ""},
                    ]
                ),
                "received_at": self.received_at,
                "origem": "teams",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(registro["titulo"], "Multiplos protocolos")
        self.assertEqual(registro["protocolo"], "111")
        self.assertEqual(len(registro["protocolos"]), 2)
        self.assertEqual(registro["protocolos"][0]["comentario"], "Primeiro")

    def test_create_with_multiple_emails(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Multiplos e-mails",
                "protocolo": "MAIL-1",
                "emails": json.dumps(
                    [
                        {"endereco": "ana@empresa.com", "comentario": ""},
                        {"endereco": "suporte@cliente.com", "comentario": "Encaminhado"},
                    ]
                ),
                "received_at": self.received_at,
                "origem": "email",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        registro = response.json()["registro"]
        self.assertEqual(len(registro["emails"]), 2)
        self.assertEqual(registro["sent_by"], "ana@empresa.com")
        self.assertEqual(registro["emails"][1]["endereco"], "suporte@cliente.com")

    def test_create_with_max_protocolos_does_not_500(self):
        protocolos = [
            {"numero": f"{100000 + index}", "comentario": ""} for index in range(107)
        ]
        response = self.client.post(
            self.url,
            {
                "titulo": "Volume alto de protocolos",
                "protocolos": json.dumps(protocolos),
                "received_at": self.received_at,
                "origem": "email",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        registro = response.json()["registro"]
        self.assertEqual(registro["protocolo"], "100000")
        self.assertEqual(len(registro["protocolos"]), 107)

    def test_create_exceeds_max_protocolos_returns_400(self):
        protocolos = [
            {"numero": f"{200000 + index}", "comentario": ""} for index in range(151)
        ]
        response = self.client.post(
            self.url,
            {
                "titulo": "Excesso de protocolos",
                "protocolos": json.dumps(protocolos),
                "received_at": self.received_at,
                "origem": "email",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("150", response.json()["detail"])

    def test_create_without_origem_returns_400(self):
        response = self.client.post(
            self.url,
            self._payload(protocolo="999", origem=""),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("origem", response.json()["detail"].lower())

    def test_create_with_status_em_andamento(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda ST-3",
                "protocolo": "ST-3",
                "received_at": self.received_at,
                "origem": "email",
                "status": "em_atendimento",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["registro"]["status"], "em_atendimento")
        self.assertEqual(response.json()["registro"]["origem"], "email")

    def test_create_with_avaliacao_stays_aberto(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda ST-4",
                "protocolo": "ST-4",
                "received_at": self.received_at,
                "origem": "teams",
                "avaliacao": "Retorno parcial ao cliente",
                "status": "aberto",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(registro["avaliacao"], "Retorno parcial ao cliente")
        self.assertEqual(registro["status"], "aberto")

    def test_create_with_avaliacao_and_concluido(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda ST-5",
                "protocolo": "ST-5",
                "received_at": self.received_at,
                "origem": "ligacao",
                "avaliacao": "Cliente orientado e caso encerrado",
                "status": "concluido",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(registro["avaliacao"], "Cliente orientado e caso encerrado")
        self.assertEqual(registro["status"], "concluido")

    def test_create_concluido_without_avaliacao_returns_400(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda ST-6",
                "protocolo": "ST-6",
                "received_at": self.received_at,
                "origem": "email",
                "status": "concluido",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("concluid", response.json()["detail"].lower())

    def test_create_with_jira_chamado(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda ST-JIRA",
                "protocolo": "ST-JIRA",
                "received_at": self.received_at,
                "origem": "teams",
                "chamado_sistema": "jira",
                "chamado_codigo": "OPS-123",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(registro["chamado_sistema"], "jira")
        self.assertEqual(registro["chamado_codigo"], "OPS-123")
        self.assertEqual(len(registro["chamados_externos"]), 1)

    def test_create_with_multiple_chamados(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda ST-MULTI",
                "protocolo": "ST-MULTI",
                "received_at": self.received_at,
                "origem": "teams",
                "chamados_externos": json.dumps(
                    [
                        {"sistema": "jira", "codigo": "OPS-1"},
                        {"sistema": "service", "codigo": "REQ-9"},
                    ]
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(len(registro["chamados_externos"]), 2)
        self.assertEqual(registro["chamado_sistema"], "jira")
        self.assertEqual(registro["chamado_codigo"], "OPS-1")

    def test_create_chamado_codigo_without_sistema_returns_400(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda ST-BAD",
                "protocolo": "ST-BAD",
                "received_at": self.received_at,
                "origem": "email",
                "chamado_codigo": "OPS-999",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_patch_chamado_externo(self):
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda PATCH-CH",
                "protocolo": "PATCH-CH",
                "received_at": self.received_at,
                "origem": "ligacao",
            },
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        response = self.client.patch(
            f"{self.url}{registro_id}/",
            {
                "chamado_sistema": "service",
                "chamado_codigo": "REQ0001",
                "chamado_url": "https://service.example/req/1",
            },
        )
        self.assertEqual(response.status_code, 200)
        registro = response.json()["registro"]
        self.assertEqual(registro["chamado_sistema"], "service")
        self.assertEqual(registro["chamado_codigo"], "REQ0001")
        self.assertEqual(registro["chamado_url"], "https://service.example/req/1")
        self.assertEqual(registro["chamado_link"], "https://service.example/req/1")

    def test_create_without_protocolo_succeeds_with_titulo(self):
        response = self.client.post(
            self.url,
            {"titulo": "Sem protocolo", "received_at": self.received_at, "origem": "teams"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        registro = response.json()["registro"]
        self.assertEqual(registro["titulo"], "Sem protocolo")
        self.assertEqual(registro["protocolo"], "")
        self.assertEqual(registro["protocolos"], [])

    def test_invalid_image_returns_400(self):
        bad = SimpleUploadedFile("doc.pdf", b"%PDF-", content_type="application/pdf")
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda 999",
                "protocolo": "999",
                "received_at": self.received_at,
                "origem": "teams",
                "images": bad,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_create_with_eml_anexo(self):
        eml = SimpleUploadedFile(
            "mensagem.eml",
            b"From: a@test.local\r\nSubject: Hi\r\n\r\nBody",
            content_type="message/rfc822",
        )
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda EML",
                "protocolo": "EML-1",
                "received_at": self.received_at,
                "origem": "email",
                "images": eml,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        anexos = response.json()["registro"]["anexos"]
        self.assertEqual(len(anexos), 1)
        self.assertTrue(anexos[0]["original_name"].lower().endswith(".eml"))

    def test_list_returns_shared_visibility(self):
        self.client.post(
            self.url,
            {"titulo": "Demanda A1", "protocolo": "A1", "received_at": self.received_at, "origem": "ligacao"},
            format="multipart",
        )
        self.client.force_authenticate(user=self.other)
        self.client.post(
            self.url,
            {"titulo": "Demanda B2", "protocolo": "B2", "received_at": self.received_at, "origem": "teams"},
            format="multipart",
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 2)
        protocolos = {item["protocolo"] for item in items}
        self.assertEqual(protocolos, {"A1", "B2"})

    def test_list_filter_created_by_me(self):
        self.client.post(
            self.url,
            {"titulo": "Demanda MINE", "protocolo": "MINE", "received_at": self.received_at, "origem": "ligacao"},
            format="multipart",
        )
        self.client.force_authenticate(user=self.other)
        self.client.post(
            self.url,
            {"titulo": "Demanda OTHER", "protocolo": "OTHER", "received_at": self.received_at, "origem": "teams"},
            format="multipart",
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url, {"created_by": "me"})
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["protocolo"], "MINE")

    def test_list_filter_status_pendentes(self):
        self.client.post(
            self.url,
            {
                "titulo": "Demanda OPEN",
                "protocolo": "OPEN",
                "received_at": self.received_at,
                "origem": "teams",
                "status": "aberto",
            },
            format="multipart",
        )
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda DONE",
                "protocolo": "DONE",
                "received_at": self.received_at,
                "origem": "email",
                "avaliacao": "Fechado",
                "status": "concluido",
            },
            format="multipart",
        )
        self.assertEqual(create.status_code, 201)
        response = self.client.get(self.url, {"status": "pendentes"})
        self.assertEqual(response.status_code, 200)
        protocolos = {item["protocolo"] for item in response.json()["items"]}
        self.assertIn("OPEN", protocolos)
        self.assertNotIn("DONE", protocolos)

    def test_list_filter_status_concluido(self):
        self.client.post(
            self.url,
            {
                "titulo": "Demanda OPEN-2",
                "protocolo": "OPEN-2",
                "received_at": self.received_at,
                "origem": "teams",
            },
            format="multipart",
        )
        self.client.post(
            self.url,
            {
                "titulo": "Demanda DONE-2",
                "protocolo": "DONE-2",
                "received_at": self.received_at,
                "origem": "email",
                "avaliacao": "Ok",
                "status": "concluido",
            },
            format="multipart",
        )
        response = self.client.get(self.url, {"status": "concluido"})
        self.assertEqual(response.status_code, 200)
        protocolos = {item["protocolo"] for item in response.json()["items"]}
        self.assertEqual(protocolos, {"DONE-2"})

    def test_list_search_q_by_protocolo_and_jira(self):
        self.client.post(
            self.url,
            {
                "titulo": "Demanda ALPHA",
                "protocolo": "PROT-ALPHA",
                "received_at": self.received_at,
                "origem": "teams",
            },
            format="multipart",
        )
        self.client.post(
            self.url,
            {
                "titulo": "Demanda BETA",
                "protocolo": "PROT-BETA",
                "received_at": self.received_at,
                "origem": "email",
                "chamado_sistema": "jira",
                "chamado_codigo": "OPS-SEARCH-99",
            },
            format="multipart",
        )
        by_proto = self.client.get(self.url, {"q": "PROT-ALPHA"})
        self.assertEqual(by_proto.status_code, 200)
        self.assertEqual({i["protocolo"] for i in by_proto.json()["items"]}, {"PROT-ALPHA"})

        by_jira = self.client.get(self.url, {"q": "OPS-SEARCH"})
        self.assertEqual(by_jira.status_code, 200)
        self.assertEqual({i["protocolo"] for i in by_jira.json()["items"]}, {"PROT-BETA"})

        too_short = self.client.get(self.url, {"q": "A"})
        self.assertEqual(too_short.status_code, 200)
        self.assertGreaterEqual(len(too_short.json()["items"]), 2)

    def test_list_meta_includes_cadastradores(self):
        self.client.post(
            self.url,
            {"titulo": "Demanda META-1", "protocolo": "META-1", "received_at": self.received_at, "origem": "teams"},
            format="multipart",
        )
        response = self.client.get(self.url)
        meta = response.json()["meta"]
        self.assertIn("cadastradores", meta)
        self.assertEqual(len(meta["cadastradores"]), 1)
        self.assertEqual(meta["cadastradores"][0]["username"], "agent001")
        self.assertEqual(meta["cadastradores"][0]["count"], 1)

    def test_detail_includes_anexos(self):
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda DET-1",
                "protocolo": "DET-1",
                "received_at": self.received_at,
                "origem": "teams",
                "images": self._png_file(),
            },
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        response = self.client.get(f"{self.url}{registro_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["anexos"]), 1)

    def test_add_anexos_after_create(self):
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda ANX-1",
                "protocolo": "ANX-1",
                "received_at": self.received_at,
                "origem": "teams",
                "images": self._png_file("primeira.png"),
            },
            format="multipart",
        )
        self.assertEqual(create.status_code, 201, create.content)
        registro_id = create.json()["registro"]["id"]
        self.assertEqual(len(create.json()["registro"]["anexos"]), 1)

        response = self.client.post(
            f"{self.url}{registro_id}/anexos/",
            {"images": self._png_file("segunda.png")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertIn("anexo", data["message"].lower())
        self.assertEqual(len(data["registro"]["anexos"]), 2)
        names = {a["original_name"] for a in data["registro"]["anexos"]}
        self.assertEqual(names, {"primeira.png", "segunda.png"})

    def test_add_anexos_respects_max(self):
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda ANX-MAX",
                "protocolo": "ANX-MAX",
                "received_at": self.received_at,
                "origem": "teams",
                "images": [
                    self._png_file(f"a{i}.png") for i in range(5)
                ],
            },
            format="multipart",
        )
        self.assertEqual(create.status_code, 201, create.content)
        registro_id = create.json()["registro"]["id"]
        response = self.client.post(
            f"{self.url}{registro_id}/anexos/",
            {"images": self._png_file("extra.png")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("máximo", response.json()["detail"].lower())

    def test_add_anexos_does_not_push_to_jira(self):
        from apps.suporte_claro.models import SuporteClaroChamadoExterno, SuporteClaroRegistro
        from unittest.mock import patch

        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda ANX-JIRA",
                "protocolo": "ANX-JIRA",
                "received_at": self.received_at,
                "origem": "teams",
            },
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        registro = SuporteClaroRegistro.objects.get(pk=registro_id)
        SuporteClaroChamadoExterno.objects.create(
            registro=registro,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
            codigo="TEST-99",
            url="https://jira.test.local/browse/TEST-99",
            natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO,
        )

        with patch("apps.suporte_claro.services.jira_rest.attach_registro_anexos_to_jira") as mock_attach:
            response = self.client.post(
                f"{self.url}{registro_id}/anexos/",
                {"images": self._png_file("jira.png")},
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.content)
            mock_attach.assert_not_called()
        self.assertNotIn("jira_attachments", response.json())
        self.assertNotIn("jira", response.json()["message"].lower())

    def test_patch_status_updates_registro(self):
        create = self.client.post(
            self.url,
            {"titulo": "Demanda ST-1", "protocolo": "ST-1", "received_at": self.received_at, "origem": "email"},
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        response = self.client.patch(
            f"{self.url}{registro_id}/status/",
            {"status": "em_atendimento"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["registro"]["status"], "em_atendimento")
        detail = self.client.get(f"{self.url}{registro_id}/")
        historico = detail.json().get("historico") or []
        self.assertTrue(any(h["action"] == "status" for h in historico))

    def test_patch_registro_logs_edit(self):
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda EDIT-1",
                "protocolo": "EDIT-1",
                "irregularidade": "Texto original",
                "received_at": self.received_at,
                "origem": "email",
                "status": "aberto",
            },
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        response = self.client.patch(
            f"{self.url}{registro_id}/",
            {
                "irregularidade": "Texto atualizado",
                "avaliacao": "Retorno parcial",
                "status": "em_atendimento",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        registro = response.json()["registro"]
        self.assertEqual(registro["irregularidade"], "Texto atualizado")
        self.assertEqual(registro["avaliacao"], "Retorno parcial")
        self.assertGreaterEqual(len(registro.get("historico") or []), 1)

    def test_patch_received_at_past_on_imported_registro(self):
        from datetime import timedelta

        from django.utils.dateparse import parse_datetime

        from apps.suporte_claro.models import SuporteClaroImportRef, SuporteClaroRegistro

        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda RETRO-1",
                "protocolo": "RETRO-1",
                "irregularidade": "Chamado antigo",
                "received_at": self.received_at,
                "origem": "email",
                "status": "aberto",
            },
            format="multipart",
        )
        self.assertEqual(create.status_code, 201)
        registro_id = create.json()["registro"]["id"]
        SuporteClaroImportRef.objects.create(
            linha_id="LINHA-RETRO-1",
            registro_id=registro_id,
            imported_by=self.user,
        )

        past = (timezone.now() - timedelta(days=30)).replace(microsecond=0)
        response = self.client.patch(
            f"{self.url}{registro_id}/",
            {"received_at": past.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        registro = response.json()["registro"]
        self.assertTrue(registro.get("is_imported"))
        parsed = parse_datetime(registro["received_at"].replace("Z", "+00:00"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.replace(microsecond=0), past)
        detail = self.client.get(f"{self.url}{registro_id}/")
        historico = detail.json().get("historico") or []
        self.assertTrue(
            any(h.get("field_name") == "received_at" for h in historico),
            historico,
        )
        db_reg = SuporteClaroRegistro.objects.get(pk=registro_id)
        self.assertEqual(db_reg.received_at.replace(microsecond=0), past)

    def test_patch_retorno_at_updates_sla_anchor(self):
        from datetime import timedelta

        from django.utils.dateparse import parse_datetime

        from apps.suporte_claro.models import SuporteClaroRegistro

        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda RETORNO-1",
                "protocolo": "RETORNO-1",
                "received_at": self.received_at,
                "origem": "email",
                "status": "concluido",
                "avaliacao": "Cliente orientado",
            },
            format="multipart",
        )
        self.assertEqual(create.status_code, 201, create.content)
        registro_id = create.json()["registro"]["id"]
        received = parse_datetime(create.json()["registro"]["received_at"].replace("Z", "+00:00"))
        concluded = (received + timedelta(hours=2)).replace(microsecond=0)
        response = self.client.patch(
            f"{self.url}{registro_id}/",
            {
                "status": "concluido",
                "avaliacao": "Cliente orientado",
                "retorno_at": concluded.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        registro = response.json()["registro"]
        parsed = parse_datetime((registro.get("retorno_at") or "").replace("Z", "+00:00"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.replace(microsecond=0), concluded)
        db_reg = SuporteClaroRegistro.objects.get(pk=registro_id)
        self.assertEqual(db_reg.retorno_at.replace(microsecond=0), concluded)

    def test_patch_invalid_status_returns_400(self):
        create = self.client.post(
            self.url,
            {"titulo": "Demanda ST-2", "protocolo": "ST-2", "received_at": self.received_at, "origem": "ligacao"},
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        response = self.client.patch(
            f"{self.url}{registro_id}/status/",
            {"status": "invalido"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_export_xlsx_returns_spreadsheet(self):
        self.client.post(
            self.url,
            {
                "titulo": "Demanda EXP-1",
                "protocolo": "EXP-1",
                "irregularidade": "Teste export",
                "received_at": self.received_at,
                "origem": "teams",
            },
            format="multipart",
        )
        response = self.client.get("/api/v1/suporte-claro/registros/export.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            response["Content-Type"],
        )
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertIn("attachment", response["Content-Disposition"])

    def test_list_filters_by_date_range(self):
        self.client.post(
            self.url,
            {
                "titulo": "Demanda IN-RANGE",
                "protocolo": "IN-RANGE",
                "received_at": self.received_at,
                "origem": "teams",
            },
            format="multipart",
        )
        today = timezone.localdate()
        response = self.client.get(
            self.url,
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 1)
        self.assertIn("meta", data)
        self.assertEqual(data["meta"]["stats"]["total"], 1)
        self.assertIn("attention_points", data["meta"])
        self.assertIsInstance(data["meta"]["attention_points"], list)

    def test_list_invalid_date_range_returns_400(self):
        response = self.client.get(
            self.url,
            {"date_from": "2026-06-10", "date_to": "2026-06-01"},
        )
        self.assertEqual(response.status_code, 400)

    def test_export_pdf_returns_report(self):
        self.client.post(
            self.url,
            {
                "titulo": "Demanda PDF-1",
                "protocolo": "PDF-1",
                "irregularidade": "Teste PDF",
                "avaliacao": "Retorno ok",
                "received_at": self.received_at,
                "origem": "email",
                "status": "concluido",
            },
            format="multipart",
        )
        response = self.client.get("/api/v1/suporte-claro/registros/report.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("attachment", response["Content-Disposition"])

    def test_export_pdf_splits_large_demand_table_across_pages(self):
        from apps.suporte_claro.models import SuporteClaroRegistro

        received_at = timezone.now().replace(microsecond=0)
        SuporteClaroRegistro.objects.bulk_create(
            [
                SuporteClaroRegistro(
                    titulo=f"Demanda PDF volume {index}",
                    protocolo=f"PDF-VOL-{index:03d}",
                    irregularidade="Demanda usada para validar a quebra da tabela entre paginas.",
                    received_at=received_at,
                    origem=SuporteClaroRegistro.ORIGEM_EMAIL,
                    status=SuporteClaroRegistro.STATUS_ABERTO,
                    created_by=self.user,
                )
                for index in range(55)
            ]
        )

        response = self.client.get("/api/v1/suporte-claro/registros/report.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 10_000)

    def test_export_registro_ficha_pdf(self):
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda FICHA-99",
                "protocolo": "FICHA-99",
                "irregularidade": "Cliente solicitou comprovante",
                "avaliacao": "Tratativa concluida",
                "received_at": self.received_at,
                "sent_by": "Cliente X",
                "origem": "teams",
                "status": "concluido",
            },
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        response = self.client.get(f"/api/v1/suporte-claro/registros/{registro_id}/ficha.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("FICHA-99", response["Content-Disposition"])

    def test_export_registro_ficha_pdf_accessible_to_other_user(self):
        create = self.client.post(
            self.url,
            {
                "titulo": "Demanda PRIV-1",
                "protocolo": "PRIV-1",
                "received_at": self.received_at,
                "origem": "email",
                "status": "aberto",
            },
            format="multipart",
        )
        registro_id = create.json()["registro"]["id"]
        self.client.force_authenticate(user=self.other)
        response = self.client.get(f"/api/v1/suporte-claro/registros/{registro_id}/ficha.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_create_incidente_without_protocolo_auto_code(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Lentidao CRM recorrente",
                "protocolos": "[]",
                "received_at": self.received_at,
                "origem": "teams",
                "categoria": "incidente",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(registro["protocolo"], f"INC-{registro['id']}")
        self.assertEqual(len(registro["protocolos"]), 1)
        self.assertEqual(registro["protocolos"][0]["numero"], f"INC-{registro['id']}")
        self.assertEqual(registro["tipo_incidente"], "")

    def test_create_demanda_without_protocolo_succeeds(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Demanda sem protocolo",
                "protocolos": "[]",
                "received_at": self.received_at,
                "origem": "teams",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        registro = response.json()["registro"]
        self.assertEqual(registro["protocolo"], "")
        self.assertEqual(registro["protocolos"], [])

    def test_create_incidente_ignores_client_protocolo(self):
        response = self.client.post(
            self.url,
            self._payload(
                protocolo="numero",
                origem="teams",
                categoria="incidente",
            ),
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(registro["protocolo"], f"INC-{registro['id']}")

    def test_create_incidente_with_tipo_legado(self):
        response = self.client.post(
            self.url,
            self._payload(
                protocolo="INC-1",
                origem="teams",
                categoria="incidente",
                tipo_incidente="lentidao",
            ),
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        registro = response.json()["registro"]
        self.assertEqual(registro["categoria"], "incidente")
        self.assertEqual(registro["tipo_incidente"], "lentidao")
        self.assertEqual(registro["tipo_incidente_label"], "Lentidão")
        self.assertEqual(registro["vinculos"], [])
        self.assertIn("somente portal", response.json()["message"].lower())
        self.assertNotIn("jira_auto", response.json())

    def test_create_incidente_skips_jira_auto(self):
        with patch(
            "apps.suporte_claro.views.create_and_link_jira_for_registro"
        ) as mock_jira:
            response = self.client.post(
                self.url,
                self._payload(
                    protocolo="INC-NOJIRA",
                    origem="teams",
                    categoria="incidente",
                ),
                format="multipart",
            )
        self.assertEqual(response.status_code, 201)
        mock_jira.assert_not_called()

    def test_vinculos_max_30(self):
        base_ids = []
        for i in range(32):
            created = self.client.post(
                self.url,
                self._payload(
                    protocolo=f"INC-MAX-{i}",
                    origem="teams",
                    categoria="incidente",
                    titulo=f"Incidente max {i}",
                ),
                format="multipart",
            ).json()["registro"]
            base_ids.append(created["id"])
        target = base_ids[0]
        too_many = base_ids[1:]  # 31 vínculos
        response = self.client.patch(
            f"{self.url}{target}/",
            {"vinculos_ids": too_many},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("30", response.json()["detail"])

    def test_create_incidente_without_tipo_ok(self):
        response = self.client.post(
            self.url,
            self._payload(
                protocolo="INC-2",
                origem="teams",
                categoria="incidente",
            ),
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["registro"]["tipo_incidente"], "")

    def test_create_incidente_with_sn_and_jira_chamados(self):
        response = self.client.post(
            self.url,
            {
                "titulo": "Incidente com SN e Jira",
                "protocolos": "[]",
                "received_at": self.received_at,
                "origem": "teams",
                "categoria": "incidente",
                "chamados_externos": json.dumps(
                    [
                        {"sistema": "service", "codigo": "INC999001", "url": ""},
                        {
                            "sistema": "jira",
                            "codigo": "IDAS-24076",
                            "url": "",
                            "tratado_por": "Silva, Thiago",
                        },
                    ]
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        externos = response.json()["registro"]["chamados_externos"]
        sistemas = {c["sistema"]: c for c in externos}
        self.assertIn("service", sistemas)
        self.assertIn("jira", sistemas)
        self.assertEqual(sistemas["jira"]["tratado_por"], "Silva, Thiago")
        self.assertEqual(sistemas["service"]["codigo"], "INC999001")

    def test_filter_categoria_incidente(self):
        self.client.post(
            self.url,
            self._payload(protocolo="DEM-F1", origem="teams", categoria="demanda"),
            format="multipart",
        )
        self.client.post(
            self.url,
            self._payload(
                protocolo="INC-F1",
                origem="teams",
                categoria="incidente",
            ),
            format="multipart",
        )
        response = self.client.get(self.url, {"categoria": "incidente"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["categoria"], "incidente")
        self.assertGreaterEqual(data["meta"]["stats"]["incidentes"], 1)

    def test_vincular_dois_incidentes(self):
        a = self.client.post(
            self.url,
            self._payload(
                protocolo="INC-V1",
                origem="teams",
                categoria="incidente",
            ),
            format="multipart",
        ).json()["registro"]
        b = self.client.post(
            self.url,
            self._payload(
                protocolo="INC-V2",
                origem="email",
                categoria="incidente",
                vinculos_ids=json.dumps([a["id"]]),
            ),
            format="multipart",
        ).json()["registro"]
        self.assertEqual(len(b["vinculos"]), 1)
        self.assertEqual(b["vinculos"][0]["id"], a["id"])

        detail_a = self.client.get(f"{self.url}{a['id']}/").json()
        self.assertEqual(len(detail_a["vinculos"]), 1)
        self.assertEqual(detail_a["vinculos"][0]["id"], b["id"])

        patch = self.client.patch(
            f"{self.url}{a['id']}/",
            {"vinculos_ids": []},
            format="json",
        )
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(patch.json()["registro"]["vinculos"], [])

    def test_vincular_demanda_normal_returns_400(self):
        demanda = self.client.post(
            self.url,
            self._payload(protocolo="DEM-V1", origem="teams"),
            format="multipart",
        ).json()["registro"]
        response = self.client.post(
            self.url,
            self._payload(
                protocolo="INC-V3",
                origem="teams",
                categoria="incidente",
                vinculos_ids=json.dumps([demanda["id"]]),
            ),
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("mesmo tipo", response.json()["detail"].lower())

    def test_vincular_duas_demandas(self):
        a = self.client.post(
            self.url,
            self._payload(protocolo="DEM-V1", origem="teams", categoria="demanda"),
            format="multipart",
        ).json()["registro"]
        b = self.client.post(
            self.url,
            self._payload(
                protocolo="DEM-V2",
                origem="email",
                categoria="demanda",
                vinculos_ids=json.dumps([a["id"]]),
            ),
            format="multipart",
        )
        self.assertEqual(b.status_code, 201)
        self.assertEqual(b.json()["registro"]["vinculos"][0]["id"], a["id"])

    def test_vincular_self_returns_400(self):
        a = self.client.post(
            self.url,
            self._payload(
                protocolo="INC-SELF",
                origem="teams",
                categoria="incidente",
            ),
            format="multipart",
        ).json()["registro"]
        response = self.client.patch(
            f"{self.url}{a['id']}/",
            {"vinculos_ids": [a["id"]]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("si mesmo", response.json()["detail"].lower())
