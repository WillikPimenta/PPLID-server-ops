# -*- coding: utf-8 -*-
"""Meta Produ separada BRFlow × Case."""
from __future__ import annotations

import pandas as pd
from django.test import SimpleTestCase, TestCase

from apps.replicacao_d1.models import ReplicacaoD1ConfigGeral
from apps.replicacao_d1.services.projecao import gerar_projecao_mensal


class MetaProduCaseSeparationTests(TestCase):
    def test_projecao_usa_metas_distintas_por_fila(self):
        from datetime import date

        from apps.replicacao_d1.models import (
            ReplicacaoD1Cliente,
            ReplicacaoD1EscalaDia,
            ReplicacaoD1Workflow,
        )

        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.meta_produ_diaria = 100
        geral.meta_produ_diaria_case = 50
        geral.usar_escala_auditores = True
        geral.save(
            update_fields=["meta_produ_diaria", "meta_produ_diaria_case", "usar_escala_auditores"]
        )

        cli = ReplicacaoD1Cliente.objects.create(nome="Cli Meta", meta_mensal=1000)
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF G",
            cliente=cli,
            fila="G auditoria",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Case",
            cliente=cli,
            fila="3.1",
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        # Um dia futuro no mês corrente da projeção — usar competência fixa com escala
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2099, 1, 15),
            auditores_brflow=2,
            auditores_case=4,
        )

        # Força competência com dia restante e escala; se o serviço filtrar dias passados,
        # ainda assim meta_produ_diaria_case deve vir no payload.
        out = gerar_projecao_mensal(competencia="2099-01")
        self.assertEqual(out["meta_produ_diaria"], 100.0)
        self.assertEqual(out["meta_produ_diaria_case"], 50.0)


class CarregarMetaProduFilaTests(SimpleTestCase):
    def test_fila_case_usa_meta_produ_case(self):
        from app.bots.replicacao_aud_planning import (
            REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
            carregar_meta_produ,
        )

        df = pd.DataFrame({"Workflow": ["x"]})
        df.attrs["meta_produ_resumo"] = 999.0
        settings = {"meta_produ": "100", "meta_produ_case": "40"}
        self.assertEqual(carregar_meta_produ(df, settings), 100.0)
        self.assertEqual(
            carregar_meta_produ(df, settings, fila=REPLICACAO_FILA_DOCUMENTOSCOPIA_31),
            40.0,
        )

    def test_fila_case_fallback_para_meta_brflow(self):
        from app.bots.replicacao_aud_planning import (
            REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
            carregar_meta_produ,
        )

        df = pd.DataFrame({"Workflow": ["x"]})
        settings = {"meta_produ": "120"}
        self.assertEqual(
            carregar_meta_produ(df, settings, fila=REPLICACAO_FILA_DOCUMENTOSCOPIA_31),
            120.0,
        )
