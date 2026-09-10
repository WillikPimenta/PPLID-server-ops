# -*- coding: utf-8 -*-
from apps.produtividade.services.meta_context import (
    META_COMPLIANCE,
    META_FRAUD,
    META_MISTA,
    classify_team,
    resolve_meta_from_teams,
)


def test_classify_team_fraud_compliance_unknown():
    assert classify_team("Operacional/Fraud") == "fraud"
    assert classify_team("Operacional Fraud") == "fraud"
    assert classify_team("Operacional/Compliance") == "compliance"
    assert classify_team("Qualidade") == "unknown"
    assert classify_team("") == "unknown"
    assert classify_team(None) == "unknown"


def test_meta_only_fraud():
    meta = resolve_meta_from_teams(["Operacional/Fraud", "Operacional/Fraud"])
    assert meta.evaluation_type == "fraud"
    assert meta.applied_meta == META_FRAUD
    assert meta.hourly_threshold == round(META_FRAUD / 5.5, 2)
    assert meta.status_for(93.0) == "atingiu"
    assert meta.status_for(91.0) == "abaixo"


def test_meta_only_compliance():
    meta = resolve_meta_from_teams(["Operacional/Compliance"])
    assert meta.evaluation_type == "compliance"
    assert meta.applied_meta == META_COMPLIANCE
    assert meta.status_for(97.0) == "atingiu"
    assert meta.status_for(96.5) == "abaixo"


def test_meta_mista_fixed_95_not_average():
    meta = resolve_meta_from_teams(["Operacional/Fraud", "Operacional/Compliance"])
    assert meta.evaluation_type == "mista"
    assert meta.applied_meta == META_MISTA
    assert meta.applied_meta != (META_FRAUD + META_COMPLIANCE) / 2


def test_unknown_only_nao_avaliavel():
    meta = resolve_meta_from_teams(["Qualidade", "", None])
    assert meta.evaluation_type == "nao_identificada"
    assert meta.applied_meta is None
    assert meta.status_for(90.0) == "nao_avaliavel"
    assert "não identific" in meta.justification().lower()


def test_unknown_plus_fraud_uses_fraud():
    meta = resolve_meta_from_teams(["Operacional/Fraud", "Sem equipe"])
    assert meta.evaluation_type == "fraud"
    assert meta.applied_meta == META_FRAUD
    assert meta.has_unknown_teams is True


def test_justification_mista_example_shape():
    meta = resolve_meta_from_teams(["Operacional/Fraud", "Operacional/Compliance"])
    text = meta.justification(93.8)
    assert "mista" in text.lower()
    assert "95" in text
    assert "93,8" in text or "93.8" in text
    assert "abaixo" in text.lower()


def test_to_dict_payload():
    meta = resolve_meta_from_teams(["Operacional/Compliance"])
    payload = meta.to_dict(98.0)
    assert payload["evaluation_type"] == "compliance"
    assert payload["applied_meta"] == META_COMPLIANCE
    assert payload["indicator_status"] == "atingiu"
    assert payload["gap_pp"] == 1.0
    assert payload["realized_pct"] == 98.0
