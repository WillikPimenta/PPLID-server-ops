# -*- coding: utf-8 -*-
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.analytics import (
    build_breakdown,
    filtered_falhas,
)


User = get_user_model()
PROTOCOLO = "53044688"
INTRANET_SOURCE = "auditoria_falha_cadastro"


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=False,
    QUALIDADE_SOURCE_MODE="hybrid",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class Protocolo53044688Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="qo-53044688", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.auditado = QualidadeAuditado.objects.create(
            protocolo=PROTOCOLO,
            data=date(2026, 8, 5),
            data_analise=date(2026, 4, 3),
            tipo_analise="Auditoria",
            tipo_conclusao="Processual",
            matricula="",
            matricula_auditor="auditor",
            source_file=INTRANET_SOURCE,
        )
        self.falha = QualidadeFalha.objects.create(
            protocolo=PROTOCOLO,
            data=date(2026, 8, 5),
            data_analise=date(2026, 8, 4),
            tipo_analise="Auditoria",
            tipo_falha="Processual",
            tipo_falha_oficial="Processual",
            matricula="",
            lider="",
            agente_ativo="",
            source_file=INTRANET_SOURCE,
        )

    def test_processual_visivel_sem_atribuicao_e_presente_no_indicador(self):
        rows = list(filtered_falhas({"protocolo": PROTOCOLO}))
        cards = build_breakdown(
            {
                "protocolo": PROTOCOLO,
                "dim": "tipo_falha",
                "metric": "quantidade",
            }
        )

        self.assertEqual(rows, [self.falha])
        self.assertEqual(rows[0].matricula, "")
        self.assertEqual(rows[0].lider, "")
        self.assertEqual(rows[0].agente_ativo, "")
        processual = next(row for row in cards["rows"] if row["key"] == "Processual")
        self.assertEqual(processual["falhas"], 1)

    def test_processual_nao_entra_em_recortes_de_atribuicao_ou_ranking(self):
        self.assertFalse(
            filtered_falhas(
                {"protocolo": PROTOCOLO, "agent_linked_only": "true"}
            ).exists()
        )
        self.assertFalse(
            filtered_falhas(
                {"protocolo": PROTOCOLO, "matricula": "qualquer-agente"}
            ).exists()
        )
        self.assertTrue(filtered_falhas({"protocolo": PROTOCOLO}).exists())

    def test_fonte_intranet_exige_modo_habilitado_e_corte_correto(self):
        params = {"protocolo": PROTOCOLO}
        self.assertTrue(filtered_falhas(params).exists())

        with override_settings(QUALIDADE_SOURCE_MODE="legacy"):
            self.assertFalse(filtered_falhas(params).exists())
        with override_settings(QUALIDADE_INTRANET_SOURCE_ENABLED=False):
            self.assertFalse(filtered_falhas(params).exists())
        with override_settings(QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-09"):
            self.assertFalse(filtered_falhas(params).exists())

    def test_listas_e_exports_respeitam_filtros_sem_mutar_o_banco(self):
        before_aud = list(
            QualidadeAuditado.objects.filter(protocolo=PROTOCOLO).values()
        )
        before_fal = list(QualidadeFalha.objects.filter(protocolo=PROTOCOLO).values())

        falhas = self.client.get(
            "/api/v1/qualidade/operacional/falhas/", {"protocolo": PROTOCOLO}
        )
        self.assertEqual(falhas.status_code, 200, falhas.data)
        self.assertEqual(falhas.data["count"], 1)
        self.assertEqual(falhas.data["results"][0]["matricula"], "")
        self.assertEqual(falhas.data["results"][0]["lider"], "")

        for kind in ("auditados", "falhas"):
            response = self.client.get(
                f"/api/v1/qualidade/operacional/export/{kind}.csv",
                {"protocolo": PROTOCOLO},
            )
            self.assertEqual(response.status_code, 200)
            body = b"".join(response.streaming_content).decode("utf-8-sig")
            self.assertIn(PROTOCOLO, body)

        self.assertEqual(
            list(QualidadeAuditado.objects.filter(protocolo=PROTOCOLO).values()),
            before_aud,
        )
        self.assertEqual(
            list(QualidadeFalha.objects.filter(protocolo=PROTOCOLO).values()),
            before_fal,
        )
