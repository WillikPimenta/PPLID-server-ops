# -*- coding: utf-8 -*-
"""Testes Team → Team/Category (HC)."""
import pandas as pd

from report_falhas.hc_maps import build_team_categoria_map, build_team_hc_map
from report_falhas.team_category import (
    CATEGORIA_NAO_CLASSIFICADO,
    CATEGORIA_OPERACIONAL,
    CATEGORIA_OUTROS,
    CATEGORIA_QUALIDADE,
    map_team_to_categoria,
)


def test_map_team_outros():
    assert map_team_to_categoria('Planejamento') == CATEGORIA_OUTROS
    assert map_team_to_categoria('Customer Experience') == CATEGORIA_OUTROS
    assert map_team_to_categoria('Gerência/Fraud') == CATEGORIA_OUTROS


def test_map_team_operacional():
    assert map_team_to_categoria('Operacional/Fraud') == CATEGORIA_OPERACIONAL
    assert map_team_to_categoria('Operacional/Compliance') == CATEGORIA_OPERACIONAL


def test_map_team_qualidade():
    assert map_team_to_categoria('Auditoria/Fraud') == CATEGORIA_QUALIDADE
    assert map_team_to_categoria('Capacitação/Fraud') == CATEGORIA_QUALIDADE
    assert map_team_to_categoria('Contestação/Fraud') == CATEGORIA_QUALIDADE


def test_map_team_desconhecido():
    assert map_team_to_categoria('Time XYZ') == CATEGORIA_NAO_CLASSIFICADO
    assert map_team_to_categoria('') == CATEGORIA_NAO_CLASSIFICADO


def test_build_team_categoria_map_hc():
    df = pd.DataFrame([
        {
            'matricula_agente': 'c12345a',
            'team': 'Operacional/Fraud',
            'data_inicial': pd.Timestamp('2026-01-01'),
            'id': 1,
        },
        {
            'matricula_agente': 'c12345a',
            'team': 'Auditoria/Compliance',
            'data_inicial': pd.Timestamp('2026-06-01'),
            'id': 2,
        },
        {
            'matricula_agente': 'c99999z',
            'team': 'Planejamento',
            'data_inicial': pd.Timestamp('2026-01-01'),
            'id': 3,
        },
    ])
    team_map = build_team_hc_map(df)
    cat_map = build_team_categoria_map(df)
    assert team_map['c12345a'] == 'Auditoria/Compliance'
    assert cat_map['c12345a'] == CATEGORIA_QUALIDADE
    assert cat_map['c99999z'] == CATEGORIA_OUTROS
