# -*- coding: utf-8 -*-
"""Testes do motor de SLA útil (§22)."""

from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.dimensoes_processos.models import (
    DimCliente,
    DimNivelHierarquico,
    DimWorkflow,
    ProjecaoSla,
)
from apps.monitoramento_sla.services.classificacao import (
    DENTRO,
    FORA,
    classificar_ajustado,
    classificar_natural_padrao,
)
from apps.monitoramento_sla.services.d2u_wf450 import calcular_vencimento_d2u, classificar_d2u
from apps.monitoramento_sla.services.faixas import classificar_faixa
from apps.monitoramento_sla.services.keys import date_key, key_cwn
from apps.monitoramento_sla.services.projecao_lookup import find_projecao_dia
from apps.monitoramento_sla.services.sla_util import calcular_sla_util_segundos


class KeysTests(TestCase):
    def test_date_key_and_cwn(self):
        self.assertEqual(date_key(date(2026, 5, 1)), 20260501)
        self.assertEqual(key_cwn(1, 2, 3), 1 * 100_000_000 + 2 * 100_000 + 3)


class SlaUtilEngineTests(TestCase):
    def setUp(self):
        self.cli = DimCliente.objects.create(id_cliente=1, nome="Cliente A")
        self.wf = DimWorkflow.objects.create(id_workflow=10, nome="WF A")
        self.nh = DimNivelHierarquico.objects.create(id_nh=1, nome="NH1")
        self.proj = ProjecaoSla.objects.create(
            cliente=self.cli,
            workflow=self.wf,
            nivel_hierarquico=self.nh,
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            dias_semana="{0..4}",
            hora_inicio=time(8, 0, 0),
            hora_fim=time(18, 0, 0),
            duracao_atendimento=36000,
            sla_segundos=3600,
            sla_ajuste=7200,
            flag_ajuste_sla=Decimal("0.1"),
            volume=5,
        )

    def test_same_window(self):
        sec, probs = calcular_sla_util_segundos(
            id_cliente=1,
            id_workflow=10,
            id_nh=1,
            data_cadastro=date(2026, 7, 1),  # wednesday
            hora_cadastro=time(9, 0, 0),
            data_fim=date(2026, 7, 1),
            hora_fim=time(10, 0, 0),
        )
        self.assertEqual(sec, 3600)
        self.assertNotIn("PROJECAO_CADASTRO_NAO_ENCONTRADA", probs)

    def test_before_open(self):
        sec, _ = calcular_sla_util_segundos(
            id_cliente=1,
            id_workflow=10,
            id_nh=1,
            data_cadastro=date(2026, 7, 1),
            hora_cadastro=time(7, 0, 0),
            data_fim=date(2026, 7, 1),
            hora_fim=time(8, 30, 0),
        )
        self.assertEqual(sec, 1800)

    def test_weekend_no_window(self):
        sec, probs = calcular_sla_util_segundos(
            id_cliente=1,
            id_workflow=10,
            id_nh=1,
            data_cadastro=date(2026, 7, 4),  # saturday
            hora_cadastro=time(9, 0, 0),
            data_fim=date(2026, 7, 4),
            hora_fim=time(10, 0, 0),
        )
        self.assertEqual(sec, 0)
        self.assertIn("PROJECAO_CADASTRO_NAO_ENCONTRADA", probs)

    def test_faixa(self):
        self.assertEqual(classificar_faixa(30), "1 minuto")
        self.assertEqual(classificar_faixa(900), "15 minutos")
        self.assertEqual(classificar_faixa(50000), "Mais de 12h")
        self.assertIsNone(classificar_faixa(None))

    def test_natural_and_ajustado(self):
        proj, _ = find_projecao_dia(id_cliente=1, id_workflow=10, id_nh=1, on_date=date(2026, 7, 1))
        nat, _ = classificar_natural_padrao(sla_segundos=100, proj_cadastro=proj, em_aberto=False)
        self.assertEqual(nat, DENTRO)
        nat2, _ = classificar_natural_padrao(sla_segundos=4000, proj_cadastro=proj, em_aberto=False)
        self.assertEqual(nat2, FORA)
        aj = classificar_ajustado(
            sla_natural=FORA,
            sla_segundos=4000,
            proj_cadastro=proj,
            rank_protocolo=10,
        )
        self.assertEqual(aj, DENTRO)  # rank >= volume (5) com flag default


class D2UTests(TestCase):
    def setUp(self):
        self.cli = DimCliente.objects.create(id_cliente=2, nome="Cli")
        self.wf = DimWorkflow.objects.create(id_workflow=450, nome="WF450")
        self.nh = DimNivelHierarquico.objects.create(id_nh=2, nome="NH2")
        # Jan–Jul weekdays
        ProjecaoSla.objects.create(
            cliente=self.cli,
            workflow=self.wf,
            nivel_hierarquico=self.nh,
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            dias_semana="{0..4}",
            hora_inicio=time(8, 0),
            hora_fim=time(17, 0),
            duracao_atendimento=32400,
            sla_segundos=99999,
        )

    def test_vencimento_and_class(self):
        # cadastro sexta 2026-05-01
        venc, probs = calcular_vencimento_d2u(
            id_cliente=2,
            id_workflow=450,
            id_nh=2,
            data_cadastro=date(2026, 5, 1),
        )
        self.assertIsNotNone(venc)
        self.assertNotIn("VENCIMENTO_D2U_NAO_ENCONTRADO", probs)
        self.assertEqual(classificar_d2u(data_referencia=venc, vencimento=venc), DENTRO)
        self.assertEqual(
            classificar_d2u(data_referencia=venc + timedelta(days=1), vencimento=venc),
            FORA,
        )
