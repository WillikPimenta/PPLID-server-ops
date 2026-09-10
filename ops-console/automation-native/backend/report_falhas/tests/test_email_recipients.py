# -*- coding: utf-8 -*-
"""Testes de destinatarios de e-mail por escopo (BU)."""
import report_falhas.config_report as cfg


def _sample_lists():
    return {
        "lideres_bsb": ["bsb1@experian.com", "shared@experian.com"],
        "lideres_sc": ["sc1@experian.com", "shared@experian.com"],
        "capacitacao": ["cap@experian.com"],
        "planejamento": ["plan@experian.com"],
        "processos_riscos": ["proc@experian.com"],
    }


def test_resolve_brasilia_includes_bsb_and_shared(monkeypatch):
    monkeypatch.setattr(cfg, "EMAIL_LISTS", _sample_lists())
    to_list, cc_list = cfg.resolve_email_recipients("Brasília")
    assert "bsb1@experian.com" in to_list
    assert "cap@experian.com" in to_list
    assert "plan@experian.com" in to_list
    assert "proc@experian.com" in to_list
    assert "sc1@experian.com" not in to_list
    assert cc_list == []
    assert to_list.count("shared@experian.com") == 1


def test_resolve_sao_carlos_includes_sc_and_shared(monkeypatch):
    monkeypatch.setattr(cfg, "EMAIL_LISTS", _sample_lists())
    to_list, _ = cfg.resolve_email_recipients("São Carlos")
    assert "sc1@experian.com" in to_list
    assert "cap@experian.com" in to_list
    assert "bsb1@experian.com" not in to_list


def test_consolidado_skips_recipients(monkeypatch):
    monkeypatch.setattr(cfg, "EMAIL_LISTS", _sample_lists())
    assert cfg.should_skip_email_preview("Consolidado") is True
    to_list, cc_list = cfg.resolve_email_recipients("Consolidado")
    assert to_list == []
    assert cc_list == []


def test_deduplicates_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "EMAIL_LISTS",
        {"lideres_bsb": ["A@experian.com", "a@experian.com"], "capacitacao": []},
    )
    monkeypatch.setattr(cfg, "EMAIL_SCOPE_LISTS", {"Brasília": ["lideres_bsb"]})
    to_list, _ = cfg.resolve_email_recipients("Brasília")
    assert to_list == ["A@experian.com"]


def test_invalid_emails_filtered(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "EMAIL_LISTS",
        {"lideres_bsb": ["invalid", "ok@experian.com", "@bad.com"]},
    )
    monkeypatch.setattr(cfg, "EMAIL_SCOPE_LISTS", {"Brasília": ["lideres_bsb"]})
    to_list, _ = cfg.resolve_email_recipients("Brasília")
    assert to_list == ["ok@experian.com"]


def test_resolve_executivo_para_and_cc(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "EMAIL_LISTS",
        {
            "gerencia_executivo": [
                "rogerio.velista@experian.com",
                "sulamita.nunes@experian.com",
            ],
            "gerencia_executivo_cc": [
                "lucelia.hiratani@experian.com",
            ],
        },
    )
    monkeypatch.setattr(cfg, "EMAIL_SCOPE_LISTS", {"Executivo": ["gerencia_executivo"]})
    monkeypatch.setattr(cfg, "EMAIL_CC_BY_SCOPE", {"Executivo": ["gerencia_executivo_cc"]})
    to_list, cc_list = cfg.resolve_email_recipients("Executivo")
    assert "rogerio.velista@experian.com" in to_list
    assert "lucelia.hiratani@experian.com" in cc_list
    assert cfg.should_skip_email_preview("Executivo") is False
