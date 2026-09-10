# -*- coding: utf-8 -*-
import json
from datetime import date

from django.test import SimpleTestCase, TestCase, override_settings

from apps.dimensoes_processos.models import DimCliente
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha, QualidadeFiltroOpcao
from apps.qualidade_operacional.services.contestacao_metrics import (
    build_contestacao_metrics,
    clear_contestacao_tipo_cache,
)
from apps.qualidade_operacional.services.contestacao_derivation import (
    build_falha_lookup_indexes,
    dedupe_contestacao_cases,
    derive_contestacao_cases_indexed,
)
from apps.qualidade_operacional.services.criticidade import CLIENTE_GAQ_ID
from apps.qualidade_operacional.services.intranet_source import INTRANET_SOURCE_FILE
from report_brb.db_loaders import derive_contestacao_from_eo


class ContestacaoDeterminismTests(SimpleTestCase):
    def test_reinspecao_uses_projected_failure_not_procedencia_label(self):
        auditados = [
            {
                "id": 1,
                "protocolo": "REINS-SEM-FALHA",
                "matricula": "c90001a",
                "data": date(2026, 8, 10),
                "data_analise": date(2026, 8, 10),
                "procedencia": "Procedente",
                "origem_tratado": "reinspecao",
                "tipo_registro": "reinspecao",
            },
            {
                "id": 2,
                "protocolo": "REINS-COM-FALHA",
                "matricula": "c90002a",
                "data": date(2026, 8, 10),
                "data_analise": date(2026, 8, 10),
                "procedencia": "Improcedente",
                "origem_tratado": "reinspecao",
                "tipo_registro": "reinspecao",
            },
        ]
        falha = {
            "id": 10,
            "protocolo": "REINS-COM-FALHA",
            "matricula": "c90002a",
            "data_analise": date(2026, 8, 10),
            "tipo_falha": "Manual",
        }
        by_case, by_protocolo = build_falha_lookup_indexes([falha])

        cases = derive_contestacao_cases_indexed(
            auditados,
            by_case=by_case,
            by_protocolo=by_protocolo,
        )
        by_protocol = {row["protocolo"]: row for row in cases}

        self.assertEqual(
            by_protocol["REINS-SEM-FALHA"]["classificacao_conforme"],
            "nao_falha",
        )
        self.assertEqual(
            by_protocol["REINS-COM-FALHA"]["classificacao_conforme"],
            "falha",
        )

    def test_falha_tie_breaker_uses_highest_id_in_opposite_orders(self):
        analysis_date = date(2026, 8, 10)
        lower = {
            "id": 101,
            "protocolo": "TIE-FALHA",
            "matricula": "c90001a",
            "data_analise": analysis_date,
            "procedencia": "Procedente",
        }
        higher = {
            "id": 202,
            "protocolo": "TIE-FALHA",
            "matricula": "c90001a",
            "data_analise": analysis_date,
            "procedencia": "Improcedente",
        }

        for rows in ([lower, higher], [higher, lower]):
            by_case, by_protocolo = build_falha_lookup_indexes(rows)
            self.assertEqual(by_case[("TIE-FALHA", "c90001a")]["id"], 202)
            self.assertEqual(by_protocolo["TIE-FALHA"]["id"], 202)

    def test_auditado_case_tie_breaker_uses_highest_id_in_opposite_orders(self):
        analysis_date = date(2026, 8, 10)
        lower = {
            "id": 101,
            "protocolo": "TIE-AUD",
            "matricula": "c90001a",
            "data": date(2026, 8, 11),
            "data_analise": analysis_date,
            "procedencia": "Procedente",
        }
        higher = {
            "id": 202,
            "protocolo": "TIE-AUD",
            "matricula": "c90001a",
            "data": date(2026, 8, 12),
            "data_analise": analysis_date,
            "procedencia": "Improcedente",
        }

        for rows in ([lower, higher], [higher, lower]):
            raw_cases = derive_contestacao_cases_indexed(
                rows,
                by_case={},
                by_protocolo={},
            )
            cases = dedupe_contestacao_cases(raw_cases)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["_auditado_id"], 202)
            self.assertEqual(cases[0]["classificacao_conforme"], "nao_falha")

    def test_later_data_analise_wins_even_with_lower_id(self):
        lower_id_later = {
            "id": 101,
            "protocolo": "DATE-WINS",
            "matricula": "c90001a",
            "data": date(2026, 8, 12),
            "data_analise": date(2026, 8, 11),
            "procedencia": "Procedente",
        }
        higher_id_earlier = {
            "id": 202,
            "protocolo": "DATE-WINS",
            "matricula": "c90001a",
            "data": date(2026, 8, 11),
            "data_analise": date(2026, 8, 10),
            "procedencia": "Improcedente",
        }

        by_case, _ = build_falha_lookup_indexes([lower_id_later, higher_id_earlier])
        self.assertEqual(by_case[("DATE-WINS", "c90001a")]["id"], 101)

        raw_cases = derive_contestacao_cases_indexed(
            [higher_id_earlier, lower_id_later],
            by_case={},
            by_protocolo={},
        )
        cases = dedupe_contestacao_cases(raw_cases)
        self.assertEqual(cases[0]["_auditado_id"], 101)

    def test_none_data_analise_tie_uses_highest_id(self):
        lower = {
            "id": 101,
            "protocolo": "NONE-DATE",
            "matricula": "c90001a",
            "data": date(2026, 8, 11),
            "data_analise": None,
            "procedencia": "Procedente",
        }
        higher = {
            "id": 202,
            "protocolo": "NONE-DATE",
            "matricula": "c90001a",
            "data": date(2026, 8, 12),
            "data_analise": None,
            "procedencia": "Improcedente",
        }

        by_case, _ = build_falha_lookup_indexes([higher, lower])
        self.assertEqual(by_case[("NONE-DATE", "c90001a")]["id"], 202)

        raw_cases = derive_contestacao_cases_indexed(
            [higher, lower],
            by_case={},
            by_protocolo={},
        )
        cases = dedupe_contestacao_cases(raw_cases)
        self.assertEqual(cases[0]["_auditado_id"], 202)


