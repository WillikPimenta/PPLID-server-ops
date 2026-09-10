# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Reconciliacao,
    ReplicacaoD1Replicado,
    ReplicacaoD1Run,
)
from apps.replicacao_d1.normalization import STATUS_REPLICADO, STATUS_PENDENTE, STATUS_FALHOU
from apps.replicacao_d1.services.reconciliation import reconcile_protocolos_for_run
from apps.replicacao_d1.services.reconciliation import _bulk_update_protocols
from apps.replicacao_d1.services.reconciliation import reconcile_protocolos_for_dates
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db
from apps.replicacao_d1.services.sync_replicados import sync_replicados_to_db


User = get_user_model()


def _write_fixture_excel(path: Path, *, run_id: str = "20260717_093000") -> Path:
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
                "Data Hora Upload BRFlow": "17/07/2026 12:05",
                "Disponivel D1": 50,
                "Faixa Horaria": "08-12",
                "RunId": run_id,
                "Data Referencia D1": "16/07/2026",
                "Data Execucao": "17/07/2026 12:00",
                "Parquet Referencia": "brflow-detalhado-tratado_20260716.parquet",
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
                "Data Analise": "16/07/2026 10:00",
                "Hora": 10,
                "Canal Destino": "BRFlow",
                "Status BRFlow": "SALVO_OK",
                "RunId": run_id,
                "Data Referencia D1": "16/07/2026",
            },
            {
                "Protocolo": "P002",
                "Workflow": "WF G",
                "Workflow D1": "wf_g_d1",
                "Data Analise": "16/07/2026 11:00",
                "Hora": 11,
                "Canal Destino": "BRFlow",
                "Status BRFlow": "ERRO",
                "RunId": run_id,
                "Data Referencia D1": "16/07/2026",
            },
        ]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_resumo.to_excel(writer, sheet_name="Resumo", index=False)
        df_plano.to_excel(writer, sheet_name="Plano", index=False)
        pd.DataFrame([{"Metrica": "Total", "Valor": 2}]).to_excel(
            writer, sheet_name="Dashboard", index=False
        )
    return path


def _write_replicados_csv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Cliente Destino;Data de Cadastro Destino;Protocolo Destino;Workflow Destino;"
        "Protocolo Origem;Cliente Origem;Workflow Origem;Nível Hierárquico Origem;"
        "Data de Cadastro Origem;Tipo de Conclusão de Análise Origem\n"
        "GAQ;18/07/2026 10:00;D001;G Auditoria Destino;P001;CLARO;WF Origem;1;"
        "16/07/2026 09:00;OK\n",
        encoding="utf-8-sig",
    )
    return path


