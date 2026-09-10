# -*- coding: utf-8 -*-
"""Testes da aba Falhas removidas (log SharePoint)."""
from __future__ import annotations

import json

from datetime import date

import pandas as pd

from report_falhas.io.data_loader import safe_str
from report_falhas.falhas_removidas import (
    _apply_falhas_removidas_to_base,
    _build_removidas_globals_empty,
    build_removidas_email_blurb,
    classify_removal_type,
    extract_failure_fingerprint,
    filter_removidas_period,
    get_removidas_state,
    match_base_rows,
    parse_tbga_json,
    summarize_removidas_period,
)
from report_falhas.html_pages import inject_email_intro


SAMPLE_BEFORE = json.dumps([
    {"Header": "TBGA_PROTOCOLO", "Value": "246796705"},
    {"Header": "TBGA_APP_FALHA_MATRICULA", "Value": "c22143q"},
    {"Header": "TBGA_APP_AUDITORIA_MATRICULA", "Value": "C96345A"},
    {"Header": "TBGA_APP_MOTIVO_FALHA", "Value": "NÃO SINALIZADO - DOCUMENTO DE IDENTIFICAÇÃO ILEGÍVEL"},
    {"Header": "TBGA_APP_ETAPA", "Value": "SOBREPOSIÇÃO"},
])

SAMPLE_AFTER = json.dumps([
    {"Header": "TBGA_PROTOCOLO", "Value": "246796705"},
    {"Header": "TBGA_APP_FALHA_MATRICULA", "Value": None},
    {"Header": "TBGA_APP_AUDITORIA_MATRICULA", "Value": "C96345A"},
    {"Header": "TBGA_APP_MOTIVO_FALHA", "Value": "NÃO SINALIZADO - FORMATAÇÃO/FONTE NO DOCUMENTO DE IDENTIFICAÇÃO ADULTERADA"},
    {"Header": "TBGA_APP_ETAPA", "Value": "ANÁLISE VISUAL"},
])

SAMPLE_DELETE = json.dumps([
    {"Header": "TBGA_PROTOCOLO", "Value": "999888777"},
    {"Header": "TBGA_APP_FALHA_MATRICULA", "Value": "c11111a"},
    {"Header": "TBGA_APP_MOTIVO_FALHA", "Value": "NÃO SINALIZADO - TESTE"},
    {"Header": "TBGA_APP_ETAPA", "Value": "ANÁLISE VISUAL"},
])


def _base_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Protocolo": "246796705",
            "Matrícula Agente": "c22143q",
            "Novo cenário": "NÃO SINALIZADO - DOCUMENTO DE IDENTIFICAÇÃO ILEGÍVEL",
            "Etapa": "SOBREPOSIÇÃO",
            "Data de Análise": pd.Timestamp("2026-06-19"),
            "Cliente": "PAGSEGURO",
        },
        {
            "Protocolo": "246796705",
            "Matrícula Agente": "C96345A",
            "Novo cenário": "NÃO SINALIZADO - FORMATAÇÃO/FONTE NO DOCUMENTO DE IDENTIFICAÇÃO ADULTERADA",
            "Etapa": "ANÁLISE VISUAL",
            "Data de Análise": pd.Timestamp("2026-06-19"),
            "Cliente": "PAGSEGURO",
        },
        {
            "Protocolo": "999888777",
            "Matrícula Agente": "c11111a",
            "Novo cenário": "NÃO SINALIZADO - TESTE",
            "Etapa": "ANÁLISE VISUAL",
            "Data de Análise": pd.Timestamp("2026-06-01"),
            "Cliente": "CLIENTE X",
        },
    ])


def test_parse_tbga_json():
    tbga = parse_tbga_json(SAMPLE_BEFORE)
    assert tbga["TBGA_PROTOCOLO"] == "246796705"
    assert tbga["TBGA_APP_FALHA_MATRICULA"] == "c22143q"


def test_parse_tbga_json_invalid():
    assert parse_tbga_json("") == {}
    assert parse_tbga_json("{not json}") == {}
    assert parse_tbga_json('{"x": 1}') == {}


def test_extract_failure_fingerprint_prefers_falha_matricula():
    fp = extract_failure_fingerprint(parse_tbga_json(SAMPLE_BEFORE))
    assert fp["protocolo"] == "246796705"
    assert fp["matricula_norm"] == "c22143q"
    assert "ilegivel" in fp["motivo_norm"] or "ileg" in fp["motivo_norm"]
    assert fp["etapa_norm"] == "sobreposicao"


def test_classify_removal_type():
    assert classify_removal_type("Edição") == "edit"
    assert classify_removal_type("Exclusão") == "delete"
    assert classify_removal_type("Delete") == "delete"
    assert classify_removal_type("") == "unknown"


