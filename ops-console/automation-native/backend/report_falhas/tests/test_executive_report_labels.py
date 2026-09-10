# -*- coding: utf-8 -*-
"""Rótulos e narrativa do relatório executivo comparativo."""
from datetime import date

from report_falhas.executive_report import (
    _FAIXA_MAIS_3,
    _FAIXA_MENOS_1,
    _NIVEL_DIFICIL,
    _NIVEL_FACIL,
    _NIVEL_MEDIO,
    _bloco_dificuldade_html,
    _bloco_team_categoria_html,
    _bloco_tempo_casa_html,
    _brand_logo_img,
    _build_html,
    _card_bu,
    _destaques,
    _insight_tempo_casa_comparativo,
    _narrativa_bu,
    _narrativa_tempo_casa,
    _tendencia,
)
from report_falhas.team_category import (
    CATEGORIA_NAO_CLASSIFICADO,
    CATEGORIA_OPERACIONAL,
    CATEGORIA_OUTROS,
    CATEGORIA_QUALIDADE,
)


def _sample_dificuldade(facil_atual=2, facil_prev=4, medio_atual=1, dificil_atual=1):
    total = facil_atual + medio_atual + dificil_atual
    niveis = {
        _NIVEL_FACIL: {
            'atual': facil_atual,
            'prev': facil_prev,
            'pct': (facil_atual / total * 100) if total else 0,
            'trend': _tendencia(facil_atual, facil_prev),
        },
        _NIVEL_MEDIO: {
            'atual': medio_atual,
            'prev': 2,
            'pct': (medio_atual / total * 100) if total else 0,
            'trend': _tendencia(medio_atual, 2),
        },
        _NIVEL_DIFICIL: {
            'atual': dificil_atual,
            'prev': 1,
            'pct': (dificil_atual / total * 100) if total else 0,
            'trend': _tendencia(dificil_atual, 1),
        },
    }
    dominante = max(niveis, key=lambda n: niveis[n]['atual'])
    return {'niveis': niveis, 'total_atual': total, 'dominante': dominante}


def _sample_team_categoria(oper_atual=5, oper_prev=3, qual_atual=2, outros_atual=1):
    total = oper_atual + qual_atual + outros_atual
    categorias = {
        CATEGORIA_OPERACIONAL: {
            'atual': oper_atual,
            'prev': oper_prev,
            'pct': (oper_atual / total * 100) if total else 0,
            'trend': _tendencia(oper_atual, oper_prev),
        },
        CATEGORIA_QUALIDADE: {
            'atual': qual_atual,
            'prev': 1,
            'pct': (qual_atual / total * 100) if total else 0,
            'trend': _tendencia(qual_atual, 1),
        },
        CATEGORIA_OUTROS: {
            'atual': outros_atual,
            'prev': 0,
            'pct': (outros_atual / total * 100) if total else 0,
            'trend': _tendencia(outros_atual, 0),
        },
        CATEGORIA_NAO_CLASSIFICADO: {
            'atual': 0,
            'prev': 0,
            'pct': 0.0,
            'trend': _tendencia(0, 0),
        },
    }
    dominante = max(
        (CATEGORIA_OPERACIONAL, CATEGORIA_QUALIDADE, CATEGORIA_OUTROS),
        key=lambda c: categorias[c]['atual'],
    )
    nao_operacional = qual_atual + outros_atual
    return {
        'categorias': categorias,
        'total_atual': total,
        'dominante': dominante,
        'pct_nao_operacional': (nao_operacional / total * 100) if total else 0,
        'nao_operacional': nao_operacional,
    }


def _sample_kpis():
    return {
        'oficial_atual': 4,
        'total_atual': 8,
        'trend_oficial': _tendencia(4, 3),
        'trend_total': _tendencia(8, 6),
        'reincidentes': 0,
        'reinc_pct': 0.0,
        'novos': 4,
        'top_cenario_nome': 'Teste',
        'top_cenario_qtd': 2,
        'oficial_prev': 3,
        'total_prev': 6,
        'prev_eq_start': date(2026, 5, 1),
        'prev_eq_end': date(2026, 5, 10),
        'dias_comparativo': 10,
        'cur_start': date(2026, 6, 1),
        'cur_end': date(2026, 6, 10),
        'dificuldade': _sample_dificuldade(),
        'team_categoria': _sample_team_categoria(),
    }


