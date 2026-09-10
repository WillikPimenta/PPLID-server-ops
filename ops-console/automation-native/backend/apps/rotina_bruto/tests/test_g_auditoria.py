# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from django.test import TestCase, override_settings

from apps.rotina_bruto.models import RotinaBrutoSyncLog, RotinaGAuditoriaRecord
from apps.rotina_bruto.services.g_auditoria_sync import (
    build_staging_records,
    sync_g_auditoria_to_db,
)
from apps.rotina_bruto.services.source_path import SourceFileInfo, parse_file_meta
from apps.rotina_bruto.services.sync_runner import run_sync_with_audit


def _row(**updates):
    payload = {
        "Chave Registro Origem": "origem-1",
        "Id_transacao_origem": "tx-1",
        "ID_TRANSACAO_ORIGEM": "code-1",
        "TMPANALISE": "60",
        "Protocolo Origem": "000123",
        "Protocolo Destino": "aud-1",
        "Cliente Origem": "Cliente A",
        "Workflow Origem": "Workflow A",
        "Nivel Hierarquico Origem": "N1",
        "Usuario Origem": "agente",
        "Numero do CPF": "",
        "N. do Contrato/Proposta": "",
        "Data de Cadastro Origem": "01/08/2026 09:00:00",
        "Status do Registro Origem": "Concluído",
        "Tipo de Conclusao de Analise Origem": "Manual",
        "NOMOPERADOR": "Agente",
        "Cliente Destino": "Auditoria",
        "Workflow Destino": "G Auditoria",
        "Data de Cadastro Destino": "02/08/2026 10:00:00",
        "Alertas Destino": "",
        "MATRICULA": "C100A",
        "Matricula Destino": "-",
        "ETAPA": "Análise Visual",
        "Data de Conclusao Origem": "01/08/2026 10:00:00",
        "Data de Conclusao Destino": "02/08/2026 11:00:00",
        "Resultado Origem": "OK",
        "Resultado Destino": "Risco alto",
        "Status do Registro Destino": "Concluído",
        "Tipo de Conclusao de Analise Destino": "Manual",
    }
    payload.update(updates)
    return payload


def _source(path: Path, report_date: date = date(2026, 8, 2)):
    return SourceFileInfo(
        path=path,
        report_type=RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
        report_date=report_date,
        mtime=path.stat().st_mtime if path.exists() else 0,
        size=path.stat().st_size if path.exists() else 0,
    )


class GAuditoriaParserTests(unittest.TestCase):
    def test_nome_do_arquivo(self):
        self.assertEqual(
            parse_file_meta(
                RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
                "brflow-gauditoria_tratado_20260802.parquet",
            ),
            (date(2026, 8, 2), None),
        )
        self.assertIsNone(
            parse_file_meta(
                RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
                "gauditoria_20260802.parquet",
            )
        )

    def test_mapeamento_datas_prazo_e_sem_cpf(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-gauditoria_tratado_20260802.parquet"
            df = pd.DataFrame(
                [
                    _row(**{"Numero do CPF": "12345678900"}),
                    _row(
                        **{
                            "Chave Registro Origem": "origem-2",
                            "Id_transacao_origem": "tx-2",
                            "ID_TRANSACAO_ORIGEM": "code-2",
                            "Protocolo Origem": "124",
                            "Data de Conclusao Origem": "01/04/2026",
                            "Data de Conclusao Destino": "30/07/2026",
                        }
                    ),
                ]
            )
            path.write_bytes(b"x")
            records, metrics = build_staging_records(df, _source(path))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].data_analise, date(2026, 8, 1))
        self.assertEqual(records[0].data_auditoria, date(2026, 8, 2))
        self.assertEqual(records[0].protocolo_origem, "000123")
        self.assertEqual(records[0].matricula_agente, "c100a")
        self.assertEqual(records[0].matricula_auditor, "")
        self.assertEqual(records[1].dias_prazo, 120)
        self.assertEqual(records[1].prazo_status, RotinaGAuditoriaRecord.PRAZO_FORA)
        self.assertNotIn("cpf", {field.name for field in RotinaGAuditoriaRecord._meta.fields})
        self.assertEqual(metrics["rows_read"], 2)
        self.assertEqual(metrics["rows_valid"], 2)

    def test_duplicata_canonica_conta_uma_vez(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-gauditoria_tratado_20260802.parquet"
            path.write_bytes(b"x")
            records, metrics = build_staging_records(
                pd.DataFrame([_row(), _row()]), _source(path)
            )
        self.assertEqual(len(records), 1)
        self.assertEqual(metrics["rows_valid"], 1)
        self.assertEqual(metrics["rows_duplicate"], 1)

    def test_coluna_obrigatoria_ausente_falha(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-gauditoria_tratado_20260802.parquet"
            path.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "Workflow Destino"):
                build_staging_records(
                    pd.DataFrame([_row()]).drop(columns=["Workflow Destino"]),
                    _source(path),
                )


@override_settings(QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=False)
class GAuditoriaSyncTests(TestCase):
    def test_staging_idempotente_e_sem_falhas(self):
        from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-gauditoria_tratado_20260802.parquet"
            pd.DataFrame([_row()]).to_parquet(path, index=False)
            source = _source(path)
            first = sync_g_auditoria_to_db(source)
            second = sync_g_auditoria_to_db(source)

        self.assertEqual(first.row_count, 1)
        self.assertEqual(second.row_count, 1)
        self.assertEqual(RotinaGAuditoriaRecord.objects.count(), 1)
        self.assertEqual(QualidadeAuditado.objects.count(), 0)
        self.assertEqual(QualidadeFalha.objects.count(), 0)
        self.assertFalse(first.metrics["projection_enabled"])

    def test_runner_persiste_metricas_hash_e_versao(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brflow-gauditoria_tratado_20260802.parquet"
            pd.DataFrame([_row()]).to_parquet(path, index=False)
            success, log, skipped = run_sync_with_audit(
                RotinaBrutoSyncLog.REPORT_G_AUDITORIA,
                path=str(path),
                force=True,
            )
        self.assertTrue(success)
        self.assertFalse(skipped)
        self.assertEqual(log.row_count, 1)
        self.assertEqual(log.metrics["rows_read"], 1)
        self.assertEqual(log.mapping_version, 1)
        self.assertEqual(len(log.source_sha256), 64)
