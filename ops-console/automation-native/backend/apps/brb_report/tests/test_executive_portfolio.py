# -*- coding: utf-8 -*-
from apps.brb_report.services.executive_portfolio import (
    _portfolio_totals,
    build_client_diagnostics,
    build_client_workflows,
    build_executive_story,
    build_monthly_scopes,
    build_portfolio_contestacao_timing,
    build_portfolio_monthly,
    build_portfolio_diagnostics,
    build_timeline_events,
    rank_portfolio_rows,
)
import pandas as pd
import pytest
from datetime import datetime

from report_brb.brb_loaders import BRBDataBundle


def _row(slug, nome, confirmadas, contestacoes=0, decididas=0, taxa=0.0, ok=True):
    return {
        "slug": slug,
        "nome_curto": nome,
        "ok": ok,
        "falhas_confirmadas": confirmadas,
        "contestacoes": contestacoes,
        "contestacoes_decididas": decididas or contestacoes,
        "taxa_confirmada_pct": taxa,
        "auditados": 100,
        "auditados_registros": 140,
        "achados_fg": 10,
        "sem_falha": max(0, (decididas or contestacoes) - confirmadas),
        "na_notificadas": 0,
        "taxa_achado_pct": 1.0,
    }


def test_rank_portfolio_top10_and_share():
    rows = [
        _row("a", "Alpha", 50, contestacoes=200, decididas=200, taxa=25.0),
        _row("b", "Beta", 30, contestacoes=120, decididas=120, taxa=25.0),
        _row("c", "Gamma", 20, contestacoes=80, decididas=80, taxa=25.0),
    ]
    top = rank_portfolio_rows(rows, top_n=2)
    assert [r["slug"] for r in top] == ["a", "b"]
    assert top[0]["rank"] == 1
    assert top[0]["share_confirmadas_pct"] == 50.0


def test_executive_story_has_four_beats():
    rows = [
        _row("a", "Alpha", 50, contestacoes=200, decididas=200, taxa=25.0),
        _row("b", "Beta", 30, contestacoes=120, decididas=120, taxa=15.0),
        _row("c", "Gamma", 10, contestacoes=80, decididas=80, taxa=10.0),
    ]
    totals = _portfolio_totals(rows)
    top = rank_portfolio_rows(rows, top_n=10)
    story = build_executive_story(rows, top, totals, periodo="jan/2026")
    assert story["headline"]
    assert len(story["beats"]) == 4
    assert story["closing"]


def test_portfolio_narrative_mentions_top_clients():
    rows = [
        _row("a", "Alpha", 40, contestacoes=100, decididas=100, taxa=40.0),
        _row("b", "Beta", 10, contestacoes=50, decididas=50, taxa=20.0),
    ]
    totals = _portfolio_totals(rows)
    top = rank_portfolio_rows(rows, top_n=10)
    text = " ".join(build_executive_story(rows, top, totals, periodo="jan/2026")["paragraphs"])
    assert "Alpha" in text


def test_portfolio_monthly_aggregates_client_series():
    rows = [
        {**_row("a", "Alpha", 10), "monthly_confirmadas": {"2026-01": 4, "2026-02": 6}},
        {**_row("b", "Beta", 5), "monthly_confirmadas": {"2026-02": 2}},
    ]
    monthly = build_portfolio_monthly(rows)
    assert monthly["confirmadas"]["2026-01"] == 4
    assert monthly["confirmadas"]["2026-02"] == 8
    assert monthly["has_data"] is True


def test_timeline_events_include_peak_and_leader():
    from datetime import date

    rows = [_row("a", "Alpha", 50, contestacoes=200, decididas=200, taxa=25.0)]
    totals = _portfolio_totals(rows)
    top = rank_portfolio_rows(rows, top_n=10)
    monthly = {
        "confirmadas": {"2026-01": 10, "2026-02": 40},
        "partial_month": None,
    }
    events = build_timeline_events(
        monthly, top, totals, inicio=date(2026, 1, 1), fim=date(2026, 2, 28)
    )
    titles = [e["title"] for e in events]
    assert "Pico de confirmações" in titles
    assert "Maior volume individual" in titles