def _sample_tempo_casa():
    return {
        'falha': {
            'total': 4,
            'menos_1_ano': 0,
            'pct_menos_1': 0.0,
            'por_faixa': {
                _FAIXA_MENOS_1: 0,
                '1 a 3 anos': 0,
                _FAIXA_MAIS_3: 4,
            },
        },
        'hc': {
            'total': 368,
            'menos_1_ano': 8,
            'pct_menos_1': 2.0,
            'por_faixa': {
                _FAIXA_MENOS_1: 8,
                '1 a 3 anos': 100,
                _FAIXA_MAIS_3: 260,
            },
        },
    }


def test_card_bu_labels_sem_metrica_oficial():
    html = _card_bu('Brasília', _sample_kpis())
    assert 'Métrica oficial' in html
    assert 'sem métrica oficial' in html
    assert 'anterior:' in html
    assert 'Comparativo MTD equivalente' in html
    assert 'falhas oficiais no período atual' in html


def test_narrativa_tempo_casa_sem_total_hc():
    texto = _narrativa_tempo_casa('Brasília', _sample_tempo_casa())
    assert 'falha oficial' in texto
    assert 'na praça' not in texto
    assert 'quadro do HC' not in texto
    assert '368' not in texto


def test_bloco_tempo_casa_sem_quadro_hc():
    ka = {'tempo_casa': _sample_tempo_casa()}
    kb = {
        'tempo_casa': {
            'falha': {
                'total': 8,
                'menos_1_ano': 1,
                'pct_menos_1': 12.5,
                'por_faixa': {
                    _FAIXA_MENOS_1: 1,
                    '1 a 3 anos': 5,
                    _FAIXA_MAIS_3: 2,
                },
            },
            'hc': {'total': 311, 'menos_1_ano': 81, 'pct_menos_1': 26.0, 'por_faixa': {}},
        }
    }
    html = _bloco_tempo_casa_html('Brasília', ka, 'São Carlos', kb)
    assert 'Quadro HC' not in html
    assert '368' not in html
    assert '311' not in html
    assert 'falha oficial' in html.lower() or 'Com falha oficial' in html


def _kpis_com_tempo_casa(oficial, falha_total, menos_1, pct_menos_1):
    return {
        'oficial_atual': oficial,
        'tempo_casa': {
            'falha': {
                'total': falha_total,
                'menos_1_ano': menos_1,
                'pct_menos_1': pct_menos_1,
                'por_faixa': {},
            },
        },
    }


def test_insight_junior_omitido_abaixo_25_pct():
    """Caso SC 1/9 (11%): não sugere ramp-up como explicação de volume."""
    ka = _kpis_com_tempo_casa(4, 4, 0, 0.0)
    kb = _kpis_com_tempo_casa(10, 9, 1, 11.1)
    assert _insight_tempo_casa_comparativo('Brasília', ka, 'São Carlos', kb) == ''


def test_insight_junior_exibido_acima_25_pct():
    ka = _kpis_com_tempo_casa(4, 4, 0, 0.0)
    kb = _kpis_com_tempo_casa(10, 4, 1, 25.0)
    texto = _insight_tempo_casa_comparativo('Brasília', ka, 'São Carlos', kb)
    assert 'São Carlos' in texto
    assert 'perfil mais júnior' in texto
    assert '25%' in texto


def test_brand_logo_img_embedded():
    html = _brand_logo_img()
    assert 'data:image/' in html
    assert 'height:36px' in html
    assert 'Serasa Experian' in html


def test_executive_html_header_has_logo():
    ka = {**_sample_kpis(), 'tempo_casa': _sample_tempo_casa()}
    kb = {**_sample_kpis(), 'oficial_atual': 10, 'total_atual': 19, 'tempo_casa': _sample_tempo_casa()}
    html = _build_html('Brasília', ka, 'São Carlos', kb, date(2026, 6, 1), date(2026, 6, 10))
    assert 'data:image/' in html
    assert 'Comparativo entre Praças' in html