@override_settings(
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
)
class ContestacaoDerivationParityTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=35, nome="BRB BANCO DE BRASILIA")
        self.id_cliente = 35
        # A paridade é exercitada diretamente sobre a fonte Intranet,
        # independente do cutover configurado no ambiente do runner.
        self.source_file = INTRANET_SOURCE_FILE
        for valor in ("Contestação", "Contestação Externa"):
            QualidadeFiltroOpcao.objects.get_or_create(
                dimensao="tipo_analise",
                valor=valor,
            )
        clear_contestacao_tipo_cache()

    def test_indicadores_matches_quality_overview_case_join(self):
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="PEXT1",
            tipo_analise="Contestação Externa",
            data=date(2026, 7, 10),
            data_recepcao_contestacao=date(2026, 7, 10),
            data_analise=date(2026, 7, 3),
            matricula="c90001a",
            procedencia="",
            source_file=self.source_file,
        )
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="PEXT2",
            tipo_analise="Contestação Externa",
            data=date(2026, 7, 16),
            data_recepcao_contestacao=date(2026, 7, 16),
            data_analise=date(2026, 7, 4),
            matricula="c90002a",
            procedencia="Improcedente",
            source_file=self.source_file,
        )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 3),
            id_cliente=self.id_cliente,
            protocolo="PEXT1",
            tipo_analise="Contestação Externa",
            modulo="Contestação",
            tipo_falha="Manual",
            matricula="c90001a",
            source_file=self.source_file,
        )
        # A falha histórica continua disponível para o batimento, mas não pode
        # inflar o volume do período corrente.
        QualidadeFalha.objects.create(
            data=date(2026, 6, 10),
            data_analise=date(2026, 6, 3),
            id_cliente=self.id_cliente,
            protocolo="PEXT-HIST",
            tipo_analise="Contestação Externa",
            modulo="Contestação",
            tipo_falha="Manual",
            matricula="c90003a",
            source_file=self.source_file,
        )

        params = {
            "id_cliente": str(self.id_cliente),
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "date_axis": "auditoria",
        }
        metrics = build_contestacao_metrics(params)
        self.assertEqual(metrics["casos_contestacao"], 2)
        self.assertEqual(metrics["eventos_contestacao"], 1)
        self.assertEqual(metrics["procedentes"], 1)
        self.assertEqual(metrics["improcedentes"], 1)
        self.assertIn("Quality Overview", metrics["procedencia_rule"])

        cont = derive_contestacao_from_eo(
            self.id_cliente,
            inicio=date(2026, 7, 1),
            fim=date(2026, 7, 31),
        )
        self.assertEqual(len(cont), 2)
        self.assertEqual(int((cont["classificacao_conforme"] == "falha").sum()), 1)
        self.assertEqual(int((cont["classificacao_conforme"] == "nao_falha").sum()), 1)

        from apps.qualidade_operacional.services.contestacao_derivation import (
            compute_contestacao_case_metrics,
        )

        scoped = compute_contestacao_case_metrics(
            params,
            start=date(2026, 7, 1),
            end=date(2026, 7, 31),
        )
        self.assertEqual(scoped["procedentes"], 1)
        self.assertEqual(scoped["improcedentes"], 1)
        self.assertEqual(scoped["eventos_contestacao"], 1)
        self.assertNotIn("_auditado_id", json.dumps(scoped, default=str))

    def test_procedencia_from_falha_when_auditado_empty(self):
        """Auditado sem procedência — decisão vem da falha batida (protocolo+matrícula)."""
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="1135084",
            tipo_analise="Contestação",
            data=date(2026, 8, 13),
            data_recepcao_contestacao=date(2026, 8, 13),
            data_analise=date(2026, 8, 12),
            matricula="c21064q",
            procedencia="",
            source_file=self.source_file,
        )
        QualidadeFalha.objects.create(
            data=date(2026, 8, 13),
            data_analise=date(2026, 8, 12),
            id_cliente=self.id_cliente,
            protocolo="1135084",
            tipo_analise="Contestação",
            modulo="Contestação",
            tipo_falha="Manual",
            matricula="c21064q",
            procedencia="Procedente",
            source_file=self.source_file,
        )
        params = {
            "id_cliente": str(self.id_cliente),
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        }
        metrics = build_contestacao_metrics(params)
        self.assertEqual(metrics["procedentes"], 1)

    def test_taxa_procedencia_uses_cases_in_both_sides(self):
        """Duas matrículas no mesmo protocolo não podem produzir taxa acima de 100%."""
        for index, matricula in enumerate(("c21064q", "c21065q"), start=1):
            QualidadeAuditado.objects.create(
                id_cliente=self.id_cliente,
                protocolo="PROTO-COMPARTILHADO",
                tipo_analise="Contestação Externa",
                data=date(2026, 8, 13),
                data_recepcao_contestacao=date(2026, 8, 13),
                data_analise=date(2026, 8, 12),
                matricula=matricula,
                procedencia="Procedente",
                source_file=self.source_file,
            )
            QualidadeFalha.objects.create(
                data=date(2026, 8, 13),
                data_analise=date(2026, 8, 12),
                id_cliente=self.id_cliente,
                protocolo="PROTO-COMPARTILHADO",
                tipo_analise="Contestação Externa",
                modulo="Contestação",
                tipo_falha="Manual",
                matricula=matricula,
                procedencia="Procedente",
                etapa=f"Etapa {index}",
                source_file=self.source_file,
            )

        metrics = build_contestacao_metrics(
            {
                "id_cliente": str(self.id_cliente),
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
            }
        )
        self.assertEqual(metrics["protocolos_contestados"], 1)
        self.assertEqual(metrics["casos_contestacao"], 2)
        self.assertEqual(metrics["procedentes"], 2)
        self.assertEqual(metrics["improcedentes"], 0)
        self.assertEqual(metrics["taxa_procedencia_pct"], 100.0)

    def test_falha_sem_matricula_bate_por_protocolo(self):
        """Falha importada sem matrícula ainda deriva decisão para o auditado."""
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="1023092",
            tipo_analise="Contestação Externa",
            data=date(2026, 4, 17),
            data_recepcao_contestacao=date(2026, 4, 17),
            data_analise=date(2025, 12, 20),
            matricula="c92762a",
            procedencia="",
            source_file=self.source_file,
        )
        QualidadeFalha.objects.create(
            data=date(2026, 4, 17),
            data_analise=date(2025, 12, 20),
            id_cliente=self.id_cliente,
            protocolo="1023092",
            tipo_analise="Contestação Externa",
            modulo="Contestação",
            tipo_falha="Processual",
            matricula="",
            procedencia="",
            source_file=self.source_file,
        )
        params = {
            "id_cliente": str(self.id_cliente),
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
        }
        metrics = build_contestacao_metrics(params)
        self.assertEqual(metrics["casos_contestacao"], 1)
        self.assertEqual(metrics["procedentes"], 1)

        cont = derive_contestacao_from_eo(
            self.id_cliente,
            inicio=date(2026, 4, 1),
            fim=date(2026, 4, 30),
        )
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont.iloc[0]["CONFORME"], "Não")

    def test_contestacao_flow_included_in_quality_overview(self):
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="1135084",
            tipo_analise="Contestação",
            data=date(2026, 8, 13),
            data_recepcao_contestacao=date(2026, 8, 13),
            data_analise=date(2026, 8, 12),
            matricula="c21064q",
            procedencia="",
            source_file=self.source_file,
        )
        QualidadeFalha.objects.create(
            data=date(2026, 8, 13),
            data_analise=date(2026, 8, 12),
            id_cliente=self.id_cliente,
            protocolo="1135084",
            tipo_analise="Contestação",
            modulo="Contestação",
            tipo_falha="Manual",
            matricula="c21064q",
            procedencia="Procedente",
            source_file=self.source_file,
        )
        cont = derive_contestacao_from_eo(
            self.id_cliente,
            inicio=date(2026, 8, 1),
            fim=date(2026, 8, 31),
        )
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont.iloc[0]["Protocolo"], "1135084")
        self.assertEqual(cont.iloc[0]["CONFORME"], "Não")

    def test_gaq_excluded_from_operational_contestacao_without_client_filter(self):
        DimCliente.objects.create(id_cliente=CLIENTE_GAQ_ID, nome="GAQ")
        QualidadeAuditado.objects.create(
            id_cliente=CLIENTE_GAQ_ID,
            protocolo="GAQ1",
            tipo_analise="Contestação",
            data=date(2026, 5, 10),
            data_recepcao_contestacao=date(2026, 5, 10),
            matricula="c00001a",
            source_file=self.source_file,
        )
        QualidadeAuditado.objects.create(
            id_cliente=self.id_cliente,
            protocolo="BRB1",
            tipo_analise="Contestação",
            data=date(2026, 5, 11),
            data_recepcao_contestacao=date(2026, 5, 11),
            matricula="c90003a",
            source_file=self.source_file,
        )
        params = {
            "start_date": "2026-05-01",
            "end_date": "2026-05-31",
        }
        metrics = build_contestacao_metrics(params)
        self.assertEqual(metrics["casos_contestacao"], 1)

        explicit = build_contestacao_metrics(
            {**params, "id_cliente": str(CLIENTE_GAQ_ID)}
        )
        self.assertEqual(explicit["casos_contestacao"], 1)
