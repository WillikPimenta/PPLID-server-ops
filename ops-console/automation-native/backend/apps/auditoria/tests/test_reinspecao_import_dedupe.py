from __future__ import annotations

import random
from datetime import datetime
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
    ReinspecaoOcorrencia,
)
from apps.auditoria.services.auditoria_compliance_import import sample_compliance_import
from apps.auditoria.services.qualidade_promocao import promover_pendente_reinspecao
from apps.auditoria.services.reinspecao_fila import set_fila_contexto, reset_fila_contexto
from apps.auditoria.services.reinspecao_import import parse_reinspecao_workbook
from apps.auditoria.services.reinspecao_import_dedupe import (
    MOTIVO_DUPLICADO_ARQUIVO,
    MOTIVO_JA_ANALISADO,
    MOTIVO_JA_NA_FILA,
    build_reinspecao_dedupe_key,
)
from apps.auditoria.testing.reinspecao_mapping import seed_test_reinspecao_mapping

User = get_user_model()

SAMPLE_CSV = """Protocolo;Tipo/Status conferencia;Matricula Inspetor;Nome Inspetor;Data/Hora da Conferência
1001;Reclassificação;C11111Q;Agente Um;27/07/2026 14:35:20
1002;Reclassificação;C11111Q;Agente Um;27/07/2026 14:35:20
2001;Reclassificação;C22222A;Agente Dois;27/07/2026 15:00:00
"""


def build_duplicate_reinspecao_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    headers = [
        "Protocolo",
        "Data da contestação",
        "Data de resposta",
        "Matricula do Inspetor",
        "Descrição das Irregularidades",
    ]
    sheet.append(headers)
    row = [
        18426529,
        datetime(2026, 7, 15, 17, 34, 54),
        "-",
        "C19131Q",
        "IC - 175 - CPF ausente",
    ]
    sheet.append(row)
    sheet.append(row)
    sheet.append(
        [
            18426529,
            datetime(2026, 7, 16, 10, 0, 0),
            "-",
            "C19131Q",
            "IC - 175 - CPF ausente",
        ]
    )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@override_settings(ACCESS_ENFORCEMENT=False)
class ReinspecaoImportDedupeTests(TestCase):
    def setUp(self):
        seed_test_reinspecao_mapping()
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="dedupe_user",
            email="dedupe@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)
        self.ctx_token = set_fila_contexto("reinspecao")

    def tearDown(self):
        reset_fila_contexto(self.ctx_token)

    def test_duplicate_in_file_is_ignored(self):
        preview = parse_reinspecao_workbook(build_duplicate_reinspecao_workbook())
        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_falhas, 2)
        self.assertEqual(preview.total_ignorados_duplicata, 1)
        self.assertEqual(len(preview.protocolos_ignorados), 1)
        self.assertEqual(preview.protocolos_ignorados[0]["motivo"], MOTIVO_DUPLICADO_ARQUIVO)

    def test_same_protocol_different_date_is_allowed(self):
        preview = parse_reinspecao_workbook(build_duplicate_reinspecao_workbook())
        datas = {row.data_contestacao.date() for row in preview.rows}
        self.assertEqual(len(datas), 2)

    def test_duplicate_in_portal_pendente_is_ignored(self):
        QualidadePendenteReinspecao.objects.create(
            protocolo="18426529",
            usuario="c19131q",
            descricao_irregularidades="IC - 175 - CPF ausente",
            data_contestacao=timezone.make_aware(datetime(2026, 7, 15, 17, 34, 54)),
            contexto="reinspecao",
            tipo_falha="reinspecao",
        )
        preview = parse_reinspecao_workbook(build_duplicate_reinspecao_workbook())
        self.assertEqual(preview.total_falhas, 1)
        portal_hits = [
            item for item in preview.protocolos_ignorados if item["motivo"] == MOTIVO_JA_NA_FILA
        ]
        self.assertEqual(len(portal_hits), 1)

    def test_duplicate_in_portal_tratado_is_ignored(self):
        pendente = QualidadePendenteReinspecao.objects.create(
            protocolo="18426529",
            usuario="c19131q",
            descricao_irregularidades="IC - 175 - CPF ausente",
            data_contestacao=timezone.make_aware(datetime(2026, 7, 15, 17, 34, 54)),
            contexto="reinspecao",
            tipo_falha="reinspecao",
        )
        promover_pendente_reinspecao(pendente, origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO)

        preview = parse_reinspecao_workbook(build_duplicate_reinspecao_workbook())
        self.assertEqual(preview.total_falhas, 1)
        tratado_hits = [
            item for item in preview.protocolos_ignorados if item["motivo"] == MOTIVO_JA_ANALISADO
        ]
        self.assertEqual(len(tratado_hits), 1)

    def test_reimport_after_confirm_is_blocked(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile(
            "dup.xlsx",
            build_duplicate_reinspecao_workbook(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        first = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(first.status_code, 200, first.content)
        confirm = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/confirmar/",
            {"import_token": first.json()["import_token"]},
            format="json",
        )
        self.assertEqual(confirm.status_code, 201, confirm.content)
        self.assertEqual(ReinspecaoOcorrencia.objects.count(), 2)
        self.assertEqual(
            ReinspecaoOcorrencia.objects.filter(
                status=ReinspecaoOcorrencia.STATUS_PENDENTE,
                pendente__isnull=False,
            ).count(),
            2,
        )

        upload2 = SimpleUploadedFile(
            "dup.xlsx",
            build_duplicate_reinspecao_workbook(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        second = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {"file": upload2},
            format="multipart",
        )
        self.assertEqual(second.status_code, 200, second.content)
        preview = second.json()["preview"]
        self.assertEqual(preview["total_falhas"], 0)
        self.assertGreaterEqual(preview["total_ignorados_duplicata"], 2)


@override_settings(ACCESS_ENFORCEMENT=False)
class ComplianceImportDedupeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="comp_dedupe",
            email="comp_dedupe@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)
        self.ctx_token = set_fila_contexto("auditoria_compliance")

    def tearDown(self):
        reset_fila_contexto(self.ctx_token)

    def test_compliance_duplicate_in_portal(self):
        QualidadePendenteAuditoriaCompliance.objects.create(
            protocolo="1001",
            usuario="c11111q",
            descricao_irregularidades="Reclassificação",
            data_analise=timezone.make_aware(datetime(2026, 7, 27, 14, 35, 20)),
            tipo_falha="auditoria",
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )
        preview = sample_compliance_import(
            file_bytes=SAMPLE_CSV.encode("utf-8"),
            filename="relatorio.csv",
            protocolos_por_agente=2,
            rng=random.Random(1),
        )
        self.assertEqual(preview.errors, [])
        self.assertLess(preview.total_selecionado, 4)
        self.assertTrue(any(item["motivo"] == MOTIVO_JA_NA_FILA for item in preview.protocolos_ignorados))

    def test_dedupe_key_is_stable(self):
        key_a = build_reinspecao_dedupe_key(
            contexto="reinspecao",
            protocolo="0018426529",
            matricula_inspetor="C19131Q",
            descricao_irregularidades="IC - 175 - CPF ausente",
            data_contestacao=timezone.make_aware(datetime(2026, 7, 15, 17, 34, 54)),
        )
        key_b = build_reinspecao_dedupe_key(
            contexto="reinspecao",
            protocolo="18426529",
            matricula_inspetor="c19131q",
            descricao_irregularidades="ic - 175 - cpf ausente",
            data_contestacao=timezone.make_aware(datetime(2026, 7, 15, 17, 34, 54)),
        )
        self.assertEqual(key_a, key_b)