def test_bloco_dificuldade_html_niveis_e_tendencia():
    ka = {'dificuldade': _sample_dificuldade(facil_atual=3, facil_prev=5)}
    kb = {'dificuldade': _sample_dificuldade(facil_atual=1, facil_prev=1, medio_atual=3, dificil_atual=2)}
    html = _bloco_dificuldade_html('Brasília', ka, 'São Carlos', kb)
    assert 'Distribuição por dificuldade' in html
    assert 'Fácil' in html
    assert 'Médio' in html
    assert 'Difícil' in html
    assert 'melhorou' in html
    assert 'maior concentração' in html


def test_destaques_falhas_faceis_melhorou():
    ka = {**_sample_kpis(), 'dificuldade': _sample_dificuldade(facil_atual=2, facil_prev=5)}
    kb = {**_sample_kpis(), 'oficial_atual': 10, 'dificuldade': _sample_dificuldade()}
    melhorou, piorou = _destaques('Brasília', ka, 'São Carlos', kb)
    assert any('fáceis caíram' in m for m in melhorou)


def test_destaques_falhas_faceis_piorou():
    ka = {**_sample_kpis(), 'dificuldade': _sample_dificuldade(facil_atual=6, facil_prev=2)}
    kb = {**_sample_kpis(), 'oficial_atual': 10, 'dificuldade': _sample_dificuldade()}
    melhorou, piorou = _destaques('Brasília', ka, 'São Carlos', kb)
    assert any('fáceis subiram' in p for p in piorou)


def test_executive_html_inclui_bloco_dificuldade():
    ka = {**_sample_kpis(), 'tempo_casa': _sample_tempo_casa()}
    kb = {
        **_sample_kpis(),
        'oficial_atual': 10,
        'total_atual': 19,
        'tempo_casa': _sample_tempo_casa(),
        'dificuldade': _sample_dificuldade(facil_atual=1, facil_prev=3, medio_atual=5, dificil_atual=4),
    }
    html = _build_html('Brasília', ka, 'São Carlos', kb, date(2026, 6, 1), date(2026, 6, 30))
    assert 'Distribuição por dificuldade' in html
    assert 'erros evitáveis' in html
    assert 'Como ler' in html


def test_bloco_team_categoria_html():
    ka = {'team_categoria': _sample_team_categoria(oper_atual=3, qual_atual=4, outros_atual=2)}
    kb = {'team_categoria': _sample_team_categoria(oper_atual=8, qual_atual=1, outros_atual=0)}
    html = _bloco_team_categoria_html('Brasília', ka, 'São Carlos', kb)
    assert 'Team/Category' in html
    assert 'Operacional' in html
    assert 'Qualidade' in html
    assert 'Outros' in html
    assert 'maior concentração' in html


def test_executive_html_inclui_bloco_team_categoria():
    ka = {**_sample_kpis(), 'tempo_casa': _sample_tempo_casa()}
    kb = {**_sample_kpis(), 'tempo_casa': _sample_tempo_casa(), 'oficial_atual': 10}
    html = _build_html('Brasília', ka, 'São Carlos', kb, date(2026, 6, 1), date(2026, 6, 30))
    assert 'Origem por Team/Category' in html
    assert 'não vêm de operação' in html.lower() or 'Operacional' in html


def test_narrativa_bu_formato_bullets():
    html = _narrativa_bu('Brasília', _sample_kpis())
    assert '<ul' in html
    assert 'falha(s) oficiais' in html
    assert 'MTD anterior' in html
    assert 'Dificuldade:' in html
    assert 'Origem:' in html
    assert 'Cenário top:' in html
    assert 'NÃO SINALIZADO' not in html or 'title=' in html


def test_resumo_executivo_sem_paragrafo_tempo_casa():
    ka = {**_sample_kpis(), 'tempo_casa': _sample_tempo_casa()}
    kb = {**_sample_kpis(), 'tempo_casa': _sample_tempo_casa(), 'oficial_atual': 10}
    html = _build_html('Brasília', ka, 'São Carlos', kb, date(2026, 6, 1), date(2026, 6, 30))
    resumo_end = html.split('<!-- Cards das praças -->')[0]
    assert 'menos de 1 ano de casa' not in resumo_end
    assert 'Perfil do time — tempo de casa' not in resumo_end
    assert '<ul' in resumo_end
