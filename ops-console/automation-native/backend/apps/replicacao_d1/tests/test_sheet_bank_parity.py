# -*- coding: utf-8 -*-
"""Paridade planilha × banco: mesmos DataFrames a partir de dados equivalentes."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook

from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.normalization import normalize_key
from apps.replicacao_d1.services.config_snapshot import build_execution_snapshot
from apps.replicacao_d1.services.planning_adapter import (
    COL_CATEGORIA,
    COL_CLIENTE,
    COL_ESCALA_BR,
    COL_ESCALA_CASE,
    COL_ESCALA_DATA,
    COL_FILA,
    COL_META_CLIENTE,
    COL_SEGMENTO,
    COL_WORKFLOW,
    COL_WORKFLOW_D1,
    COL_WORKFLOW_SEL,
    snapshot_to_dataframes,
)


def _write_default_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Workflow d1"
    ws.append(["Workflow", "Cliente", "Workflow d-1", "Workflow - selenium", "Status", "Fila"])
    ws.append(["WF Tela", "Cliente Alpha", "WF Parquet", "WF Selenium", True, "G auditoria"])
    ws.append(["WF Off", "Cliente Alpha", "WF Off D1", "WF Off Sel", False, "G auditoria"])
    wb.save(path)


def _write_categoria_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append(["Cliente", "Segmento", "Categoria", "Meta Cliente"])
    ws.append(["Cliente Alpha", "Seg A", "High", 1500])
    wb.save(path)


def _write_escala_csv(path: Path) -> None:
    path.write_text(
        "data;auditores_ativos;auditores_ativos_case\n"
        "2026-08-06;10;3\n",
        encoding="utf-8-sig",
    )


class SheetVsBankParityTests(TestCase):
    """Compara leitores legados (se importáveis) com DataFrames do snapshot."""

    def setUp(self):
        self.tmp = Path(self._tmp_dir())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.default = self.tmp / "Default.xlsx"
        self.categoria = self.tmp / "Categoria.xlsx"
        self.escala = self.tmp / "escala_auditores.csv"
        _write_default_xlsx(self.default)
        _write_categoria_xlsx(self.categoria)
        _write_escala_csv(self.escala)

        geral = ReplicacaoD1ConfigGeral.get_solo()
        geral.fonte_banco_ativa = True
        geral.calculadora_params = {
            "dias_uteis": 24.0,
            "meta_produ": 300.0,
            "confianca": 0.99,
            "margin_high": 0.0115,
            "margin_low": 0.0178,
        }
        geral.save()

        cli = ReplicacaoD1Cliente.objects.create(
            nome="Cliente Alpha",
            segmento_nome="Seg A",
            categoria_nome="High",
            meta_mensal=1500,
        )
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Tela",
            nome_d1="WF Parquet",
            nome_selenium="WF Selenium",
            fila="G auditoria",
            cliente=cli,
            status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        # Inativo no Excel (Status=False) — não deve aparecer no mapa ativo do banco
        ReplicacaoD1Workflow.objects.create(
            nome_canonico="WF Off",
            nome_d1="WF Off D1",
            nome_selenium="WF Off Sel",
            fila="G auditoria",
            cliente=cli,
            status=ReplicacaoD1Workflow.STATUS_INATIVO,
            ativo=False,
        )
        ReplicacaoD1EscalaDia.objects.create(
            data=date(2026, 8, 6),
            auditores_brflow=10,
            auditores_case=3,
        )

    def _tmp_dir(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="d1_parity_")

    def test_workflows_clientes_filas_metas_escala_parity(self):
        snap = build_execution_snapshot(persist=False)
        bank = snapshot_to_dataframes(snap)

        # --- workflows ativos ---
        mapa_bank = bank["mapa_workflow_d1"].copy()
        mapa_bank = mapa_bank.sort_values("_wf_key").reset_index(drop=True)
        self.assertEqual(len(mapa_bank), 1)
        self.assertEqual(mapa_bank.iloc[0][COL_WORKFLOW], "WF Tela")
        self.assertEqual(mapa_bank.iloc[0][COL_WORKFLOW_D1], "WF Parquet")
        self.assertEqual(mapa_bank.iloc[0][COL_FILA], "G auditoria")
        self.assertEqual(mapa_bank.iloc[0]["_wf_key"], normalize_key("WF Tela"))

        # --- clientes / metas ---
        cat_bank = bank["categoria_clientes"].copy()
        self.assertEqual(len(cat_bank), 1)
        self.assertEqual(cat_bank.iloc[0][COL_CLIENTE], "Cliente Alpha")
        self.assertEqual(cat_bank.iloc[0][COL_SEGMENTO], "Seg A")
        self.assertEqual(cat_bank.iloc[0][COL_CATEGORIA], "High")
        self.assertEqual(int(cat_bank.iloc[0][COL_META_CLIENTE]), 1500)

        # --- escala ---
        esc_bank = bank["escala_auditores"].copy()
        self.assertEqual(len(esc_bank), 1)
        self.assertEqual(int(esc_bank.iloc[0][COL_ESCALA_BR]), 10)
        self.assertEqual(int(esc_bank.iloc[0][COL_ESCALA_CASE]), 3)

        # Compare with automacoes Excel readers when package is importable
        try:
            from app.bots import replicacao_aud_planning as rap
        except ImportError:
            self.skipTest("Pacote automacoes não importável neste ambiente")

        mapa_xlsx = rap.carregar_mapa_workflow_d1(self.default)
        cat_xlsx = rap.carregar_categoria_clientes(self.categoria)
        escala_xlsx = pd.read_csv(self.escala, sep=";")

        self.assertEqual(
            set(mapa_xlsx["_wf_key"].astype(str)),
            set(mapa_bank["_wf_key"].astype(str)),
        )
        self.assertEqual(
            set(cat_xlsx["_cli_key"].astype(str)),
            set(cat_bank["_cli_key"].astype(str)),
        )
        self.assertEqual(
            int(cat_xlsx.iloc[0][COL_META_CLIENTE]),
            int(cat_bank.iloc[0][COL_META_CLIENTE]),
        )
        self.assertEqual(
            int(escala_xlsx.iloc[0]["auditores_ativos"]),
            int(esc_bank.iloc[0][COL_ESCALA_BR]),
        )
        self.assertEqual(
            int(escala_xlsx.iloc[0]["auditores_ativos_case"]),
            int(esc_bank.iloc[0][COL_ESCALA_CASE]),
        )

    def test_totais_amostra_formula_parity(self):
        """Mesma fórmula de amostra total para o mesmo volume e categoria."""
        try:
            from app.bots.replicacao_aud_planning import _calcular_amostra_total_planilha
        except ImportError:
            self.skipTest("Pacote automacoes não importável neste ambiente")

        snap = build_execution_snapshot(persist=False)
        meta = dict(snap.persistent.get("calculadora_params") or {})
        if not meta.get("meta_produ"):
            meta = {
                "dias_uteis": 24.0,
                "meta_produ": 300.0,
                "confianca": 0.99,
                "margin_high": 0.0115,
                "margin_low": 0.0178,
            }
        for total_vol, categoria in [(1000, "High"), (500, "Mid"), (0, "High")]:
            a = _calcular_amostra_total_planilha(float(total_vol), meta, categoria)
            b = _calcular_amostra_total_planilha(float(total_vol), meta, categoria)
            self.assertEqual(a, b)


class NormalizeKeyParitySimpleTests(SimpleTestCase):
    def test_normalize_matches_common_variants(self):
        self.assertEqual(normalize_key("  Café  "), normalize_key("cafe"))
        self.assertEqual(normalize_key("A–B"), normalize_key("a-b"))