def test_portfolio_diagnostics_groups_tipo_and_motivo():
    df = pd.DataFrame(
        {
            "Protocolo": ["P1", "P2", "P3"],
            "Data": [datetime(2026, 5, 10), datetime(2026, 6, 5), datetime(2026, 6, 20)],
            "classificacao_conforme": ["falha", "falha", "falha"],
            "Cenário": [
                "SINALIZAÇÃO INCORRETA",
                "DOCUMENTO ILEGÍVEL",
                "SINALIZAÇÃO INCORRETA",
            ],
            "Tipo de procedência": ["Automático", "Manual", "Automático"],
        }
    )
    diag = build_portfolio_diagnostics([df])
    cont = diag["contestacao"]
    assert cont["total"] == 3
    labels = {row["label"] for row in cont["tipos"]}
    assert "Automático" in labels
    assert "Manual" in labels
    assert cont["pareto_motivos"][0]["quantidade"] >= 2
    assert len(cont["motivos_por_mes"]) == 2


def test_portfolio_diagnostics_includes_auditoria_achados():
    fg = pd.DataFrame(
        {
            "Protocolo": ["A1", "A2", "A3", "A4"],
            "Data de Análise": [
                datetime(2026, 5, 10),
                datetime(2026, 5, 12),
                datetime(2026, 6, 5),
                datetime(2026, 6, 8),
            ],
            "Novo cenário": [
                "FORMATAÇÃO INCORRETA",
                "DOCUMENTO ILEGÍVEL",
                "FORMATAÇÃO INCORRETA",
                "SOBREPOSIÇÃO",
            ],
            "Tipo de Falha": ["Automático", "Manual", "Automático", "Mapeamento"],
            "duplicado_contestacao": [False, False, False, True],
        }
    )
    diag = build_portfolio_diagnostics([], [fg])
    audit = diag["auditoria"]
    assert audit["achados_bruto"] == 4
    assert audit["duplicados_contestacao"] == 1
    assert audit["total"] == 3
    labels = {row["label"] for row in audit["tipos"]}
    assert "Automático" in labels
    assert "Manual" in labels
    assert audit["pareto_motivos"][0]["motivo"] == "FORMATAÇÃO INCORRETA"
    assert len(audit["motivos_por_mes"]) == 2


def test_portfolio_diagnostics_auditoria_normaliza_cenarios():
    fg = pd.DataFrame(
        {
            "Protocolo": ["B1", "B2", "B3"],
            "Data de Análise": [
                datetime(2026, 5, 10),
                datetime(2026, 5, 11),
                datetime(2026, 5, 12),
            ],
            "Novo cenário": [
                "NÃO SINALIZADO - DOC. POSSUI SOBREPOSIÇÃO NA FOTO",
                "SINALIZAÇÃO INCORRETA - DOC. POSSUI SOBREPOSIÇÃO NA FOTO",
                "NÃO SINALIZADO - FACE A DO DOCUMENTO DE IDENTIFICAÇÃO INCOMPATÍVEL COM A FACE B",
            ],
            "Tipo de Falha": ["Mapeamento", "Manual", "Manual"],
            "duplicado_contestacao": [False, False, False],
        }
    )
    diag = build_portfolio_diagnostics([], [fg])
    motivos = {row["motivo"] for row in diag["auditoria"]["pareto_motivos"]}
    assert "Não sinalizado · Sobreposição na foto" in motivos
    assert "Sinalização incorreta · Sobreposição na foto" in motivos
    assert "Não sinalizado · Face A incompatível com Face B" in motivos
    assert diag["auditoria"]["total"] == 3
    pct_sum = sum(row["pct"] for row in diag["auditoria"]["pareto_motivos"])
    assert 99.0 <= pct_sum <= 101.0


def test_portfolio_totals_sum_auditados_registros():
    rows = [
        _row("a", "Alpha", 10, contestacoes=50, decididas=50),
        _row("b", "Beta", 5, contestacoes=30, decididas=30),
    ]
    rows[0]["auditados_registros"] = 200
    rows[1]["auditados_registros"] = 150
    totals = _portfolio_totals(rows)
    assert totals["auditados_registros"] == 350


