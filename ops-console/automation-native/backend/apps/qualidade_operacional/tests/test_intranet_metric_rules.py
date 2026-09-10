# -*- coding: utf-8 -*-
"""Contrato canônico de resultado_qualidade na projeção Intranet."""
from __future__ import annotations

from django.test import SimpleTestCase

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.qualidade_operacional.services.intranet_metric_rules import (
    classify_intranet_source,
    is_falha_efetiva,
    normalize_resultado_qualidade,
    normalize_status_falha,
)


class IntranetMetricRulesTests(SimpleTestCase):
    def classify(self, resultado: str, **extra):
        return classify_intranet_source(
            resultado_qualidade=resultado,
            status_falha=extra.pop("status_falha", "ativa"),
            tipo_falha=extra.pop("tipo_falha", "auditoria"),
            procedencia_raw=extra.pop("procedencia_raw", ""),
            **extra,
        )

    def test_com_falha_e_auditado_e_falha(self):
        cls = self.classify(AuditoriaFalhaCadastro.RESULTADO_COM_FALHA)
        self.assertTrue(cls.is_auditado)
        self.assertTrue(cls.is_falha)

    def test_sem_falha_e_auditado_sem_falha(self):
        cls = self.classify(AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA)
        self.assertTrue(cls.is_auditado)
        self.assertFalse(cls.is_falha)

    def test_nao_classificado_fica_fora_e_alerta(self):
        cls = self.classify(AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO)
        self.assertFalse(cls.is_auditado)
        self.assertFalse(cls.is_falha)
        self.assertEqual(cls.skip_reason, "resultado_qualidade_nao_classificado")
        self.assertIn("resultado_qualidade_nao_classificado", cls.warnings)

    def test_valor_vazio_ou_invalido_e_nao_classificado(self):
        for raw in ("", None, "desconhecido"):
            self.assertEqual(
                normalize_resultado_qualidade(raw),
                AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO,
            )

    def test_nao_reinfere_por_tipo_status_ou_origem(self):
        sem = self.classify(
            AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
            tipo_falha="Colaborador",
            procedencia_raw="Procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
        )
        com = self.classify(
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            tipo_falha="reinspecao",
            procedencia_raw="Procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
        )
        self.assertFalse(sem.is_falha)
        self.assertTrue(com.is_falha)

    def test_retirada_nao_conta_falha_e_mantida_conta(self):
        retirada = self.classify(
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
        )
        mantida = self.classify(
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA,
        )
        self.assertTrue(retirada.is_auditado)
        self.assertFalse(retirada.is_falha)
        self.assertIn("status_falha_nao_contabilizavel", retirada.warnings)
        self.assertTrue(mantida.is_auditado)
        self.assertTrue(mantida.is_falha)

    def test_status_vazio_nao_conta_falha(self):
        cls = self.classify(
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            status_falha="",
        )

        self.assertTrue(cls.is_auditado)
        self.assertFalse(cls.is_falha)
        self.assertIn("status_falha_nao_contabilizavel", cls.warnings)

    def test_metadados_continuam_normalizados(self):
        cls = self.classify(
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            status_falha=" ATIVA ",
            tipo_falha=" Reinspeção ",
            procedencia_raw="improcedente",
            tipo_registro=" REINSPECAO ",
            origem=" REINSPECAO ",
        )
        self.assertEqual(normalize_status_falha(" ATIVA "), "ativa")
        self.assertEqual(cls.tipo_falha_original, "Reinspeção")
        self.assertEqual(cls.procedencia, "Improcedente")
        self.assertEqual(cls.tipo_registro, "reinspecao")
        self.assertEqual(cls.origem, "reinspecao")

    def test_ativo_nao_e_alias_de_ativa(self):
        cls = self.classify(
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            status_falha="ativo",
        )

        self.assertEqual(normalize_status_falha("ativo"), "ativo")
        self.assertFalse(cls.is_falha)

    def test_is_falha_efetiva_exige_resultado_e_status_contabilizavel(self):
        self.assertTrue(
            is_falha_efetiva(
                resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
                status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            )
        )
        self.assertFalse(
            is_falha_efetiva(
                resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
                status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            )
        )
        self.assertFalse(
            is_falha_efetiva(
                resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
                status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
            )
        )

    def test_falha_sempre_e_subconjunto_de_auditado(self):
        for resultado in (
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
            AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO,
        ):
            cls = self.classify(resultado)
            if cls.is_falha:
                self.assertTrue(cls.is_auditado)
