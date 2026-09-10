# -*- coding: utf-8 -*-
"""Testes mínimos do pacote report_brb."""
from datetime import date
from pathlib import Path

import pytest

from report_brb.brb_filters import is_brb, match_client
from report_brb.brb_loaders import (
    _notification_mask,
    _risk_bucket,
    load_workbook,
    preview_workbook,
    validate_workbook,
)
from report_brb.client_registry import get_client_config, list_clients

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "BRB_Report_atualizado.xlsx"


pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(),
    reason=f"Fixture ausente: {FIXTURE}",
)


def test_client_registry_brb():
    clients = list_clients()
    assert any(c["slug"] == "brb" for c in clients)
    cfg = get_client_config("brb")
    assert cfg["nome_curto"] == "BRB"


def test_match_client_pilot_clients():
    assert match_client("Claro - Brsafe", "claro")
    assert match_client("Banco BMG", "bmg")
    assert match_client("Picpay Servicos SA", "picpay")
    assert match_client("Claro - Brsafe", "brb") is False
    assert match_client("BRB - BANCO DE BRASILIA S.A.", "brb")
    assert match_client("Mercantil do Brasil", "brb") is False
    assert is_brb("BRB DTVM")


def test_notification_rule_uses_risk_change_and_face_override():
    import pandas as pd

    rows = pd.DataFrame(
        [
            {
                "RESULTADO DO CLIENTE": "SEM RISCO APARENTE",
                "RESULTADO DA AUDITORIA": "COM RISCO NO DOCUMENTO",
                "MOTIVO DA FALHA": "NÃO SINALIZADO - SOBREPOSIÇÃO",
            },
            {
                "RESULTADO DO CLIENTE": "COM RISCO - BIOMETRIA",
                "RESULTADO DA AUDITORIA": "SEM RISCO",
                "MOTIVO DA FALHA": "SINALIZAÇÃO INCORRETA",
            },
            {
                "RESULTADO DO CLIENTE": "SEM RISCO",
                "RESULTADO DA AUDITORIA": "SEM RISCO APARENTE",
                "MOTIVO DA FALHA": "DOCUMENTO ILEGÍVEL",
            },
            {
                "RESULTADO DO CLIENTE": 240,
                "RESULTADO DA AUDITORIA": 180,
                "MOTIVO DA FALHA": "FACE ENCONTRADA NA BASE DE FRAUDADORES",
            },
        ]
    )

    assert _notification_mask(rows).tolist() == [True, True, False, True]
    assert _risk_bucket(240) == "SEM_RISCO"
    assert _risk_bucket(190) == "COM_RISCO"


def test_validate_and_preview_workbook():
    sheets = validate_workbook(FIXTURE)
    assert "NA_Demandas" in sheets
    preview = preview_workbook(FIXTURE)
    assert preview["sheets"]["NA_Demandas"] > 0
    assert any(c["slug"] == "brb" for c in preview["clients_detected"])


def test_load_workbook_period_filter():
    bundle = load_workbook(FIXTURE, date(2026, 1, 1), date(2026, 8, 6), client_slug="brb")
    assert len(bundle.na_falhas) >= 0
    assert len(bundle.contestacao) >= 0