def test_portfolio_contestacao_timing_mediana():
    df = pd.DataFrame(
        {
            "Data de Análise": [datetime(2025, 6, 1), datetime(2025, 1, 1)],
            "Data": [datetime(2026, 3, 1), datetime(2026, 2, 1)],
            "classificacao_conforme": ["falha", "ok"],
            "_protocolo_norm": ["p1", "p2"],
        }
    )
    timing = build_portfolio_contestacao_timing([df], fim=datetime(2026, 8, 19).date())
    assert timing["total"] == 2
    assert timing["mediana_dias"] > 0
    assert len(timing["faixas"]) == 6


def test_portfolio_contestacao_timing_faixa_clientes():
    df_a = pd.DataFrame(
        {
            "Data de Análise": [datetime(2024, 1, 1), datetime(2024, 1, 1)],
            "Data": [datetime(2026, 3, 1), datetime(2026, 3, 2)],
            "classificacao_conforme": ["falha", "falha"],
            "_protocolo_norm": ["p1", "p2"],
            "_client_slug": ["alpha", "alpha"],
            "_client_nome": ["Alpha", "Alpha"],
        }
    )
    df_b = pd.DataFrame(
        {
            "Data de Análise": [datetime(2023, 1, 1)],
            "Data": [datetime(2026, 3, 1)],
            "classificacao_conforme": ["falha"],
            "_protocolo_norm": ["p3"],
            "_client_slug": ["beta"],
            "_client_nome": ["Beta"],
        }
    )
    timing = build_portfolio_contestacao_timing(
        [df_a, df_b],
        fim=datetime(2026, 8, 19).date(),
    )
    faixa_antiga = next(f for f in timing["faixas"] if f["label"] == "Mais de 2 anos")
    assert faixa_antiga["qtd"] == 3
    assert faixa_antiga["clientes"][0]["nome"] == "Alpha"
    assert faixa_antiga["clientes"][0]["qtd"] == 2
    assert faixa_antiga["clientes"][1]["nome"] == "Beta"


def test_portfolio_contestacao_timing_anos_procedentes():
    df = pd.DataFrame(
        {
            "Data de Análise": [
                datetime(2024, 1, 1),
                datetime(2024, 2, 1),
                datetime(2023, 6, 1),
            ],
            "Data": [datetime(2026, 3, 1)] * 3,
            "classificacao_conforme": ["falha", "ok", "falha"],
            "_protocolo_norm": ["p1", "p2", "p3"],
        }
    )
    timing = build_portfolio_contestacao_timing([df], fim=datetime(2026, 8, 19).date())
    by_ano = {item["ano"]: item for item in timing["anos"]}
    assert by_ano[2024]["procedentes"] == 1
    assert by_ano[2024]["improcedentes"] == 1
    assert by_ano[2023]["procedentes"] == 1
    assert by_ano[2023]["improcedentes"] == 0


def test_portfolio_contestacao_timing_anos_clientes_recebimento():
    df = pd.DataFrame(
        {
            "Data de Análise": [datetime(2024, 1, 1), datetime(2024, 1, 1), datetime(2024, 2, 1)],
            "Data": [datetime(2026, 4, 10), datetime(2026, 6, 20), datetime(2026, 5, 5)],
            "classificacao_conforme": ["falha", "ok", "falha"],
            "_protocolo_norm": ["p1", "p2", "p3"],
            "_client_slug": ["alpha", "alpha", "beta"],
            "_client_nome": ["Alpha", "Alpha", "Beta"],
        }
    )
    timing = build_portfolio_contestacao_timing([df], fim=datetime(2026, 8, 19).date())
    ano_2024 = next(item for item in timing["anos"] if item["ano"] == 2024)
    assert ano_2024["clientes"][0]["nome"] == "Alpha"
    assert ano_2024["clientes"][0]["qtd"] == 2
    assert ano_2024["clientes"][0]["recebimento"] == "10/04/2026 – 20/06/2026"
    assert ano_2024["clientes"][1]["nome"] == "Beta"
    assert ano_2024["clientes"][1]["recebimento"] == "05/05/2026"


