# -*- coding: utf-8 -*-
from datetime import date

from django.test import SimpleTestCase

from report_falhas.training_utils import training_situacao_calculada


class TrainingSituacaoTests(SimpleTestCase):
    def test_ministrado_com_prazo_ultrapassado_e_vencido(self):
        row = {
            'Status': 'ministrado',
            'SignatureDate': None,
            'Event:EventDeadline': date(2026, 6, 30),
        }
        self.assertEqual(
            training_situacao_calculada(row, ref_date=date(2026, 7, 30)),
            'Vencido',
        )

    def test_ministrado_dentro_do_prazo_e_pendente_assinatura(self):
        row = {
            'Status': 'ministrado',
            'SignatureDate': None,
            'Event:EventDeadline': date(2026, 8, 15),
        }
        self.assertEqual(
            training_situacao_calculada(row, ref_date=date(2026, 7, 30)),
            'Pendente de assinatura do agente',
        )

    def test_previsto_dentro_do_prazo_e_nao_concluido(self):
        row = {
            'Status': 'previsto',
            'SignatureDate': None,
            'Event:EventDeadline': date(2026, 8, 20),
        }
        self.assertEqual(
            training_situacao_calculada(row, ref_date=date(2026, 7, 30)),
            'Treinamento não concluído',
        )

    def test_a_vencer_em_7_dias(self):
        row = {
            'Status': 'ministrado',
            'SignatureDate': None,
            'Event:EventDeadline': date(2026, 8, 2),
        }
        self.assertEqual(
            training_situacao_calculada(row, ref_date=date(2026, 7, 30)),
            'A vencer',
        )
