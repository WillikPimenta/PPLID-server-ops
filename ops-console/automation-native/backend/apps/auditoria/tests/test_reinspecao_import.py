from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeImportStaging,
    AuditoriaFalhaCadastro,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.reinspecao_import import (
    avaliar_elegibilidade_reinspecao,
    parse_reinspecao_workbook,
    resolve_matricula_inspetor,
)
from apps.rotina_bruto.models import RotinaGedIrregularidadeTratadoRecord
from apps.auditoria.testing.reinspecao_mapping import seed_test_reinspecao_mapping

User = get_user_model()


class ReinspecaoImportEligibilityTests(SimpleTestCase):
    irregularidade_668 = (
        "IC - 668 - Plano no GED diverge do Plano no Contrato de Habilitação"
    )
    irregularidade_49 = "CO - 49 - Comprovante de Endereço Ausente"

    def avaliar(self, irregularidade, contestacao, resposta):
        return avaliar_elegibilidade_reinspecao(
            descricao_irregularidades=irregularidade,
            data_contestacao=contestacao,
            data_resposta=resposta,
        )

    def test_excecoes_com_data_resposta_vazia_sao_elegiveis(self):
        for irregularidade in (self.irregularidade_668, self.irregularidade_49):
            with self.subTest(irregularidade=irregularidade):
                self.assertEqual(
                    self.avaliar(irregularidade, "20/08/2026 10:00", ""),
                    (True, "data_resposta_vazia"),
                )

    def test_excecoes_com_datas_iguais_sao_elegiveis(self):
        for irregularidade in (self.irregularidade_668, self.irregularidade_49):
            with self.subTest(irregularidade=irregularidade):
                self.assertEqual(
                    self.avaliar(
                        irregularidade.lower(),
                        "20/08/2026 10:00",
                        "20/08/2026 10:00",
                    ),
                    (True, "data_resposta_espelho"),
                )

    def test_excecoes_com_resposta_posterior_nao_sao_elegiveis(self):
        for irregularidade in (self.irregularidade_668, self.irregularidade_49):
            with self.subTest(irregularidade=irregularidade):
                self.assertEqual(
                    self.avaliar(
                        irregularidade,
                        "20/08/2026 10:00",
                        "20/08/2026 10:01",
                    ),
                    (False, "respondida"),
                )

    def test_irregularidade_comum_com_datas_iguais_permanece_respondida(self):
        self.assertEqual(
            self.avaliar(
                "IC - 175 - CPF ausente",
                "20/08/2026 10:00",
                "20/08/2026 10:00",
            ),
            (False, "respondida"),
        )

    def test_datas_invalidas_ou_invertidas_nao_sao_elegiveis(self):
        self.assertEqual(
            self.avaliar(self.irregularidade_668, "data inválida", "20/08/2026 10:00"),
            (False, "data_invalida"),
        )
        self.assertEqual(
            self.avaliar(self.irregularidade_49, "20/08/2026 10:00", "19/08/2026 10:00"),
            (False, "data_inconsistente"),
        )

    def test_parser_csv_encaminha_apenas_linhas_elegiveis(self):
        from apps.auditoria.services.reinspecao_import import parse_reinspecao_csv

        csv_content = (
            "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
            "Descrição das Irregularidades\n"
            f"EX-001;20/08/2026 10:00;20/08/2026 10:00;C19131Q;{self.irregularidade_668}\n"
            f"EX-002;20/08/2026 10:00;20/08/2026 10:01;C19131Q;{self.irregularidade_49}\n"
        ).encode("utf-8")

        with patch(
            "apps.auditoria.services.reinspecao_import_dedupe.apply_reinspecao_import_dedupe",
            side_effect=lambda rows: (rows, [], []),
        ):
            preview = parse_reinspecao_csv(csv_content)

        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_lidas, 2)
        self.assertEqual(preview.total_falhas, 1)
        self.assertEqual(preview.linhas_filtradas, 1)
        self.assertEqual(preview.rows[0].protocolo, "EX-001")


