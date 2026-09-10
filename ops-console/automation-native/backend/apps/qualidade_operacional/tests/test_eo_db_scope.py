# -*- coding: utf-8 -*-
from datetime import date

from django.test import TestCase

from apps.dimensoes_processos.models import DimCliente
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.analytics import (
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.eo_db_scope import (
    build_eo_filter_params,
    filtered_auditados_for_client,
    filtered_falhas_for_client,
)
from report_brb.db_loaders import load_auditados_from_db, load_falhas_gerais_from_db


class EoDbScopeParityTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=35, nome="BRB BANCO DE BRASILIA")
        self.id_cliente = 35
        self.source_file = "parity"

    def test_loaders_match_indicadores_filtered_qs(self):
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="AUD1",
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 11),
            matricula="c90001a",
            source_file=self.source_file,
        )
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="AUD0",
            data=date(2026, 6, 1),
            matricula="c90002a",
            source_file=self.source_file,
        )
        QualidadeFalha.objects.create(
            id_cliente=self.id_cliente,
            protocolo="FG1",
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 11),
            matricula="c90001a",
            source_file=self.source_file,
        )

        inicio = date(2026, 7, 1)
        fim = date(2026, 7, 31)
        params = build_eo_filter_params(self.id_cliente, start=inicio, end=fim)

        self.assertEqual(
            filtered_auditados_for_client(self.id_cliente, start=inicio, end=fim).count(),
            filtered_auditados(params).count(),
        )
        self.assertEqual(
            filtered_falhas_for_client(self.id_cliente, start=inicio, end=fim).count(),
            filtered_falhas(params).count(),
        )

        aud = load_auditados_from_db(self.id_cliente, inicio=inicio, fim=fim)
        fg, linhas, _dup = load_falhas_gerais_from_db(self.id_cliente, inicio=inicio, fim=fim)
        self.assertEqual(len(aud), 1)
        self.assertEqual(aud.iloc[0]["Protocolo"], "AUD1")
        self.assertEqual(linhas, 1)
        self.assertEqual(len(fg), 1)
