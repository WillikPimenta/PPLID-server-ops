"""Testes integrados das melhorias de replicação (run_id, histórico, dashboard, retomada)."""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

import app.config as cfg
import app.bots.replicacao_aud_planning as rap
from app.config import COLUNA_CONFIG_CLIENTE


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        proto = base / "protocolos"
        resumo = base / "resumo"
        proto.mkdir()
        resumo.mkdir()

        orig_proto = cfg.PASTA_REPLICACAO_AUD_PROTOCOLOS
        orig_resumo = cfg.PASTA_REPLICACAO_AUD_RESUMO
        rap.PASTA_REPLICACAO_AUD_PROTOCOLOS = proto
        rap.PASTA_REPLICACAO_AUD_RESUMO = resumo

        d1 = datetime(2026, 5, 27, 14, 30, 52)
        rid1 = rap._resolver_run_id(d1, {})
        assert rid1 == "20260527_143052", rid1

        run_a = proto / "20260527_100000"
        run_a.mkdir()
        (run_a / "wf.csv").write_text("P001\nP002\n", encoding="utf-8")
        hist = rap.carregar_protocolos_historico(dias_historico=30)
        assert "P001" in hist and "P002" in hist

        dia = proto / "20260527"
        dia.mkdir()
        rid_legacy = rap._resolver_run_id(datetime(2026, 5, 27, 15, 0, 0), {"sobrescrever": True})
        assert rid_legacy == "20260527"

        run_id = "20260527_test01"
        pasta = proto / run_id
        pasta.mkdir()
        csv = pasta / "Workflow_A.csv"
        csv.write_text("P100\n", encoding="utf-8")
        estado = {
            "run_id": run_id,
            "pasta_protocolos": str(pasta),
            "workflows": {
                "Workflow A": {
                    "status": "UPLOAD_OK",
                    "csv": str(csv),
                    "atualizado_em": "2026-05-27T10:00:00",
                },
                "Workflow B": {
                    "status": "PENDENTE",
                    "csv": str(pasta / "Workflow_B.csv"),
                    "atualizado_em": "",
                },
            },
        }
        estado_path = resumo / f"execucao_{run_id}.json"
        estado_path.write_text(json.dumps(estado), encoding="utf-8")
        (pasta / "Workflow_B.csv").write_text("P200\n", encoding="utf-8")
        (pasta / "Workflow_C.csv").write_text("P300\n", encoding="utf-8")

        plano = rap.carregar_plano_por_run_id(run_id, {"apenas_pendentes": True})
        assert plano.run_id == run_id
        assert "Workflow B" in plano.workflows
        assert "Workflow A" not in plano.workflows

        estado["workflows"]["Workflow C"] = {
            "status": "SALVO_OK",
            "csv": str(pasta / "Workflow_C.csv"),
            "atualizado_em": "2026-05-27T11:00:00",
        }
        estado_path.write_text(json.dumps(estado), encoding="utf-8")
        plano_salvo = rap.carregar_plano_por_run_id(run_id, {"apenas_pendentes": True})
        assert "Workflow C" not in plano_salvo.workflows
        assert "Workflow B" in plano_salvo.workflows

        plano.resumo = [
            {
                "Workflow": "WF1",
                "Workflow D1": "WF1 D1",
                "Amostra Diaria": 10,
                "Amostra Solicitada": 10,
                "Amostra Redistribuida": 0,
                "Amostra Efetiva": 10,
                "Protocolos Salvos": 8,
                "Excluidos Historico": 0,
                "Disponivel D1": 100,
                "Horas Utilizadas": 3,
                "Faixa Horaria": "08-12",
                "Status": "OK",
                "Pct Atingido": 80.0,
                "Arquivo CSV": "WF1.csv",
                "Observacao": "",
                "RunId": run_id,
                "Parquet Referencia": "test.parquet",
                "Data Referencia D1": "26/05/2026",
                "Data Execucao": "27/05/2026 10:00",
                "Seed Amostra": 42,
                COLUNA_CONFIG_CLIENTE: "Cliente X",
            },
        ]
        plano.total_workflows_config = 1
        plano.pool_redistribuido = 0
        plano.excluidos_historico_total = 2
        plano.workflow_brflow = {"WF1": "WF1 BRFlow"}
        estado_rel = {
            "run_id": run_id,
            "iniciado_em": "2026-05-27T09:00:00",
            "parquet_referencia": "test.parquet",
            "data_referencia_d1": "26/05/2026",
            "data_execucao": "27/05/2026 10:00",
            "workflows": {
                "WF1": {
                    "status": "SALVO_OK",
                    "workflow_brflow": "WF1 BRFlow",
                    "atualizado_em": "2026-05-27T11:30:52",
                    "csv": str(csv),
                },
            },
        }
        relatorios = resumo / "relatorios"
        relatorios.mkdir()
        df_plano = pd.DataFrame([{"Protocolo": "P100", "WorkflowConfig": "WF1"}])
        xlsx = rap.exportar_relatorio_excel(
            plano,
            Path("test.parquet"),
            datetime(2026, 5, 26),
            estado=estado_rel,
            df_plano=df_plano,
            pasta_saida=relatorios,
        )
        assert xlsx.parent == relatorios
        assert xlsx.exists()
        assert run_id in xlsx.name
        with pd.ExcelFile(xlsx, engine="openpyxl") as xl:
            assert xl.sheet_names[0] == "Visão Geral"
            assert set(xl.sheet_names) == {"Visão Geral", "Plano", "Resumo", "Dashboard"}
            df_res = pd.read_excel(xl, sheet_name="Resumo")
            cols = list(df_res.columns)
            assert cols.index("Workflow") < cols.index("Workflow BRFlow")
            assert cols.index("Workflow BRFlow") < cols.index("Status BRFlow")
            row = df_res[df_res["Workflow"] == "WF1"].iloc[0]
            assert row["Status BRFlow"] == "SALVO_OK"
            assert row["Workflow BRFlow"] == "WF1 BRFlow"
            assert "27/05/2026 11:30:52" in str(row["Data Hora Upload BRFlow"])
            df_dash = pd.read_excel(xl, sheet_name="Dashboard")
            assert "Secao" in df_dash.columns
            brflow_rows = df_dash[df_dash["Metrica"] == "WorkflowsSalvosBRFlow"]
            assert not brflow_rows.empty
            assert int(brflow_rows.iloc[0]["Valor"]) == 1

        rap.atualizar_relatorio_excel(plano, estado_rel)
        assert plano.relatorio_excel_path == xlsx

        import gc
        gc.collect()

        # Upload diário: só workflows com protocolos geram CSV
        plano2 = rap.PlanoReplicacao(
            datetime.now(), pasta, resumo, run_id="upload_test"
        )
        plano2.workflows = ["WF Com Proto", "WF Vazio", "WF Sem D1"]
        plano2.workflows_sem_registro = ["WF Sem D1"]
        plano2.workflow_brflow = {
            "WF Com Proto": "WF Com Proto",
            "WF Vazio": "WF Vazio",
            "WF Sem D1": "WF Sem D1",
        }
        plano2.protocolos_por_workflow = {
            "WF Com Proto": ["P1", "P2"],
            "WF Vazio": [],
            "WF Sem D1": [],
        }
        pasta2 = proto / "upload_test"
        pasta2.mkdir()
        paths = rap.exportar_protocolos_csv(plano2.protocolos_por_workflow, pasta2)
        assert len(paths) == 1
        assert "WF Vazio" not in paths
        assert "WF Sem D1" not in paths
        assert rap._ler_protocolos_de_csv(paths["WF Com Proto"]) == {"P1", "P2"}
        assert rap.csv_protocolos_e_limpeza(paths["WF Com Proto"]) is False
        plano2.csv_paths = paths
        plano2.pasta_protocolos = pasta2
        estado2 = rap.inicializar_estado_execucao(plano2)
        assert estado2["workflows"]["WF Com Proto"]["status"] == "PENDENTE"
        assert estado2["workflows"]["WF Vazio"]["status"] == "PULADO"
        assert estado2["workflows"]["WF Vazio"].get("motivo") == "sem protocolos"
        assert estado2["workflows"]["WF Sem D1"]["status"] == "PULADO"
        assert estado2["workflows"]["WF Sem D1"].get("motivo") == "sem D-1"

        from app.bots import bot_replicacao_aud as bot

        assert bot._upload_csv_vazio_habilitado({}) is False

        assert rap._as_int(3) == 3
        assert rap._as_int(float("nan")) == 0
        assert rap._as_int(None) == 0
        meta = rap._meta_colunas_config(
            pd.Series({"amostra_total": float("nan"), "amostra_conf_prod": float("nan")}),
            pasta_volumetria="",
        )
        assert meta["Amostra Total Config"] == 0
        assert meta["Amostra Conf Prod Config"] == 0

        rap.PASTA_REPLICACAO_AUD_PROTOCOLOS = orig_proto
        rap.PASTA_REPLICACAO_AUD_RESUMO = orig_resumo

    print("OK: todos os testes integrados passaram")


if __name__ == "__main__":
    main()