def build_reinspecao_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Planilha1"
    headers = [
        "Protocolo",
        "Status Contrato",
        "MSISDN",
        "CPF",
        "Data Recebimento",
        "Data da contestação",
        "Data de resposta",
        "Tipo de serviço",
        "Regional",
        "Estado",
        "Canal de Ativação",
        "Cód. PDV",
        "Usuario",
        "Status Contestação",
        "Matricula do Inspetor",
        "Descrição das Irregularidades",
    ]
    sheet.append(headers)
    # Válida: data resposta vazia + matrícula c#####q
    sheet.append(
        [
            18426529,
            "IC",
            71982425637,
            10484290592,
            datetime(2026, 3, 12, 18, 38),
            datetime(2026, 7, 15, 17, 34, 54),
            "-",
            "Ativação",
            "BA/SE",
            "BA",
            "LOJA_PROPRIA",
            "MB1Q",
            95794081,
            "Em Avaliação",
            "C19131Q",
            'IC - 175 - CPF/CNPJ no "CPF/RG" Ausente',
        ]
    )
    # Filtrada: data resposta preenchida
    sheet.append(
        [
            22417758,
            "IC",
            11964041260,
            29136979848,
            datetime(2026, 4, 13, 12, 24),
            datetime(2026, 7, 21, 11, 23, 29),
            datetime(2026, 7, 21, 11, 23, 29),
            "Migração de Plano",
            "SPC",
            "SP",
            "AGENTE_AUTORIZADO",
            "PT5L",
            94191196,
            "Em Avaliação",
            "C96333A",
            "IC - 668 - Plano no GED diverge",
        ]
    )
    # Válida: matrícula c#####a
    sheet.append(
        [
            23718475,
            "IC",
            11978724030,
            8417444831,
            datetime(2026, 4, 22, 15, 35, 37),
            datetime(2026, 7, 16, 16, 45, 20),
            "-",
            "Inclusão de Dependentes",
            "SPC",
            "SP",
            "LOJA_PROPRIA",
            "FKJ6",
            95911228,
            "Em Avaliação",
            "C96333A",
            "IC - 445 - Assinatura diverge",
        ]
    )
    # Válida: matrícula literal sistema → gravada como sistema
    sheet.append(
        [
            25071257,
            "IC",
            73981963553,
            98699032587,
            datetime(2026, 5, 2, 11, 39, 50),
            datetime(2026, 7, 16, 16, 11, 1),
            "-",
            "Ativação",
            "BA/SE",
            "BA",
            "AGENTE_AUTORIZADO",
            "B8VB",
            94126089,
            "Em Avaliação",
            "sistema",
            "IC - 668 - Plano no GED diverge",
        ]
    )
    # Válida: matrícula fora do padrão → Sistema
    sheet.append(
        [
            26000001,
            "IC",
            11999999999,
            11111111111,
            datetime(2026, 5, 3, 10, 0, 0),
            datetime(2026, 7, 17, 10, 0, 0),
            "",
            "Ativação",
            "SPC",
            "SP",
            "LOJA_PROPRIA",
            "ABCD",
            12345678,
            "Em Avaliação",
            "12345",
            "IC - 100 - Fora do padrao",
        ]
    )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@override_settings(ACCESS_ENFORCEMENT=False)
