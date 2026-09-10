# -*- coding: utf-8 -*-
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from apps.dimensoes_processos.models import (
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DimCliente,
    DimEtapa,
    DimNomeAlias,
    DimWorkflow,
)
from apps.dimensoes_processos.services.derivacao_etapa.lookup import MegazordLookup
from apps.dimensoes_processos.services.derivacao_etapa.normalize import fix_csv_dashes
from apps.dimensoes_processos.services.derivacao_etapa.normalize import normalize_csv_etapa_key
from apps.dimensoes_processos.services.derivacao_etapa.reader import read_derivacao_csv
from apps.dimensoes_processos.services.derivacao_etapa.scan_comparativo import (
    build_comparativo_resumo,
    scan_derivacao_etapa_comparativo,
)
from apps.dimensoes_processos.services.derivacao_etapa.sync import (
    PreflightMismatchError,
    UnsafePartialImportError,
    _consolidate_diaria_rows,
    import_derivacao_etapa_csv,
)


class DerivacaoEtapaLookupTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=100, nome="Bradesco – Cartões")
        DimWorkflow.objects.create(id_workflow=200, nome="Workflow Megazord")
        DimEtapa.objects.create(id_etapa=300, nome="Gerenciador de Entrada - Cliente")

    def test_fix_csv_dash_byte(self):
        self.assertIn("-", fix_csv_dashes("Bradesco \x96 Cartões"))

    def test_resolve_cliente_with_dash_fix(self):
        lookup = MegazordLookup.build()
        res = lookup.resolve_cliente("Bradesco \x96 Cartões")
        self.assertTrue(res.ok)
        self.assertEqual(res.id, 100)

    def test_resolve_workflow_suffix(self):
        lookup = MegazordLookup.build()
        res = lookup.resolve_workflow("DE07 - Workflow Megazord")
        self.assertTrue(res.ok)
        self.assertEqual(res.id, 200)
        self.assertEqual(res.match_strategy, "suffix")

    def test_resolve_etapa_via_alias(self):
        origem = "Ger. Entrada"
        DimNomeAlias.objects.create(
            dimensao=DimNomeAlias.DIM_ETAPA,
            nome_origem=origem,
            nome_origem_key=normalize_csv_etapa_key(origem),
            etapa_id=300,
        )
        lookup = MegazordLookup.build()
        res = lookup.resolve_etapa(origem, workflow_id=200)
        self.assertTrue(res.ok)
        self.assertEqual(res.id, 300)
        self.assertEqual(res.match_strategy, "alias")


class DerivacaoEtapaImportTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=100, nome="Cliente Teste")
        DimWorkflow.objects.create(id_workflow=200, nome="Workflow Teste")
        DimEtapa.objects.create(id_etapa=300, nome="Etapa Teste")

        self.tmp = tempfile.TemporaryDirectory()
        self.csv_dir = Path(self.tmp.name)
        self.csv_path = self.csv_dir / "FINALIZADO_20260501.csv"
        self.csv_path.write_text(
            "\n".join(
                [
                    "Data;Cliente;WF;Etapas;Registros;Percentual",
                    "01/05/2026;Cliente Teste;Workflow Teste;Etapa Teste;42;100,00",
                    "01/05/2026;Cliente Teste;Workflow Teste;Total;42;100",
                    "01/05/2026;Cliente Teste;Workflow Teste;Etapa Desconhecida;1;50,00",
                ]
            ),
            encoding="latin-1",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_reader_skips_total_rows(self):
        result = read_derivacao_csv(self.csv_path)
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.skipped_total, 1)

    def test_import_with_unknown_row_is_blocked_before_partition_reload(self):
        DerivacaoEtapaDiaria.objects.create(
            data="2026-05-01",
            cliente_id=100,
            workflow_id=200,
            etapa_id=300,
            registros=999,
            percentual=100,
            source_file="carga-anterior.csv",
        )

        with self.assertRaises(UnsafePartialImportError):
            import_derivacao_etapa_csv(directory=self.csv_dir)

        persisted = DerivacaoEtapaDiaria.objects.get()
        self.assertEqual(persisted.registros, 999)
        self.assertEqual(persisted.source_file, "carga-anterior.csv")

    def test_dry_run_does_not_persist(self):
        run, metrics = import_derivacao_etapa_csv(directory=self.csv_dir, dry_run=True)
        self.assertIsNone(run)
        self.assertEqual(DerivacaoEtapaDiaria.objects.count(), 0)
        self.assertEqual(metrics.rows_inserted, 1)

    def test_consolidate_diaria_rows_sums_colliding_etapa_aliases(self):
        from apps.dimensoes_processos.models import DerivacaoEtapaImportRun

        rows = [
            DerivacaoEtapaDiaria(
                data=date(2026, 5, 1),
                cliente_id=100,
                workflow_id=200,
                etapa_id=300,
                registros=10,
                percentual=Decimal("40.00"),
                etapa_nome_origem="OCR - A",
            ),
            DerivacaoEtapaDiaria(
                data=date(2026, 5, 1),
                cliente_id=100,
                workflow_id=200,
                etapa_id=300,
                registros=15,
                percentual=Decimal("60.00"),
                etapa_nome_origem="OCR - B",
            ),
        ]
        merged = _consolidate_diaria_rows(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].registros, 25)
        self.assertEqual(merged[0].percentual, Decimal("100.00"))
        self.assertIn("OCR - A", merged[0].etapa_nome_origem)
        self.assertIn("OCR - B", merged[0].etapa_nome_origem)

    def test_import_consolidates_alias_collisions(self):
        DimEtapa.objects.create(id_etapa=301, nome="OCR (automática)", manual=False)
        for origem in ("OCR - A", "OCR - B"):
            DimNomeAlias.objects.create(
                dimensao=DimNomeAlias.DIM_ETAPA,
                nome_origem=origem,
                nome_origem_key=normalize_csv_etapa_key(origem),
                etapa_id=301,
                classificacao=DimNomeAlias.CLASS_ETAPA_AUTOMATICA,
            )
        self.csv_path.write_text(
            "\n".join(
                [
                    "Data;Cliente;WF;Etapas;Registros;Percentual",
                    "01/05/2026;Cliente Teste;Workflow Teste;OCR - A;10;40,00",
                    "01/05/2026;Cliente Teste;Workflow Teste;OCR - B;15;60,00",
                ]
            ),
            encoding="latin-1",
        )
        run, metrics = import_derivacao_etapa_csv(directory=self.csv_dir)
        self.assertEqual(run.status, DerivacaoEtapaImportRun.STATUS_OK)
        self.assertEqual(metrics.rows_inserted, 1)
        row = DerivacaoEtapaDiaria.objects.get()
        self.assertEqual(row.etapa_id, 301)
        self.assertEqual(row.registros, 25)


class DerivacaoEtapaScanTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=100, nome="Cliente Teste")
        DimWorkflow.objects.create(id_workflow=200, nome="Workflow Teste")
        DimEtapa.objects.create(id_etapa=300, nome="Etapa Teste")

        self.tmp = tempfile.TemporaryDirectory()
        self.csv_dir = Path(self.tmp.name)
        (self.csv_dir / "FINALIZADO_20260501.csv").write_text(
            "Data;Cliente;WF;Etapas;Registros;Percentual\n"
            "01/05/2026;Cliente Teste;Workflow Teste;Etapa Teste;10;100,00\n"
            "01/05/2026;Cliente Teste;DE07 - Workflow Teste;Etapa Teste;5;100,00\n",
            encoding="latin-1",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_builds_comparativo_distinct(self):
        run, metrics = scan_derivacao_etapa_comparativo(directory=self.csv_dir)
        self.assertEqual(run.run_kind, DerivacaoEtapaImportRun.KIND_SCAN)
        self.assertEqual(metrics.rows_csv, 2)
        resumo = build_comparativo_resumo(scan_run_id=run.pk)
        self.assertEqual(resumo["scan_run_id"], run.pk)
        self.assertEqual(resumo["dimensoes"]["cliente"]["origens_distintas"], 1)
        self.assertEqual(resumo["dimensoes"]["workflow"]["origens_distintas"], 2)
        self.assertEqual(resumo["dimensoes"]["cliente"]["registros_total"], 15)
        self.assertEqual(resumo["dimensoes"]["cliente"]["registros_resolvidos"], 15)
        self.assertEqual(resumo["dimensoes"]["cliente"]["registros_pendentes"], 0)
        self.assertEqual(resumo["dimensoes"]["cliente"]["taxa_registros_ok_pct"], 100.0)


class DerivacaoEtapaSourceTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv_dir = Path(self.tmp.name)
        (self.csv_dir / "FINALIZADO_20260501.csv").write_text(
            "Data;Cliente;WF;Etapas;Registros;Percentual\n"
            "01/05/2026;Cliente;WF;Etapa;1;100,00\n",
            encoding="latin-1",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_derivacao_source_from_directory(self):
        from apps.dimensoes_processos.services.derivacao_etapa.source import resolve_derivacao_source

        files, batch_id = resolve_derivacao_source(
            directory=self.csv_dir,
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 1),
        )
        self.assertEqual(len(files), 1)
        self.assertIsNone(batch_id)
