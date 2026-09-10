# -*- coding: utf-8 -*-
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from apps.dimensoes_processos.models import DimCliente
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services import contestacao_derivation
from report_brb.db_loaders import (
    derive_conforme_value,
    derive_contestacao_from_eo,
    load_eo_core_frames,
)


class DbLoadersContestacaoTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=35, nome="BRB BANCO DE BRASILIA")
        self.id_cliente = 35

    def test_derive_conforme_procedencia_and_falha(self):
        self.assertEqual(derive_conforme_value(procedencia="Improcedente"), "Sim")
        self.assertEqual(derive_conforme_value(procedencia="Procedente"), "Não")
        self.assertEqual(
            derive_conforme_value(has_contestacao_falha=True, tipo_falha="Manual"),
            "Não",
        )
        self.assertEqual(
            derive_conforme_value(has_contestacao_falha=True, tipo_falha="SEM FALHA"),
            "Sim",
        )
        self.assertEqual(derive_conforme_value(has_contestacao_falha=False), "Sim")

    def test_contestacao_excludes_compliance_keeps_operational_flows(self):
        """Paridade Indicadores/QO: Contestação + Externa; exclui Compliance (Formalização)."""
        from apps.qualidade_operacional.models import QualidadeFiltroOpcao
        from apps.qualidade_operacional.services.contestacao_metrics import clear_contestacao_tipo_cache

        for valor in ("Contestação", "Contestação Externa", "Contestação Compliance"):
            QualidadeFiltroOpcao.objects.get_or_create(dimensao="tipo_analise", valor=valor)
        clear_contestacao_tipo_cache()

        QualidadeAuditado.objects.create(
            id_cliente=9999,
            protocolo="FORM1",
            tipo_analise="Contestação",
            data=date(2026, 1, 5),
            data_recepcao_contestacao=date(2026, 1, 5),
            matricula="c99999a",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="PCONT1",
            tipo_analise="Contestação",
            data=date(2026, 1, 6),
            data_recepcao_contestacao=date(2026, 1, 6),
            matricula="c90000a",
            procedencia="Procedente",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="PCMP1",
            tipo_analise="Contestação Compliance",
            data=date(2026, 1, 6),
            data_recepcao_contestacao=date(2026, 1, 6),
            matricula="c90000b",
            procedencia="Improcedente",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="PEXT1",
            tipo_analise="Contestação Externa",
            data=date(2026, 1, 7),
            data_recepcao_contestacao=date(2026, 1, 7),
            data_analise=date(2026, 1, 3),
            matricula="c90001a",
            etapa="COMPARAÇÃO DE SELFIE",
            cenario="SINALIZAÇÃO INCORRETA",
            procedencia="Procedente",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            id_cliente=self.id_cliente,
            protocolo="PEXT1",
            tipo_analise="Contestação Externa",
            modulo="Contestação",
            tipo_falha="Manual",
            matricula="c90001a",
            data=date(2026, 1, 3),
            data_analise=date(2026, 1, 3),
            source_file="test",
        )

        cont = derive_contestacao_from_eo(
            self.id_cliente,
            inicio=date(2026, 1, 1),
            fim=date(2026, 1, 31),
        )
        self.assertEqual(len(cont), 2)
        protos = set(cont["Protocolo"].astype(str))
        self.assertEqual(protos, {"PCONT1", "PEXT1"})
        self.assertNotIn("PCMP1", protos)
        pext = cont[cont["Protocolo"] == "PEXT1"].iloc[0]
        self.assertEqual(pext["CONFORME"], "Não")
        self.assertEqual(pext["classificacao_conforme"], "falha")
        self.assertEqual(pext["Etapa"], "COMPARAÇÃO DE SELFIE")

    def test_contestacao_batimento_keys_cover_full_eo(self):
        from report_brb.brb_filters import contestacao_batimento_key_from_row
        from report_brb.db_loaders import load_contestacao_batimento_keys

        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="BAT1",
            tipo_analise="Contestação Externa",
            data=date(2026, 3, 1),
            data_recepcao_contestacao=date(2026, 3, 1),
            matricula="c90002a",
            etapa="VALIDAÇÃO",
            cenario="CENARIO TESTE",
            source_file="test",
        )
        cont = derive_contestacao_from_eo(self.id_cliente)
        keys = load_contestacao_batimento_keys(self.id_cliente)
        self.assertEqual(len(cont), 1)
        self.assertIn(contestacao_batimento_key_from_row(cont.iloc[0]), keys)

    def test_contestacao_report_tie_breakers_are_order_independent_and_hide_ids(self):
        from apps.qualidade_operacional.models import QualidadeFiltroOpcao
        from apps.qualidade_operacional.services.contestacao_metrics import (
            clear_contestacao_tipo_cache,
        )

        QualidadeFiltroOpcao.objects.get_or_create(
            dimensao="tipo_analise",
            valor="Contestação",
        )
        clear_contestacao_tipo_cache()

        lower_aud = QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="TIE-REPORT",
            tipo_analise="Contestação",
            data=date(2026, 1, 20),
            data_recepcao_contestacao=date(2026, 1, 20),
            data_analise=date(2026, 1, 10),
            matricula="c90009a",
            procedencia="Procedente",
            source_file="test",
        )
        higher_aud = QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="TIE-REPORT",
            tipo_analise="Contestação",
            data=date(2026, 1, 21),
            data_recepcao_contestacao=date(2026, 1, 20),
            data_analise=date(2026, 1, 10),
            matricula="c90009a",
            procedencia="",
            source_file="test",
        )
        lower_fal = QualidadeFalha.objects.create(
            id_cliente=self.id_cliente,
            protocolo="TIE-REPORT",
            tipo_analise="Contestação",
            modulo="Contestação",
            tipo_falha="Manual",
            data=date(2026, 1, 20),
            data_analise=date(2026, 1, 10),
            matricula="c90009a",
            procedencia="Procedente",
            source_file="test",
        )
        higher_fal = QualidadeFalha.objects.create(
            id_cliente=self.id_cliente,
            protocolo="TIE-REPORT",
            tipo_analise="Contestação",
            modulo="Contestação",
            tipo_falha="Manual",
            data=date(2026, 1, 20),
            data_analise=date(2026, 1, 10),
            matricula="c90009a",
            procedencia="Improcedente",
            source_file="test",
        )
        self.assertGreater(higher_aud.id, lower_aud.id)
        self.assertGreater(higher_fal.id, lower_fal.id)

        original_aud_scope = contestacao_derivation.contestacao_auditados_scope
        original_fal_scope = contestacao_derivation.contestacao_falhas_scope

        def derive_with_order(prefix: str):
            with patch.object(
                contestacao_derivation,
                "contestacao_auditados_scope",
                side_effect=lambda params: original_aud_scope(params).order_by(f"{prefix}id"),
            ), patch.object(
                contestacao_derivation,
                "contestacao_falhas_scope",
                side_effect=lambda params: original_fal_scope(params).order_by(f"{prefix}id"),
            ):
                return derive_contestacao_from_eo(
                    self.id_cliente,
                    inicio=date(2026, 1, 1),
                    fim=date(2026, 1, 31),
                )

        ascending = derive_with_order("")
        descending = derive_with_order("-")

        self.assertEqual(ascending.to_dict("records"), descending.to_dict("records"))
        self.assertEqual(len(ascending), 1)
        self.assertEqual(ascending.iloc[0]["CONFORME"], "Sim")
        self.assertNotIn("id", ascending.columns)
        self.assertNotIn("_auditado_id", ascending.columns)

    def test_load_eo_core_frames_period_filter(self):
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="AUD1",
            data=date(2026, 2, 1),
            matricula="c90001a",
            source_file="test",
        )
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="AUD0",
            data=date(2025, 12, 1),
            matricula="c90002a",
            source_file="test",
        )
        QualidadeFalha.objects.create(
            id_cliente=self.id_cliente,
            protocolo="FG1",
            data=date(2026, 2, 2),
            data_analise=date(2026, 2, 2),
            matricula="c90001a",
            source_file="test",
        )

        frames = load_eo_core_frames(
            "brb",
            inicio=date(2026, 1, 1),
            fim=date(2026, 3, 31),
        )
        self.assertEqual(len(frames["auditados"]), 1)
        self.assertEqual(frames["auditados"].iloc[0]["Protocolo"], "AUD1")
        self.assertEqual(len(frames["falhas_gerais"]), 1)
        self.assertEqual(frames["id_cliente"], 35)
