# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


def _module(name: str, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def test_persist_plan_queries_only_selected_protocols_in_chunks():
    from app.bots import replicacao_d1_db_bridge as bridge

    source_rows = [
        {
            "id": 1, "source_row_number": 1, "protocolo": "P1",
            "protocolo_normalizado": "P1", "workflow": "WF A D1",
            "data_analise": None, "hora": 9, "matricula_tipo": "manual",
        },
        {
            "id": 2, "source_row_number": 2, "protocolo": "P2",
            "protocolo_normalizado": "P2", "workflow": "WF A D1",
            "data_analise": None, "hora": 10, "matricula_tipo": "automatico",
        },
        {
            "id": 3, "source_row_number": 3, "protocolo": "X",
            "protocolo_normalizado": "X", "workflow": "WF A D1",
            "data_analise": None, "hora": 11, "matricula_tipo": "manual",
        },
    ]
    queried_chunks = []

    class SourceQuery:
        def __init__(self, rows):
            self.rows = rows

        def order_by(self, field):
            assert field == "source_row_number"
            return self

        def values(self, *_fields):
            return self.rows

    class SourceManager:
        def filter(self, **kwargs):
            assert kwargs["lote_id"] == 77
            chunk = list(kwargs["protocolo_normalizado__in"])
            queried_chunks.append(chunk)
            return SourceQuery([row for row in source_rows if row["protocolo_normalizado"] in chunk])

    source_model = SimpleNamespace(objects=SourceManager())
    snapshot_model = SimpleNamespace(
        objects=SimpleNamespace(filter=lambda **_kwargs: SimpleNamespace(first=lambda: None))
    )
    persist_plan = MagicMock(return_value="persisted")
    fake_modules = {
        "apps.replicacao_d1.config_models": _module(
            "apps.replicacao_d1.config_models",
            ReplicacaoD1ConfigSnapshot=snapshot_model,
        ),
        "apps.replicacao_d1.models": _module(
            "apps.replicacao_d1.models",
            ReplicacaoD1FonteRegistro=source_model,
        ),
        "apps.replicacao_d1.normalization": _module(
            "apps.replicacao_d1.normalization",
            normalize_protocolo=lambda value: str(value or "").strip(),
        ),
        "apps.replicacao_d1.services.plan_validation": _module(
            "apps.replicacao_d1.services.plan_validation",
            persist_plan=persist_plan,
        ),
    }
    plano = SimpleNamespace(
        run_id="run-selected",
        data_referencia=datetime(2026, 8, 31),
        workflows=["WF A", "WF QTD"],
        resumo=[
            {"Workflow": "WF A", "Workflow D1": "WF A D1"},
            {"Workflow": "WF QTD", "Workflow D1": "WF QTD D1", "Amostra Efetiva": 3},
        ],
        workflow_fila={"WF A": "", "WF QTD": ""},
        workflow_modo_replicacao={"WF A": "protocolos", "WF QTD": "qtd"},
        protocolos_por_workflow={"WF A": ["P2", "P1"], "WF QTD": ["X"]},
        qtd_por_workflow={"WF QTD": 3},
        workflow_brflow={"WF A": "WF A", "WF QTD": "WF QTD"},
        selection_reason_por_protocolo={},
        protocolo_meta_por_chave={},
        warnings=[],
        auditores_ativos=0,
        auditores_ativos_case=0,
    )

    with patch.object(bridge, "ensure_django_ready", return_value=True), patch.object(
        bridge, "_SOURCE_PROTOCOL_QUERY_CHUNK_SIZE", 1
    ), patch.dict(sys.modules, fake_modules):
        result = bridge.persist_plan_db(plano, settings={}, source_batch_id=77)

    assert result == "persisted"
    assert queried_chunks == [["P2"], ["P1"]]
    persisted_protocols = persist_plan.call_args.kwargs["protocolos"]
    assert [row["protocolo"] for row in persisted_protocols] == ["P2", "P1"]
    assert [row["source_record_id"] for row in persisted_protocols] == [2, 1]


def test_append_planning_event_db_is_best_effort_and_sanitizes_payload():
    from app.bots import replicacao_d1_db_bridge as bridge

    run = object()
    run_model = SimpleNamespace(
        objects=SimpleNamespace(filter=lambda **_kwargs: SimpleNamespace(first=lambda: run))
    )
    event_create = MagicMock()
    event_model = SimpleNamespace(objects=SimpleNamespace(create=event_create))
    fake_models = _module(
        "apps.replicacao_d1.models",
        ReplicacaoD1ExecutionEvent=event_model,
        ReplicacaoD1Run=run_model,
    )
    event = {
        "version": 1,
        "event_id": "evt-1",
        "run_id": "run-1",
        "phase": "planning_source",
        "state": "completed",
        "message": "Fonte carregada",
        "metrics": {"rows": 10, "token": "secret", "arquivo": "C:/sensitive/file.xlsx"},
    }

    with patch.object(bridge, "ensure_django_ready", return_value=True), patch.dict(
        sys.modules, {"apps.replicacao_d1.models": fake_models}
    ):
        assert bridge.append_planning_event_db(event) is True

    persisted = event_create.call_args.kwargs
    assert persisted["run"] is run
    assert persisted["status"] == "completed"
    assert persisted["payload"]["metrics"] == {"rows": 10}
    assert bridge.append_planning_event_db({"state": "progress"}) is False


def test_inject_planning_flags_preserves_explicit_execution_choice():
    from app.bots import replicacao_d1_db_bridge as bridge

    fake_flags = _module(
        "apps.replicacao_d1.feature_flags",
        optimized_planning_enabled=lambda: True,
        optimized_planning_shadow_enabled=lambda: True,
    )
    with patch.object(bridge, "ensure_django_ready", return_value=True), patch.dict(
        sys.modules, {"apps.replicacao_d1.feature_flags": fake_flags}
    ):
        injected = bridge.try_inject_planning_flags({})
        explicit = bridge.try_inject_planning_flags(
            {"replicacao_d1_planejamento_otimizado": False, "optimized_planning_shadow": False}
        )

    assert injected["optimized_planning"] is True
    assert injected["optimized_planning_shadow"] is True
    assert "optimized_planning" not in explicit
    assert explicit["replicacao_d1_planejamento_otimizado"] is False
    assert explicit["optimized_planning_shadow"] is False
