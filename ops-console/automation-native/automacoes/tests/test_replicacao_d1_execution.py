# -*- coding: utf-8 -*-
import json
from datetime import datetime
from types import SimpleNamespace

from app.bots.replicacao_d1.domain import RunStatus, WorkflowStatus
from app.bots.replicacao_d1.execution import (
    build_replication_run_result,
    collect_workflow_results,
    run_status_allows_consumo,
    run_status_allows_retention,
    write_run_manifest,
)
from app.bots.replicacao_d1.manifest import MANIFEST_STDOUT_PREFIX


def test_collect_workflow_results_from_estado():
    plano = SimpleNamespace(
        workflows=["WF_A", "WF_B"],
        protocolos_por_workflow={"WF_A": ["1", "2"], "WF_B": []},
    )
    estado = {
        "workflows": {
            "WF_A": {"status": "SALVO_OK", "workflow_brflow": "A BRFlow", "csv": "a.csv"},
            "WF_B": {"status": "ERRO", "motivo": "timeout"},
        }
    }
    results = collect_workflow_results(plano, estado)
    assert len(results) == 2
    by_wf = {r.workflow: r for r in results}
    assert by_wf["WF_A"].status == WorkflowStatus.UPLOADED
    assert by_wf["WF_A"].protocolos_planejados == 2
    assert by_wf["WF_B"].status == WorkflowStatus.FAILED


def test_build_replication_run_result_partial():
    results = collect_workflow_results(
        SimpleNamespace(workflows=["A", "B"], protocolos_por_workflow={"A": [], "B": []}),
        {"workflows": {"A": {"status": "SALVO_OK"}, "B": {"status": "ERRO"}}},
    )
    run = build_replication_run_result("r1", results, started_at=datetime.now())
    assert run.status == RunStatus.PARTIAL


def test_skip_bloqueante_vira_falha_e_run_parcial():
    results = collect_workflow_results(
        SimpleNamespace(workflows=["A", "B"], protocolos_por_workflow={"A": ["1"], "B": ["2"]}),
        {
            "workflows": {
                "A": {"status": "SALVO_OK"},
                "B": {
                    "status": "PULADO",
                    "motivo_codigo": "CSV_AUSENTE",
                    "motivo_resumo": "CSV ausente",
                },
            }
        },
    )

    assert results[1].status == WorkflowStatus.FAILED
    assert results[1].status_brflow_raw == "PULADO"
    assert build_replication_run_result("r-skip", results).status == RunStatus.PARTIAL


def test_apenas_skips_benignos_conclui_com_aviso():
    results = collect_workflow_results(
        SimpleNamespace(workflows=["A"], protocolos_por_workflow={"A": []}),
        {
            "workflows": {
                "A": {
                    "status": "PULADO",
                    "motivo_codigo": "SEM_PROTOCOLOS",
                }
            }
        },
    )

    assert results[0].status == WorkflowStatus.SKIPPED
    assert build_replication_run_result("r-benigno", results).status == RunStatus.COMPLETED


def test_modo_qtd_preserva_planejado_e_sem_alteracao_nao_conta_envio():
    plano = SimpleNamespace(
        workflows=["WF QTD"],
        protocolos_por_workflow={"WF QTD": []},
        qtd_por_workflow={"WF QTD": 25},
        workflow_modo_replicacao={"WF QTD": "qtd"},
    )
    results = collect_workflow_results(
        plano,
        {
            "workflows": {
                "WF QTD": {
                    "status": "SEM_ALTERACAO",
                    "modo": "qtd",
                    "quantidade_alvo": 25,
                }
            }
        },
    )

    assert results[0].protocolos_planejados == 25
    assert results[0].protocolos_enviados == 0


def test_run_status_gates():
    assert run_status_allows_retention(RunStatus.COMPLETED) is True
    assert run_status_allows_retention(RunStatus.PARTIAL) is False
    assert run_status_allows_consumo(RunStatus.PARTIAL) is True
    assert run_status_allows_consumo(RunStatus.FAILED) is False


def test_write_run_manifest_atomic(tmp_path, capsys):
    plano = SimpleNamespace(
        run_id="20260810_120000",
        estado_execucao_path=str(tmp_path / "execucao.json"),
    )
    (tmp_path / "execucao.json").write_text("{}", encoding="utf-8")
    run = build_replication_run_result(
        plano.run_id,
        [],
        started_at=datetime.now(),
        planning_error="",
    )
    path = write_run_manifest(plano, run, {"config_version": 1, "config_hash": "abc"})
    assert path is not None
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == plano.run_id
    assert data["schema_version"] == 1
    captured = capsys.readouterr()
    assert MANIFEST_STDOUT_PREFIX in captured.out
