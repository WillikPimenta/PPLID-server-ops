# -*- coding: utf-8 -*-
"""Smoke da tarefa fila_aberta (collection mockada)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.bots.produtividade_case.bot import executar_pipeline
from app.bots.produtividade_case.reports.fila import (
    FILA_ITEMS_MAX,
    aggregate_fila,
    build_pipeline_by_idade,
    build_pipeline_fila,
    build_pipeline_sample,
    calc_periodo_fila,
    match_fila_aberta,
)


def test_match_fila_aberta_uses_audit_new_and_monthly_window():
    ref = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    m = match_fila_aberta(ref)
    assert m["origin.requestType"] == "AUDIT"
    assert m["transactionStatus"] == "NEW"
    assert m["createdAt"] == {
        "$gte": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "$lt": ref,
    }
    assert "blockedDate" not in m
    assert "$or" not in m


def test_periodo_fila_day_one_keeps_previous_cycle():
    inicio, fim = calc_periodo_fila(
        datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)
    )
    assert inicio == datetime(2026, 7, 2, tzinfo=timezone.utc)
    assert fim == datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)


def test_periodo_fila_day_two_starts_current_cycle():
    inicio, fim = calc_periodo_fila(
        datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
    )
    assert inicio == datetime(2026, 8, 2, tzinfo=timezone.utc)
    assert fim == datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)


def test_periodo_fila_uses_calendar_day_in_sao_paulo():
    inicio, fim = calc_periodo_fila(
        datetime(2026, 8, 2, 0, 30, tzinfo=timezone.utc)
    )
    assert inicio == datetime(2026, 7, 2, tzinfo=timezone.utc)
    assert fim == datetime(2026, 8, 2, 0, 30, tzinfo=timezone.utc)


def test_periodo_fila_handles_year_boundary():
    inicio, fim = calc_periodo_fila(
        datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
    )
    assert inicio == datetime(2026, 12, 2, tzinfo=timezone.utc)
    assert fim == datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_pipeline_fila_no_facet_or_dollar_now():
    """DocumentDB rejeita $facet e $$NOW."""
    ref = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    pipeline = build_pipeline_fila(now=ref)
    assert all("$facet" not in stage for stage in pipeline)
    assert "$$NOW" not in str(pipeline)
    assert pipeline[0]["$match"] == match_fila_aberta(ref)
    blob = str(build_pipeline_by_idade(now=ref))
    assert "$toDate" in blob
    assert "$_id" in blob
    assert "2026" in blob or repr(ref) in blob or str(ref) in blob


def test_pipeline_sample_full_list_cap():
    pipe = build_pipeline_sample(now=datetime(2026, 7, 17, tzinfo=timezone.utc))
    assert any(s.get("$limit") == FILA_ITEMS_MAX for s in pipe)
    assert "origin.customerName" in str(pipe)
    assert FILA_ITEMS_MAX >= 100_000
    # limite explícito menor ainda respeitado
    pipe200 = build_pipeline_sample(
        now=datetime(2026, 7, 17, tzinfo=timezone.utc), limit=200
    )
    assert any(s.get("$limit") == 200 for s in pipe200)


def test_aggregate_fila_runs_separate_pipelines():
    collection = MagicMock()

    def _agg(pipeline, **_kwargs):
        match = pipeline[0].get("$match")
        assert match == match_fila_aberta(
            datetime(2026, 7, 17, tzinfo=timezone.utc)
        )
        if any("$group" in s and s["$group"].get("_id") is None for s in pipeline):
            return [{"_id": None, "n": 7}]
        if any("$limit" in s for s in pipeline):
            return [
                {
                    "protocolo_id": "abc123",
                    "transaction_status": "PENDING",
                    "idade_bucket": "0-1h",
                    "created_ts": datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc),
                    "workflow_origem": "WF",
                    "cliente_origem": "Cliente X",
                    "protocolo_origem": "CUST-99",
                    "cadastro_origem_at": datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
                }
            ]
        if pipeline[-1].get("$group", {}).get("_id") == "$idade_bucket":
            return [{"_id": "0-1h", "count": 7}]
        return [{"_id": "PENDING", "count": 7}]

    collection.aggregate.side_effect = _agg
    payload = aggregate_fila(collection, now=datetime(2026, 7, 17, tzinfo=timezone.utc))
    assert payload["total_abertos"] == 7
    assert collection.aggregate.call_count == 5
    assert len(payload["sample_items"]) == 1
    assert payload["sample_items"][0]["protocolo_id"] == "abc123"
    assert payload["sample_items"][0]["protocolo_origem"] == "CUST-99"
    assert payload["sample_items"][0]["cliente_origem"] == "Cliente X"
    assert payload["sample_items"][0]["cadastro_origem_at"]
    assert all("$facet" not in str(c.args[0]) for c in collection.aggregate.call_args_list)
    # idade não é só 24h+ quando há created_ts via ObjectId
    assert payload["by_idade_bucket"][0]["key"] == "0-1h"
    assert payload["by_idade_bucket"][0]["count"] == 7


def test_executar_pipeline_fila_aberta(tmp_path: Path, capsys):
    collection = MagicMock()

    def _agg(pipeline, **_kwargs):
        if any(s.get("$group", {}).get("_id") is None for s in pipeline):
            return [{"_id": None, "n": 3}]
        if any("$limit" in s for s in pipeline):
            return [
                {
                    "protocolo_id": "x1",
                    "transaction_status": "PENDING",
                    "idade_bucket": "1-4h",
                    "created_ts": None,
                    "workflow_origem": "",
                }
            ]
        if pipeline[-1].get("$group", {}).get("_id") == "$idade_bucket":
            return [{"_id": "0-1h", "count": 3}]
        return [{"_id": "PENDING", "count": 3}]

    collection.aggregate.side_effect = _agg
    client = MagicMock()

    settings = {
        "tarefas": ["fila_aberta"],
        "dir_hora": str(tmp_path / "hora"),
        "dir_tempo_logado": str(tmp_path / "tempo"),
        "dir_consolidado": str(tmp_path / "cons"),
        "dir_temp": str(tmp_path / "temp"),
    }

    with (
        patch(
            "app.bots.produtividade_case.bot.validate_docdb_config",
            return_value=(True, ""),
        ),
        patch(
            "app.bots.produtividade_case.bot.get_collection",
            return_value=(client, collection),
        ),
    ):
        executar_pipeline(settings)

    assert collection.aggregate.call_count >= 5
    json_files = list((tmp_path / "temp").rglob("fila_aberta_*.json"))
    assert len(json_files) == 1
    out = capsys.readouterr().out
    assert "CASE_FILA_SAVED|" in out
