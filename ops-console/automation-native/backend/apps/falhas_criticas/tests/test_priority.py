# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.falhas_criticas.services.priority import (
    PRIORITY_ALTA,
    PRIORITY_MEDIA,
    agent_priority,
    build_dashboard_leitura,
    build_dashboard_prioridades,
    driver_priority,
    share_to_level,
)


class PriorityContractTests(SimpleTestCase):
    def test_share_thresholds(self):
        self.assertEqual(share_to_level(25), PRIORITY_ALTA)
        self.assertEqual(share_to_level(20), PRIORITY_ALTA)
        self.assertEqual(share_to_level(15), PRIORITY_MEDIA)
        self.assertEqual(share_to_level(10), PRIORITY_MEDIA)
        self.assertEqual(share_to_level(5), 'baixa')

    def test_agent_priority(self):
        self.assertEqual(agent_priority(oficial=True, critico=True), PRIORITY_ALTA)
        self.assertEqual(agent_priority(oficial=True), PRIORITY_MEDIA)
        self.assertEqual(agent_priority(alta_frequencia=True), PRIORITY_MEDIA)
        self.assertEqual(agent_priority(), 'baixa')

    def test_driver_priority(self):
        self.assertEqual(driver_priority(1, worsening=True), PRIORITY_ALTA)
        self.assertEqual(driver_priority(2), PRIORITY_MEDIA)
        self.assertEqual(driver_priority(5), 'baixa')

    def test_dashboard_prioridades_filters_baixa(self):
        items = build_dashboard_prioridades(
            top_cenario_nome='Doc incompleto',
            top_cenario_qtd=40,
            top_cenario_pct=32,
            reinc_total=3,
            suporte_nc_pct=5,
            suporte_total=10,
        )
        self.assertTrue(items)
        self.assertTrue(all(i['level'] in (PRIORITY_ALTA, PRIORITY_MEDIA) for i in items))
        self.assertIn('cta', items[0])
        self.assertEqual(items[0]['cta']['module'], 'diagnostico')
        self.assertEqual(items[0]['cta']['label'], 'Ver cenário')

    def test_reincidentes_priority_pct_threshold(self):
        media = build_dashboard_prioridades(
            top_cenario_nome=None,
            top_cenario_qtd=0,
            top_cenario_pct=0,
            reinc_total=3,
            agentes_com_falha=30,
        )
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]['level'], PRIORITY_MEDIA)
        self.assertIn('10%', media[0]['evidence'])
        self.assertIn('≥15%', media[0]['evidence'])

        alta_pct = build_dashboard_prioridades(
            top_cenario_nome=None,
            top_cenario_qtd=0,
            top_cenario_pct=0,
            reinc_total=3,
            agentes_com_falha=10,
        )
        self.assertEqual(alta_pct[0]['level'], PRIORITY_ALTA)

        alta_count = build_dashboard_prioridades(
            top_cenario_nome=None,
            top_cenario_qtd=0,
            top_cenario_pct=0,
            reinc_total=5,
            agentes_com_falha=100,
        )
        self.assertEqual(alta_count[0]['level'], PRIORITY_ALTA)

    def test_dashboard_leitura_fallback(self):
        leitura = build_dashboard_leitura(
            pre_diagnostico=None,
            top_cenario_nome='X',
            top_cenario_pct=40,
            reinc_total=2,
            variacao_delta=5,
        )
        self.assertIn('X', leitura['veredito'])
        self.assertEqual(leitura['cta']['module'], 'diagnostico')
        self.assertTrue(leitura['bullets'])
