"""Testes da fila Documentoscopia 3.1 vs G auditoria (planejamento + BRFlow)."""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_planning import (
    PlanoReplicacao,
    _normalizar_fila,
    agrupar_workflows_por_fila,
    eh_fila_documentoscopia_31,
    exportar_protocolos_csv_por_fila,
    resolver_filtros_brflow_por_fila,
    subpasta_protocolos_fila,
)
from app.config import (
    REPLICACAO_D1_SUBPASTA_BRFLOW,
    REPLICACAO_D1_SUBPASTA_CASE,
    REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
    REPLICACAO_FILA_G_AUDITORIA,
    REPLICACAO_WORKFLOW_COD_DOCUMENTOSCOPIA_31,
    REPLICACAO_WORKFLOW_COD_G_AUDITORIA,
)


def test_normalizar_fila():
    assert _normalizar_fila("") == REPLICACAO_FILA_G_AUDITORIA
    assert _normalizar_fila("G auditoria") == REPLICACAO_FILA_G_AUDITORIA
    assert _normalizar_fila("3.1") == REPLICACAO_FILA_DOCUMENTOSCOPIA_31
    assert _normalizar_fila("Documentoscopia 3.1") == REPLICACAO_FILA_DOCUMENTOSCOPIA_31
    assert eh_fila_documentoscopia_31("3.1") is True
    assert eh_fila_documentoscopia_31("G auditoria") is False


def test_resolver_filtros_brflow_por_fila():
    g = resolver_filtros_brflow_por_fila(REPLICACAO_FILA_G_AUDITORIA)
    assert g["replicacao_workflow_cod"] == REPLICACAO_WORKFLOW_COD_G_AUDITORIA
    assert g["replicacao_cliente_cod"] == "751"

    f31 = resolver_filtros_brflow_por_fila(REPLICACAO_FILA_DOCUMENTOSCOPIA_31)
    assert f31["replicacao_workflow_cod"] == REPLICACAO_WORKFLOW_COD_DOCUMENTOSCOPIA_31
    assert f31["replicacao_cliente_cod"] == "751"


def test_agrupar_workflows_por_fila_ordem():
    plano = PlanoReplicacao(
        data_referencia=datetime(2026, 6, 1),
        pasta_protocolos=Path("."),
        pasta_resumo=Path("."),
        workflow_fila={
            "WF G": REPLICACAO_FILA_G_AUDITORIA,
            "WF 31": REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
        },
    )
    grupos = agrupar_workflows_por_fila(["WF 31", "WF G"], plano)
    assert list(grupos.keys()) == [REPLICACAO_FILA_G_AUDITORIA, REPLICACAO_FILA_DOCUMENTOSCOPIA_31]
    assert grupos[REPLICACAO_FILA_G_AUDITORIA] == ["WF G"]
    assert grupos[REPLICACAO_FILA_DOCUMENTOSCOPIA_31] == ["WF 31"]


def test_exportar_protocolos_csv_por_fila_subpastas(tmp_path):
    wf_fila = {
        "WF G": REPLICACAO_FILA_G_AUDITORIA,
        "WF 31": REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
    }
    paths = exportar_protocolos_csv_por_fila(
        {"WF G": ["1", "2"], "WF 31": ["3"]},
        tmp_path,
        wf_fila,
    )
    assert not list(tmp_path.glob("*.csv"))
    brflow_csvs = list((tmp_path / REPLICACAO_D1_SUBPASTA_BRFLOW).glob("*.csv"))
    case_csvs = list((tmp_path / REPLICACAO_D1_SUBPASTA_CASE).glob("*.csv"))
    assert len(brflow_csvs) == 1
    assert len(case_csvs) == 1
    assert REPLICACAO_D1_SUBPASTA_BRFLOW in str(paths["WF G"]).replace("\\", "/")
    assert REPLICACAO_D1_SUBPASTA_CASE in str(paths["WF 31"]).replace("\\", "/")
    assert subpasta_protocolos_fila(REPLICACAO_FILA_G_AUDITORIA) == REPLICACAO_D1_SUBPASTA_BRFLOW
    assert subpasta_protocolos_fila(REPLICACAO_FILA_DOCUMENTOSCOPIA_31) == REPLICACAO_D1_SUBPASTA_CASE


def test_carregar_plano_retomada_resolve_csv_case(tmp_path, monkeypatch):
    from app.bots import replicacao_aud_d1_planning as d1

    run_id = "test_subpastas_retomada"
    pasta_proto = tmp_path / "protocolos" / run_id
    case_dir = pasta_proto / REPLICACAO_D1_SUBPASTA_CASE
    case_dir.mkdir(parents=True)
    csv_case = case_dir / "WF CASE.csv"
    csv_case.write_text("999\n", encoding="utf-8-sig")

    estado = {
        "run_id": run_id,
        "pasta_protocolos": str(pasta_proto),
        "pastas_fila": {
            REPLICACAO_D1_SUBPASTA_BRFLOW: str(pasta_proto / REPLICACAO_D1_SUBPASTA_BRFLOW),
            REPLICACAO_D1_SUBPASTA_CASE: str(case_dir),
        },
        "workflows": {
            "WF CASE": {
                "status": "PENDENTE",
                "csv": "",
                "fila": REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
                "subpasta": REPLICACAO_D1_SUBPASTA_CASE,
            }
        },
    }
    estado_path = tmp_path / f"execucao_d1_{run_id}.json"
    estado_path.write_text(json.dumps(estado), encoding="utf-8")

    monkeypatch.setattr(d1, "PASTA_REPLICACAO_AUD_D1_RESUMO", tmp_path)
    monkeypatch.setattr(
        d1,
        "caminho_estado_execucao",
        lambda rid: tmp_path / f"execucao_d1_{rid}.json",
    )

    plano = d1.carregar_plano_por_run_id(
        run_id,
        settings={"apenas_pendentes": False, "fonte_banco_ativa": False},
    )
    assert plano.workflow_fila["WF CASE"] == REPLICACAO_FILA_DOCUMENTOSCOPIA_31
    assert plano.csv_paths["WF CASE"].resolve() == csv_case.resolve()
    assert "WF CASE" in plano.workflows


def main():
    test_normalizar_fila()
    test_resolver_filtros_brflow_por_fila()
    test_agrupar_workflows_por_fila_ordem()
    print("OK: testes fila BRFlow passaram")


if __name__ == "__main__":
    main()