def test_match_edicao_remove_before_state():
    base = _base_df()
    from report_falhas.io.data_loader import norm_matricula, norm_protocolo
    base["Protocolo"] = base["Protocolo"].apply(norm_protocolo)
    base["_mat_norm"] = base["Matrícula Agente"].apply(norm_matricula)

    fp = extract_failure_fingerprint(parse_tbga_json(SAMPLE_BEFORE))
    matches, status = match_base_rows(base, "Protocolo", fp)
    assert status == "removido"
    assert len(matches) == 1
    assert matches.iloc[0]["Etapa"] == "SOBREPOSIÇÃO"


def test_apply_edicao_nao_remove_estado_novo():
    base = _base_df()
    df_rem = pd.DataFrame([{
        "ID": "152",
        "Type": "Edição",
        "DateRemoved": pd.Timestamp("2026-06-20"),
        "UserRemove": "c96345a",
        "DeleteMotive": "",
        "ChangeMotive": "Outro",
        "FailureDetailJSON": SAMPLE_AFTER,
        "BeforeJSON": SAMPLE_BEFORE,
        "Path": "sites/.../tblFailuresRemoved",
        "__KIND__": "edit",
        "__PROTOCOLO__": "246796705",
        "__MATRICULA__": "c22143q",
        "__MATRICULA_NORM__": "c22143q",
        "__MOTIVO__": "NÃO SINALIZADO - DOCUMENTO DE IDENTIFICAÇÃO ILEGÍVEL",
        "__MOTIVO_NORM__": "nao sinalizado - documento de identificacao ilegivel",
        "__ETAPA__": "SOBREPOSIÇÃO",
        "__ETAPA_NORM__": "sobreposicao",
        "__MOTIVO_LOG__": "Outro",
        "__APLICAVEL__": True,
    }])

    out, resumo, det = _apply_falhas_removidas_to_base(base, df_rem)
    assert len(out) == 2
    assert int(resumo.iloc[0]["Linhas removidas"]) == 1
    etapas = set(out["Etapa"].tolist())
    assert "SOBREPOSIÇÃO" not in etapas
    assert "ANÁLISE VISUAL" in etapas
    assert det.iloc[0]["Status Match"] == "removido"


def test_apply_delete_remove_failure_detail():
    base = _base_df()
    df_rem = pd.DataFrame([{
        "ID": "200",
        "Type": "Exclusão",
        "DateRemoved": pd.Timestamp("2026-06-21"),
        "UserRemove": "admin",
        "DeleteMotive": "Duplicado",
        "ChangeMotive": "",
        "FailureDetailJSON": SAMPLE_DELETE,
        "BeforeJSON": "",
        "Path": "",
        "__KIND__": "delete",
        "__PROTOCOLO__": "999888777",
        "__MATRICULA__": "c11111a",
        "__MATRICULA_NORM__": "c11111a",
        "__MOTIVO__": "NÃO SINALIZADO - TESTE",
        "__MOTIVO_NORM__": "nao sinalizado - teste",
        "__ETAPA__": "ANÁLISE VISUAL",
        "__ETAPA_NORM__": "analise visual",
        "__MOTIVO_LOG__": "Duplicado",
        "__APLICAVEL__": True,
    }])

    out, resumo, _ = _apply_falhas_removidas_to_base(base, df_rem)
    assert len(out) == 2
    assert int(resumo.iloc[0]["Linhas removidas"]) == 1


def test_ambiguo_nao_remove():
    base = pd.DataFrame([
        {"Protocolo": "111", "Matrícula Agente": "c1", "Novo cenário": "A", "Etapa": "X", "Data de Análise": pd.Timestamp("2026-01-01")},
        {"Protocolo": "111", "Matrícula Agente": "c2", "Novo cenário": "B", "Etapa": "Y", "Data de Análise": pd.Timestamp("2026-01-01")},
    ])
    df_rem = pd.DataFrame([{
        "ID": "1", "Type": "Exclusão", "DateRemoved": pd.NaT, "UserRemove": "",
        "DeleteMotive": "", "ChangeMotive": "", "FailureDetailJSON": "", "BeforeJSON": "", "Path": "",
        "__KIND__": "delete",
        "__PROTOCOLO__": "111",
        "__MATRICULA__": "",
        "__MATRICULA_NORM__": "",
        "__MOTIVO__": "",
        "__MOTIVO_NORM__": "",
        "__ETAPA__": "",
        "__ETAPA_NORM__": "",
        "__MOTIVO_LOG__": "",
        "__APLICAVEL__": True,
    }])
    out, resumo, det = _apply_falhas_removidas_to_base(base, df_rem)
    assert len(out) == 2
    assert int(resumo.iloc[0]["Ambíguos"]) == 1
    assert det.iloc[0]["Status Match"] == "ambiguo"


