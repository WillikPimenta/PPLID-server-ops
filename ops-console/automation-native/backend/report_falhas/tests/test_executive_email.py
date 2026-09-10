# -*- coding: utf-8 -*-
"""Testes de copy e assunto do e-mail executivo."""
from datetime import date

from report_falhas.email_copy import (
    build_executive_email_body,
    build_executive_email_subject,
    build_executive_period_ref,
)


def test_executive_subject():
    subj = build_executive_email_subject(date(2026, 6, 1), date(2026, 6, 30))
    assert "Brasília x São Carlos" in subj
    assert "01/06/2026 a 30/06/2026" in subj


def test_executive_period_ref_fechamento():
    ref = build_executive_period_ref(date(2026, 6, 1), date(2026, 6, 30), fechamento_mes=True)
    assert "fechamento de junho" in ref
    assert "01/06/2026 a 30/06/2026" in ref


def test_executive_period_ref_mtd():
    ref = build_executive_period_ref(date(2026, 7, 1), date(2026, 7, 6), fechamento_mes=False)
    assert ref == "período de 01/07/2026 a 06/07/2026"


def test_executive_email_body_fechamento():
    html = build_executive_email_body(date(2026, 6, 1), date(2026, 6, 30), fechamento_mes=True)
    assert "Olá, pessoal." in html
    assert "Brasília" in html
    assert "São Carlos" in html
    assert "fechamento de junho" in html
    assert "HTML anexo" in html


def test_executive_email_body_mtd():
    html = build_executive_email_body(date(2026, 7, 1), date(2026, 7, 6), fechamento_mes=False)
    assert "período de 01/07/2026 a 06/07/2026" in html