class ReinspecaoImportTests(TestCase):
    def setUp(self):
        seed_test_reinspecao_mapping()
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="reinspecao_user",
            email="reinspecao@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    def test_parse_filters_data_resposta_and_normalizes_matricula(self):
        preview = parse_reinspecao_workbook(build_reinspecao_workbook())
        self.assertEqual(preview.errors, [])
        # Linha com data resposta preenchida é filtrada; as demais entram.
        self.assertEqual(preview.total_falhas, 4)
        self.assertEqual(preview.total_lidas, 5)
        self.assertEqual(preview.linhas_filtradas, 1)
        self.assertEqual(
            preview.resumo_planilha,
            {
                "total_protocolos": 5,
                "total_respondidos": 1,
                "total_irregularidades_vazias": 0,
                "total_excecoes": 0,
                "total_agentes": 3,
            },
        )
        filtrado = next(
            row for row in preview.registros_importacao if row["protocolo"] == "22417758"
        )
        self.assertEqual(filtrado["status_importacao"], "filtrado")
        self.assertIn("não será importado", filtrado["observacao"])
        self.assertEqual(len(preview.registros_importacao), 5)
        by_protocolo = {row.protocolo: row.matricula_inspetor for row in preview.rows}
        self.assertEqual(
            by_protocolo,
            {
                "18426529": "c19131q",
                "23718475": "c96333a",
                "25071257": "sistema",
                "26000001": "sistema",
            },
        )

    def test_parse_csv_reinspecao(self):
        from apps.auditoria.services.reinspecao_import import parse_reinspecao_csv

        csv_content = (
            "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;Descrição das Irregularidades\n"
            "18426529;15/07/2026 17:34;-;C19131Q;IC - 175 - CPF ausente\n"
            "22417758;21/07/2026 11:23;21/07/2026 11:23;C96333A;IC - 668 - Plano diverge\n"
            "23718475;16/07/2026 16:45;;c96333a;IC - ok\n"
        ).encode("utf-8")
        preview = parse_reinspecao_csv(csv_content)
        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_falhas, 2)
        self.assertEqual({row.protocolo for row in preview.rows}, {"18426529", "23718475"})

    def test_parse_csv_applies_data_resposta_exceptions(self):
        from apps.auditoria.services.reinspecao_import import parse_reinspecao_csv

        irregularidade_668 = (
            "IC - 668 - Plano no GED diverge do Plano no Contrato de Habilitação"
        )
        irregularidade_49 = "CO - 49 - Comprovante de Endereço Ausente"
        csv_content = (
            "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
            "Descrição das Irregularidades\n"
            f"EX-001;20/08/2026 10:00;;C19131Q;{irregularidade_668}\n"
            f"EX-002;20/08/2026 10:00;-;C19131Q;{irregularidade_49}\n"
            f"EX-003;20/08/2026 10:00;20/08/2026 10:00;C19131Q;{irregularidade_668}\n"
            f"EX-004;20/08/2026 10:00;20/08/2026 10:00;C19131Q;{irregularidade_49}\n"
            f"EX-005;20/08/2026 10:00;20/08/2026 10:01;C19131Q;{irregularidade_668}\n"
            f"EX-006;20/08/2026 10:00;21/08/2026 10:00;C19131Q;{irregularidade_49}\n"
            "EX-007;20/08/2026 10:00;20/08/2026 10:00;C19131Q;IC - 175 - CPF ausente\n"
            f"EX-008;data inválida;20/08/2026 10:00;C19131Q;{irregularidade_668}\n"
            f"EX-009;20/08/2026 10:00;19/08/2026 10:00;C19131Q;{irregularidade_49}\n"
        ).encode("utf-8")

        preview = parse_reinspecao_csv(csv_content)

        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_lidas, 9)
        self.assertEqual(preview.total_falhas, 4)
        self.assertEqual(preview.linhas_filtradas, 5)
        self.assertEqual(
            {row.protocolo for row in preview.rows},
            {"EX-001", "EX-002", "EX-003", "EX-004"},
        )
        self.assertEqual(
            preview.resumo_planilha,
            {
                "total_protocolos": 9,
                "total_respondidos": 3,
                "total_irregularidades_vazias": 0,
                "total_excecoes": 2,
                "total_agentes": 1,
            },
        )
        by_protocolo = {row["protocolo"]: row for row in preview.registros_importacao}
        self.assertEqual(by_protocolo["EX-001"]["observacao"], "")
        self.assertEqual(by_protocolo["EX-003"]["observacao"], "Exceção IC 668/CO 49")
        self.assertEqual(by_protocolo["EX-004"]["observacao"], "Exceção IC 668/CO 49")
        self.assertEqual(
            by_protocolo["EX-005"]["observacao"],
            "Contestação já respondida — não será importado",
        )
        self.assertEqual(
            by_protocolo["EX-007"]["observacao"],
            "Contestação já respondida — não será importado",
        )
        self.assertEqual(len(preview.registros_importacao), 9)

    def test_resumo_planilha_multiplos_agentes(self):
        from apps.auditoria.services.reinspecao_import import parse_reinspecao_csv

        csv_content = (
            "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
            "Descrição das Irregularidades\n"
            "P-001;20/08/2026 10:00;;C19131Q;IC - 175 - CPF ausente\n"
            "P-002;20/08/2026 10:00;;C96333A;IC - 445 - Assinatura diverge\n"
            "P-003;20/08/2026 10:00;20/08/2026 10:01;C19131Q;IC - 175 - CPF ausente\n"
            "P-004;20/08/2026 10:00;;12345;IC - ok\n"
        ).encode("utf-8")

        preview = parse_reinspecao_csv(csv_content)

        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_falhas, 3)
        self.assertEqual(preview.resumo_planilha["total_protocolos"], 4)
        self.assertEqual(preview.resumo_planilha["total_respondidos"], 1)
        self.assertEqual(preview.resumo_planilha["total_irregularidades_vazias"], 0)
        self.assertEqual(preview.resumo_planilha["total_excecoes"], 0)
        self.assertEqual(preview.resumo_planilha["total_agentes"], 3)
        filtrado = next(row for row in preview.registros_importacao if row["protocolo"] == "P-003")
        self.assertEqual(filtrado["status_importacao"], "filtrado")
        self.assertIn("não será importado", filtrado["observacao"])
        self.assertEqual(len(preview.registros_importacao), 4)
        self.assertEqual(
            {row.protocolo: row.matricula_inspetor for row in preview.rows},
            {
                "P-001": "c19131q",
                "P-002": "c96333a",
                "P-004": "sistema",
            },
        )

    def test_parse_workbook_applies_data_resposta_exception(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(
            [
                "Protocolo",
                "Data da contestação",
                "Data de resposta",
                "Matricula do Inspetor",
                "Descrição das Irregularidades",
            ]
        )
        sheet.append(
            [
                "EX-XLSX-001",
                datetime(2026, 8, 20, 10, 0),
                datetime(2026, 8, 20, 10, 0),
                "C19131Q",
                "IC - 668 - Plano no GED diverge do Plano no Contrato de Habilitação",
            ]
        )
        sheet.append(
            [
                "EX-XLSX-002",
                datetime(2026, 8, 20, 10, 0),
                datetime(2026, 8, 20, 10, 1),
                "C19131Q",
                "CO - 49 - Comprovante de Endereço Ausente",
            ]
        )
        buffer = BytesIO()
        workbook.save(buffer)

        preview = parse_reinspecao_workbook(buffer.getvalue())

        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_lidas, 2)
        self.assertEqual(preview.total_falhas, 1)
        self.assertEqual(preview.linhas_filtradas, 1)
        self.assertEqual(preview.rows[0].protocolo, "EX-XLSX-001")

    def test_resolve_matricula_inspetor_pattern(self):
        self.assertEqual(resolve_matricula_inspetor("C19131Q"), "c19131q")
        self.assertEqual(resolve_matricula_inspetor("c96333a"), "c96333a")
        self.assertEqual(resolve_matricula_inspetor("sistema"), "sistema")
        self.assertEqual(resolve_matricula_inspetor("Sistema"), "sistema")
        self.assertEqual(resolve_matricula_inspetor(""), "sistema")
        self.assertEqual(resolve_matricula_inspetor("12345"), "sistema")
        self.assertEqual(resolve_matricula_inspetor("agente.externo"), "sistema")
        self.assertEqual(resolve_matricula_inspetor("c1234a"), "sistema")  # 4 dígitos

    def test_validate_and_confirm_api(self):
        uploaded = SimpleUploadedFile(
            "Modelo_Reinspecao_QI.xlsx",
            build_reinspecao_workbook(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        validate = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {"file": uploaded},
            format="multipart",
        )
        self.assertEqual(validate.status_code, 200, validate.data)
        token = validate.data["import_token"]
        self.assertEqual(validate.data["preview"]["total_falhas"], 4)

        confirm = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/confirmar/",
            {"import_token": token},
            format="json",
        )
        self.assertEqual(confirm.status_code, 201, confirm.data)
        self.assertEqual(confirm.data["created"], 4)
        self.assertEqual(confirm.data["source_file"], "Modelo_Reinspecao_QI.xlsx")
        self.assertEqual(AuditoriaAtividade.objects.filter(tipo=AuditoriaAtividade.TIPO_REINSPECAO).count(), 0)

        # Cadastro grava em pendentes; tratados só após finalizar análise.
        self.assertEqual(AuditoriaFalhaCadastro.objects.filter(origem="reinspecao").count(), 0)
        pendentes = list(QualidadePendenteReinspecao.objects.order_by("protocolo"))
        self.assertEqual(len(pendentes), 4)
        self.assertTrue(all(p.id for p in pendentes))
        self.assertTrue(all(p.tipo_falha == "reinspecao" for p in pendentes))
        self.assertTrue(all(p.data_resposta is None for p in pendentes))
        self.assertTrue(all(p.cliente == "" and p.status == "" and p.auditor == "" for p in pendentes))
        self.assertEqual(pendentes[0].usuario, "c19131q")
        self.assertEqual(pendentes[1].usuario, "c96333a")
        self.assertEqual(pendentes[2].usuario, "sistema")
        self.assertEqual(pendentes[3].usuario, "sistema")
        self.assertEqual(
            pendentes[0].data_contestacao,
            timezone.make_aware(datetime(2026, 7, 15, 17, 34, 54)),
        )
        self.assertTrue(all(p.data_analise is None for p in pendentes))
        self.assertIn("CPF/CNPJ", pendentes[0].descricao_irregularidades)

        listing = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/falhas/",
            {"andamento": "em_andamento"},
        )
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual(listing.data["total"], 4)
        self.assertEqual(listing.data["realizadas"], 0)
        self.assertEqual(listing.data["pendentes"], 4)

        first_id = pendentes[0].id
        patch = self.client.patch(
            f"/api/v1/qualidade/auditoria/reinspecao/falhas/{first_id}/",
            {"status": "Em análise", "cliente": "Claro"},
            format="json",
        )
        self.assertEqual(patch.status_code, 200, patch.data)
        self.assertEqual(patch.data["status"], "Em análise")
        self.assertEqual(patch.data["cliente"], "Claro")
        self.assertEqual(patch.data["id"], first_id)
        self.assertNotIn("public_id", patch.data)

        listing2 = self.client.get(
            "/api/v1/qualidade/auditoria/reinspecao/falhas/",
            {"andamento": "em_andamento"},
        )
        self.assertEqual(listing2.data["realizadas"], 0)
        self.assertEqual(listing2.data["pendentes"], 4)

    def test_confirm_creates_multiple_irregularidades_same_protocol(self):
        csv_content = (
            "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;Descrição das Irregularidades\n"
            "36553981;22/07/2026 09:05;-;C19011Q;IC - 175 - CPF/CNPJ no CPF/RG Ausente\n"
            "36553981;22/07/2026 09:05;-;C19011Q;IC - 668 - Plano no GED diverge\n"
        ).encode("utf-8")
        validate = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {
                "file": SimpleUploadedFile(
                    "multi-irreg.csv",
                    csv_content,
                    content_type="text/csv",
                )
            },
            format="multipart",
        )
        self.assertEqual(validate.status_code, 200, validate.data)
        self.assertEqual(validate.data["preview"]["total_falhas"], 2)

        confirm = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/confirmar/",
            {"import_token": validate.data["import_token"]},
            format="json",
        )
        self.assertEqual(confirm.status_code, 201, confirm.data)
        self.assertEqual(confirm.data["created"], 2)
        self.assertEqual(
            QualidadePendenteReinspecao.objects.filter(protocolo="36553981").count(),
            2,
        )

    def test_confirm_does_not_block_with_legacy_day_only_ged_snapshot(self):
        RotinaGedIrregularidadeTratadoRecord.objects.create(
            report_date=date(2026, 6, 1),
            periodo=1,
            protocolo=18426529,
            data_resposta=date(2026, 7, 22),
        )
        uploaded = SimpleUploadedFile(
            "Modelo_Reinspecao_QI.xlsx",
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
        self.assertEqual(confirm.data["created"], 4)
        self.assertEqual(confirm.data["skipped_finalized_ged"], 0)
        self.assertTrue(
            QualidadePendenteReinspecao.objects.filter(protocolo="18426529").exists()
        )

    def test_validate_reuses_staging_for_identical_file(self):
        file_bytes = (
            "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
            "Descrição das Irregularidades\n"
            "18426529;15/07/2026 17:34;-;C19131Q;IC - 175 - CPF ausente\n"
            "23718475;16/07/2026 16:45;;c96333a;IC - ok\n"
        ).encode("utf-8")

        first = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {
                "file": SimpleUploadedFile(
                    "irregularidades.csv",
                    file_bytes,
                    content_type="text/csv",
                )
            },
            format="multipart",
        )
        second = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {
                "file": SimpleUploadedFile(
                    "irregularidades.csv",
                    file_bytes,
                    content_type="text/csv",
                )
            },
            format="multipart",
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["preview"]["total_falhas"], 2)
        self.assertEqual(first.data["import_token"], second.data["import_token"])
        self.assertEqual(AuditoriaAtividadeImportStaging.objects.count(), 1)