def test_type_unknown_ignored():
    _build_removidas_globals_empty()
    base = _base_df()
    df_rem = pd.DataFrame([{
        "ID": "1", "Type": "Movido", "DateRemoved": pd.NaT, "UserRemove": "",
        "DeleteMotive": "", "ChangeMotive": "", "FailureDetailJSON": SAMPLE_DELETE,
        "BeforeJSON": "", "Path": "",
        "__KIND__": "unknown",
        "__PROTOCOLO__": "999888777",
        "__MATRICULA__": "c11111a",
        "__MATRICULA_NORM__": "c11111a",
        "__MOTIVO__": "", "__MOTIVO_NORM__": "", "__ETAPA__": "", "__ETAPA_NORM__": "",
        "__MOTIVO_LOG__": "",
        "__APLICAVEL__": False,
    }])
    out, resumo, det = _apply_falhas_removidas_to_base(base, df_rem)
    assert len(out) == 3
    assert int(resumo.iloc[0]["Linhas removidas"]) == 0
    assert det.iloc[0]["Status Match"] == "ignorado_type"


def test_filter_period_returns_empty_when_no_match():
    df = pd.DataFrame({
        "DateRemoved": [pd.Timestamp("2026-05-01"), pd.Timestamp("2026-07-01")],
        "Protocolo": ["1", "2"],
    })
    out = filter_removidas_period(df, date(2026, 6, 1), date(2026, 6, 30))
    assert out.empty


def test_summarize_removidas_period_dedupes_and_retroativas():
    det = pd.DataFrame([
        {
            "Status Match": "removido",
            "Protocolo": "111",
            "Data de Análise Base": pd.Timestamp("2026-05-15"),
            "Matrícula Agente Base": "c1",
            "DateRemoved": pd.Timestamp("2026-06-10"),
        },
        {
            "Status Match": "removido",
            "Protocolo": "111",
            "Data de Análise Base": pd.Timestamp("2026-05-15"),
            "Matrícula Agente Base": "c1",
            "DateRemoved": pd.Timestamp("2026-06-11"),
        },
        {
            "Status Match": "removido",
            "Protocolo": "222",
            "Data de Análise Base": pd.Timestamp("2026-06-05"),
            "Matrícula Agente Base": "c2",
            "DateRemoved": pd.Timestamp("2026-06-12"),
        },
    ])
    stats = summarize_removidas_period(det, date(2026, 6, 1))
    assert stats["eventos"] == 3
    assert stats["unicas"] == 2
    assert stats["retroativas"] == 1
    assert "mai 1" in safe_str(stats["meses_retroativos_txt"])


def test_email_blurb_empty_when_no_removidas_in_period():
    _build_removidas_globals_empty()
    assert build_removidas_email_blurb(date(2026, 6, 1), date(2026, 6, 30)) == ""


def test_email_blurb_neutral_copy_with_counts():
    from report_falhas import falhas_removidas as fr
    fr._removidas_detalhe_df = pd.DataFrame([
        {
            "Status Match": "removido",
            "Protocolo": "111",
            "Data de Análise Base": pd.Timestamp("2026-05-15"),
            "Matrícula Agente Base": "c1",
            "DateRemoved": pd.Timestamp("2026-06-10"),
            "Localidade Base": "Brasília",
        },
    ])
    html = build_removidas_email_blurb(date(2026, 6, 1), date(2026, 6, 30), scope_name="Brasília")
    assert "log SharePoint/BI" in html
    assert "qualidade" not in html.lower()
    assert "meses anteriores" in html
    assert "não refletem erro de carga" in html


def test_email_blurb_hides_when_already_reported(tmp_path):
    from report_falhas import falhas_removidas as fr
    fr._removidas_detalhe_df = pd.DataFrame([
        {
            "Status Match": "removido",
            "Protocolo": "111",
            "Data de Análise Base": pd.Timestamp("2026-05-15"),
            "Matrícula Agente Base": "c1",
            "DateRemoved": pd.Timestamp("2026-06-10"),
            "Localidade Base": "Brasília",
        },
    ])
    html1 = build_removidas_email_blurb(
        date(2026, 6, 1), date(2026, 6, 30), scope_name="Brasília", out_dir=tmp_path,
    )
    assert html1
    html2 = build_removidas_email_blurb(
        date(2026, 6, 1), date(2026, 6, 30), scope_name="Brasília", out_dir=tmp_path,
    )
    assert html2 == ""


def test_email_intro_removidas_notice_when_blurb_present():
    blurb = (
        "<div>Ajustes de base (log SharePoint/BI)</div>"
        "<div>Neste período: <b>1</b> retificação(ões)</div>"
    )
    html = inject_email_intro(
        "<html><body></body></html>",
        "Brasília",
        "01/06/2026 a 22/06/2026",
        "• teste",
        removidas_html=blurb,
    )
    assert "Novidade:" in html
    assert "Ajustes de base (log SharePoint/BI)" in html
    assert "#B91C1C" in html
    assert "logo abaixo" in html


def test_email_intro_no_removidas_notice_without_blurb():
    html = inject_email_intro(
        "<html><body></body></html>",
        "Brasília",
        "01/06/2026 a 22/06/2026",
        "• teste",
    )
    assert "Novidade:" not in html
