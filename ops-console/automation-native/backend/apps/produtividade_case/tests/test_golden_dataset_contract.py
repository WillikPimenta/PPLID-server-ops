"""Contrato independente da fixture sintética usada na reconciliação do Case Manager."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from statistics import median


FIXTURE = Path(__file__).parent / "fixtures" / "case_manager_golden.json"
FORBIDDEN_FIELDS = {"cpf", "contrato", "nome", "email", "source_file"}


def _dataset() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _nearest_rank(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def test_golden_fixture_is_versioned_synthetic_and_has_no_pii_fields():
    dataset = _dataset()
    assert dataset["schema_version"] == 1
    assert "sintético" in dataset["description"]
    assert dataset["timezone"] == "America/Sao_Paulo"
    for fact in dataset["facts"]:
        assert FORBIDDEN_FIELDS.isdisjoint(fact)
        assert fact["protocolo_destino"].startswith("QA-P")
        assert fact["protocolo_origem"].startswith("QA-O")
        assert fact["matricula_destino"].startswith("qa")
        timestamp = datetime.fromisoformat(fact["conclusao_destino_at"])
        assert timestamp.utcoffset() is not None


def test_golden_expected_totals_are_calculated_independently():
    dataset = _dataset()
    facts = dataset["facts"]
    expected = dataset["expected"]
    times = [
        row["tempo_analise_segundos"]
        for row in facts
        if row["tempo_analise_segundos"] is not None
        and row["tempo_analise_segundos"] >= 0
    ]
    comparable = [
        row
        for row in facts
        if row["resultado_origem"].strip() and row["resultado_destino"].strip()
    ]
    coincident = [
        row
        for row in comparable
        if row["resultado_origem"] == row["resultado_destino"]
    ]

    assert len(facts) == expected["total_protocolos"]
    assert len(times) == expected["tempo_com_cobertura"]
    assert len(facts) - len(times) == expected["tempo_sem_cobertura"]
    assert sum(times) == expected["analysis_seconds_sum"]
    assert sum(times) / len(times) == expected["tma_seconds"]
    assert median(times) == expected["mediana_seconds"]
    assert _nearest_rank(times, 0.90) == expected["p90_seconds"]
    assert len(comparable) == expected["total_comparavel"]
    assert len(coincident) == expected["total_coincidente"]
    assert len(comparable) - len(coincident) == expected["total_nao_coincidente"]
    assert len(facts) - len(comparable) == expected["total_nao_comparavel"]
    assert round(100 * len(coincident) / len(comparable), 2) == expected["taxa_coincidencia"]


def test_golden_workflow_breakdown_reconciles_with_grand_total():
    dataset = _dataset()
    actual: dict[str, int] = {}
    for fact in dataset["facts"]:
        workflow = fact["workflow_origem"]
        actual[workflow] = actual.get(workflow, 0) + 1

    assert actual == dataset["expected"]["by_workflow"]
    assert sum(actual.values()) == dataset["expected"]["total_protocolos"]