class ReconciliationTests(TestCase):
    def setUp(self):
        from django.conf import settings

        self.dir = Path(settings.BASE_DIR) / "tmp" / "replicacao_d1_reconcile_test"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.excel = _write_fixture_excel(self.dir / "replicacao_aud_d1_relatorio_20260717_093000.xlsx")
        self.csv = _write_replicados_csv(self.dir / "brflow-replicadosd1-tratado_20260718.csv")

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_reconcile_plano_and_replicados(self):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)

        p1 = ReplicacaoD1Protocolo.objects.get(protocolo="P001")
        p2 = ReplicacaoD1Protocolo.objects.get(protocolo="P002")
        self.assertEqual(p1.status_operacional, STATUS_REPLICADO)
        self.assertEqual(p2.status_operacional, STATUS_FALHOU)
        self.assertEqual(p1.protocolo_normalizado, "p001")

    def test_reconcile_idempotent(self):
        run = ReplicacaoD1Run.objects.create(
            run_id="test_run",
            data_referencia_d1=date(2026, 7, 16),
            data_execucao=timezone.make_aware(datetime(2026, 7, 16, 12, 0)),
        )
        ReplicacaoD1Protocolo.objects.create(
            run=run,
            data_referencia_d1=date(2026, 7, 16),
            protocolo="00099",
            protocolo_normalizado="99",
            workflow_config="WF",
            status_brflow="SALVO_OK",
            status_operacional=STATUS_PENDENTE,
        )
        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 7, 17),
            protocolo_origem="99",
            protocolo_origem_normalizado="99",
            workflow_origem="WF",
        )
        updated = reconcile_protocolos_for_run("test_run")
        self.assertGreaterEqual(updated, 1)
        prot = ReplicacaoD1Protocolo.objects.get(protocolo="00099")
        self.assertEqual(prot.status_operacional, STATUS_REPLICADO)

    def test_bulk_update_protocols_persists_reconciliation_fields(self):
        run = ReplicacaoD1Run.objects.create(
            run_id="bulk_update_run",
            data_referencia_d1=date(2026, 7, 16),
        )
        prot = ReplicacaoD1Protocolo.objects.create(
            run=run,
            data_referencia_d1=date(2026, 7, 16),
            protocolo="00077",
            workflow_config="WF",
            status_operacional=STATUS_PENDENTE,
        )
        prot.protocolo_normalizado = "77"
        prot.status_operacional = STATUS_REPLICADO
        prot.erro_resumido = ""

        self.assertEqual(_bulk_update_protocols([prot]), 1)
        prot.refresh_from_db()
        self.assertEqual(prot.protocolo_normalizado, "77")
        self.assertEqual(prot.status_operacional, STATUS_REPLICADO)

    @patch("apps.replicacao_d1.services.reconciliation.reconcile_protocolos_for_run")
    def test_reconcile_dates_calls_each_run_only_once(self, mock_reconcile):
        mock_reconcile.return_value = 0
        run = ReplicacaoD1Run.objects.create(
            run_id="distinct_run",
            data_referencia_d1=date(2026, 7, 16),
            data_execucao=timezone.make_aware(datetime(2026, 7, 15, 12, 0)),
        )
        for protocolo in ("P001", "P002", "P003"):
            ReplicacaoD1Protocolo.objects.create(
                run=run,
                data_referencia_d1=date(2026, 7, 16),
                protocolo=protocolo,
                workflow_config=f"WF-{protocolo}",
            )

        reconcile_protocolos_for_dates({date(2026, 7, 16)})

        mock_reconcile.assert_called_once_with("distinct_run", progress=None)

    def test_reconcile_ambiguous_multiple_candidates(self):
        run = ReplicacaoD1Run.objects.create(
            run_id="ambig_run",
            data_referencia_d1=date(2026, 7, 16),
            data_execucao=timezone.make_aware(datetime(2026, 7, 15, 12, 0)),
        )
        prot = ReplicacaoD1Protocolo.objects.create(
            run=run,
            data_referencia_d1=date(2026, 7, 16),
            protocolo="777",
            protocolo_normalizado="777",
            workflow_config="WF",
            status_brflow="SALVO_OK",
            status_operacional=STATUS_PENDENTE,
        )
        for i in range(2):
            ReplicacaoD1Replicado.objects.create(
                report_date=date(2026, 7, 16),
                protocolo_origem="777",
                protocolo_origem_normalizado="777",
                workflow_origem=f"WF{i}",
            )
        reconcile_protocolos_for_run("ambig_run")
        prot.refresh_from_db()
        self.assertEqual(prot.status_operacional, STATUS_PENDENTE)
        rec = ReplicacaoD1Reconciliacao.objects.get(protocolo=prot)
        self.assertEqual(rec.status, ReplicacaoD1Reconciliacao.STATUS_AMBIGUOUS)

    def test_reconcile_idempotent_twice(self):
        run = ReplicacaoD1Run.objects.create(
            run_id="idempotent_run",
            data_referencia_d1=date(2026, 7, 16),
            data_execucao=timezone.make_aware(datetime(2026, 7, 15, 12, 0)),
        )
        ReplicacaoD1Protocolo.objects.create(
            run=run,
            data_referencia_d1=date(2026, 7, 16),
            protocolo="00042",
            protocolo_normalizado="42",
            workflow_config="WF",
            status_brflow="SALVO_OK",
            status_operacional=STATUS_PENDENTE,
        )
        ReplicacaoD1Replicado.objects.create(
            report_date=date(2026, 7, 16),
            protocolo_origem="42",
            protocolo_origem_normalizado="42",
            workflow_origem="WF",
        )
        first = reconcile_protocolos_for_run("idempotent_run")
        second = reconcile_protocolos_for_run("idempotent_run")
        prot = ReplicacaoD1Protocolo.objects.get(protocolo="00042")
        self.assertEqual(prot.status_operacional, STATUS_REPLICADO)
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(
            ReplicacaoD1Reconciliacao.objects.filter(protocolo=prot).count(),
            1,
        )

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_reconcile_matched_execution_d_plus_one(self):
        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)
        p1 = ReplicacaoD1Protocolo.objects.get(protocolo="P001")
        self.assertEqual(p1.status_operacional, STATUS_REPLICADO)
        rec = ReplicacaoD1Reconciliacao.objects.get(protocolo=p1)
        self.assertEqual(rec.status, ReplicacaoD1Reconciliacao.STATUS_MATCHED)

    @override_settings(REPLICACAO_D1_SOURCE_DIR="")
    def test_dashboard_uses_execution_d_plus_one_independently(self):
        from apps.replicacao_d1.services.dashboard import build_replicacao_d1_dashboard, parse_dashboard_params

        sync_replicacao_d1_to_db(path=str(self.excel), force=True)
        sync_replicados_to_db(path=str(self.csv), force=True)

        params = parse_dashboard_params(
            {"data_de": "2026-07-17", "data_ate": "2026-07-17"},
        )
        dash = build_replicacao_d1_dashboard(params)
        prot_qs = ReplicacaoD1Protocolo.objects.filter(
            data_referencia_d1=date(2026, 7, 16),
        )
        self.assertEqual(dash["indicators"]["planejados"], prot_qs.count())
        self.assertEqual(dash["indicators"]["replicados"], 1)
        self.assertEqual(dash["replication_summary"]["runs_sem_confirmacao"], 0)
        self.assertEqual(
            dash["indicators"]["falhos"],
            prot_qs.filter(status_operacional=STATUS_FALHOU).count(),
        )
        self.assertEqual(dash["protocolos"]["count"], prot_qs.count())
