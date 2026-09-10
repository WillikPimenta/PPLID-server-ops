# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.falhas_criticas.services.narrative import build_comparativo_narrative


class ComparativoNarrativeTests(SimpleTestCase):
    def _bu(self, falhas_total, suporte_total=0, nc=None, r3_count=0):
        return {
            'falhas': {'total': falhas_total, 'reinc_pct': 10},
            'suporte': {
                'total': suporte_total,
                'nc_oficial_pct': nc,
                'regra3_pct': 0 if suporte_total else None,
                'regra3_count': r3_count,
            },
            'treinamentos': {},
        }

    def test_capacitacao_segue_unidade_pior_no_driver(self):
        # BSB tem mais falhas no total, mas o driver top é pior em SC (delta < 0).
        bsb = self._bu(27, suporte_total=5, nc=10)
        sc = self._bu(23, suporte_total=5, nc=8)
        drivers = [
            {
                'label': 'Novo Pré Venda',
                'bsb_qtd': 3,
                'sc_qtd': 8,
                'delta': -5,
            }
        ]
        block = build_comparativo_narrative(bsb, sc, drivers)
        joined = ' '.join(block.get('bullets') or [])
        self.assertIn('São Carlos', joined)
        self.assertIn('Novo Pré Venda', joined)
        self.assertNotIn('capacitação em «Novo Pré Venda» na operação Brasília', joined)

    def test_maior_diferenca_nao_chama_principal_driver(self):
        bsb = self._bu(27, suporte_total=5, nc=10)
        sc = self._bu(23, suporte_total=5, nc=8)
        drivers = [
            {'label': 'Novo Pré Venda', 'bsb_qtd': 3, 'sc_qtd': 8, 'delta': -5},
            {'label': 'Doc. adulterado', 'bsb_qtd': 6, 'sc_qtd': 1, 'delta': 5},
            {'label': 'Rasuras', 'bsb_qtd': 4, 'sc_qtd': 1, 'delta': 3},
        ]
        block = build_comparativo_narrative(bsb, sc, drivers)
        joined = ' '.join(block.get('bullets') or [])
        self.assertNotIn('Principal driver', joined)
        self.assertIn('Maior diferença: Novo Pré Venda', joined)
        self.assertIn('São Carlos registra 5 falha(s) a mais', joined)
        self.assertIn('Itens que elevam Brasília no total', joined)
        self.assertIn('Doc. adulterado', joined)

    def test_nc_sem_dados_quando_volume_zero(self):
        bsb = self._bu(10, suporte_total=0, nc=None)
        sc = self._bu(8, suporte_total=0, nc=None)
        block = build_comparativo_narrative(bsb, sc, [])
        joined = ' '.join(block.get('bullets') or [])
        self.assertIn('BSB sem dados', joined)
        self.assertIn('SC sem dados', joined)
