# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path

import pandas as pd
from django.test import TestCase, override_settings

from apps.replicacao_d1.models import ReplicacaoD1Protocolo, ReplicacaoD1Replicado
from apps.replicacao_d1.normalization import STATUS_REPLICADO
from apps.replicacao_d1.services.excel_reader import ParsedReport, ParsedProtocolo, ParsedWorkflow
from apps.replicacao_d1.services.shadow_compare import (
    compare_operational_status,
    compare_parsed_report_to_db,
    log_shadow_comparison,
)
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db


def _write_fixture_excel(path: Path, *, run_id: str = "20260810_120000") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df_resumo = pd.DataFrame(
        [
            {
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                "Workflow BRFlow": "G Auditoria Origem",
                "Canal Destino": "BRFlow",
                "Cliente": "CLARO",
                "Segmento": "SEG",
                "Categoria": "CAT",
                "Fila": "G auditoria",
                "Amostra Diaria": 10,
                "Amostra Solicitada": 10,
                "Amostra Efetiva": 2,
                "Protocolos Salvos": 2,
                "Pct Atingido": 100.0,
                "Status": "OK",
                "Status BRFlow": "SALVO_OK",
                "Data Hora Upload BRFlow": "10/08/2026 12:05",
                "Disponivel D1": 50,
                "Faixa Horaria": "08-12",
                "RunId": run_id,
                "Data Referencia D1": "09/08/2026",
                "Data Execucao": "10/08/2026 12:00",
                "Parquet Referencia": "brflow-detalhado-tratado_20260809.parquet",
                "Auditores Ativos": 12,
            },
        ]
    )
    df_plano = pd.DataFrame(
        [
            {
                "Protocolo": "P001",
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                "Data Analise": "09/08/2026 10:00",
                "Hora": 10,
                "Canal Destino": "BRFlow",
                "Status BRFlow": "SALVO_OK",
                "RunId": run_id,
                "Data Referencia D1": "09/08/2026",
            },
        ]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_resumo.to_excel(writer, sheet_name="Resumo", index=False)
        df_plano.to_excel(writer, sheet_name="Plano", index=False)
        pd.DataFrame([{"Metrica": "Total", "Valor": 1}]).to_excel(
            writer, sheet_name="Dashboard", index=False
        )
    return path


class ShadowCompareTests(TestCase):
    @override_settings(REPLICACAO_D1_FF_SHADOW_MODE=True)
    def test_compare_empty_run_matches_zero_db(self):
        parsed = ParsedReport(
            run_id="20260810_120000",
            data_referencia_d1=date(2026, 8, 9),
            protocolos=[],
            workflows=[],
        )
        result = compare_parsed_report_to_db(parsed)
        self.assertTrue(result["match"])
        self.assertEqual(result["protocolos_db"], 0)
        self.assertEqual(result["workflows_db"], 0)

    @override_settings(REPLICACAO_D1_FF_SHADOW_MODE=True, REPLICACAO_D1_SOURCE_DIR="")
    def test_operational_status_after_sync(self):
        from django.conf import settings

        run_id = "20260810_120000"
        excel = _write_fixture_excel(
            Path(settings.BASE_DIR)
            / "tmp"
            / "shadow_compare_test"
            / f"replicacao_aud_d1_relatorio_{run_id}.xlsx",
            run_id=run_id,
        )
        sync_replicacao_d1_to_db(path=str(excel), force=True)
        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 8, 10),
            protocolo_origem="P001",
            protocolo_origem_normalizado="p001",
            workflow_origem="WF Origem",
        )
        ReplicacaoD1Protocolo.objects.filter(run_id=run_id).update(status_operacional=STATUS_REPLICADO)

        parsed = ParsedReport(
            run_id=run_id,
            data_referencia_d1=date(2026, 8, 9),
            protocolos=[
                ParsedProtocolo(
                    protocolo="P001",
                    workflow_config="WF G",
                    status_brflow="SALVO_OK",
                ),
            ],
            workflows=[ParsedWorkflow(workflow_config="WF G", status_brflow="SALVO_OK")],
        )
        op = compare_operational_status(parsed)
        self.assertEqual(op["excel_upload_ok"], 1)
        self.assertEqual(op["db_upload_ok"], 1)
        self.assertEqual(op["db_confirmed"], 1)
        self.assertTrue(op["match"])

        full = compare_parsed_report_to_db(parsed)
        self.assertTrue(full["match"])
        logged = log_shadow_comparison(parsed)
        self.assertIn("operational", logged)