def test_monthly_scopes_filter_top3():
    rows = [
        {**_row("a", "Alpha", 10), "monthly_confirmadas": {"2026-04": 5}, "monthly_achados": {"2026-04": 50}},
        {**_row("b", "Beta", 5), "monthly_confirmadas": {"2026-04": 3}, "monthly_achados": {"2026-04": 20}},
        {**_row("c", "Gamma", 1), "monthly_confirmadas": {"2026-04": 1}, "monthly_achados": {"2026-04": 10}},
        {**_row("d", "Delta", 1), "monthly_confirmadas": {"2026-04": 9}, "monthly_achados": {"2026-04": 100}},
    ]
    top = rank_portfolio_rows(rows, top_n=3)
    scopes = build_monthly_scopes(rows, top, inicio=None, fim=None)
    assert scopes["portfolio"]["2026-04"] == 18
    assert scopes["top3"]["2026-04"] == 9
    assert scopes["metrics"]["achados"]["portfolio"]["2026-04"] == 180
    assert scopes["metrics"]["achados"]["top3"]["2026-04"] == 80


@pytest.mark.django_db
def test_build_client_workflows_groups_metrics():
    auditados = pd.DataFrame(
        {
            "Protocolo": ["P1", "P2", "P3"],
            "Workflow": ["WF A", "WF A", "WF B"],
        }
    )
    falhas = pd.DataFrame(
        {
            "Protocolo": ["P1", "P4"],
            "Workflow": ["WF A", "WF B"],
            "Data de Análise": [datetime(2026, 5, 10), datetime(2026, 6, 5)],
            "Novo cenário": ["C1", "C2"],
            "Cenário": ["C1", "C2"],
        }
    )
    contestacao = pd.DataFrame(
        {
            "Protocolo": ["P1", "P2"],
            "Data": [datetime(2026, 5, 15), datetime(2026, 6, 1)],
            "classificacao_conforme": ["falha", "ok"],
        }
    )
    bundle = BRBDataBundle(
        na_demandas=pd.DataFrame(),
        na_falhas=pd.DataFrame(),
        treinamentos=pd.DataFrame(),
        falhas_gerais=falhas,
        contestacao=contestacao,
        auditados=auditados,
    )
    workflows = build_client_workflows(bundle)
    by_name = {row["nome"]: row for row in workflows}
    assert by_name["WF A"]["auditados"] == 2
    assert by_name["WF A"]["achados_fg"] == 1
    assert by_name["WF A"]["falhas_confirmadas"] == 1
    assert by_name["WF B"]["auditados"] == 1
    assert by_name["WF B"]["achados_fg"] == 1
    assert by_name["WF A"]["diagnostics"]["contestacao"]["total"] == 1
    assert by_name["WF A"]["diagnostics"]["auditoria"]["total"] == 1
    assert by_name["WF B"]["diagnostics"]["auditoria"]["total"] == 1
    assert isinstance(by_name["WF A"]["contestacao_timing"], dict)


def test_build_client_diagnostics_merges_sources():
    falhas = pd.DataFrame(
        {
            "Protocolo": ["P1", "P2"],
            "Workflow": ["WF A", "WF A"],
            "Data de Análise": [datetime(2026, 5, 10), datetime(2026, 5, 12)],
            "Novo cenário": ["C1", "C2"],
            "Cenário": ["C1", "C2"],
        }
    )
    contestacao = pd.DataFrame(
        {
            "Protocolo": ["P1"],
            "Data": [datetime(2026, 5, 15)],
            "classificacao_conforme": ["falha"],
        }
    )
    bundle = BRBDataBundle(
        na_demandas=pd.DataFrame(),
        na_falhas=pd.DataFrame(),
        treinamentos=pd.DataFrame(),
        falhas_gerais=falhas,
        contestacao=contestacao,
        auditados=pd.DataFrame({"Protocolo": ["P1"], "Workflow": ["WF A"]}),
    )
    diag = build_client_diagnostics(bundle)
    assert diag["auditoria"]["total"] == 2
    assert diag["contestacao"]["total"] == 1
