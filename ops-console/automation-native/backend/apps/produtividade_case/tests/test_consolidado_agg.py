# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PROC_USUARIO, role_group_name
from apps.produtividade_case.constants import (
    DIM_ALERTA_DESTINO,
    DIM_CLIENTE_ORIGEM,
    DIM_MATRICULA_DESTINO,
    DIM_NH_ORIGEM,
    DIM_VOLUME_CADASTRO_DIA,
    DIM_VOLUME_DIA,
    DIM_WORKFLOW_ORIGEM,
)
from apps.produtividade_case.models import (
    CaseConsolidadoDailyAgg,
    CaseConsolidadoFact,
    CaseConsolidadoSnapshot,
)
from apps.produtividade_case.services.analitica_detail import (
    serialize_agente_detalhe,
    serialize_protocolos,
    serialize_ranking,
)
from apps.produtividade_case.services.consolidado_agg import (
    build_daily_agg_buckets,
    serialize_por_agente,
    serialize_por_workflow,
    serialize_serie_diaria,
    sync_consolidado_daily_aggs,
)
from apps.produtividade_case.services.mvp_metrics import (
    percentile_nearest_rank,
    serialize_matriz_origem_destino,
)
from apps.produtividade_case.services.reconciliation import reconcile_case_manager
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from datetime import date

from apps.workforce.models import Agent, AgentHistory

User = get_user_model()
TZ_BR = ZoneInfo("America/Sao_Paulo")


def _sample_rows():
    return [
        {
            "protocolo_destino": "P1",
            "conclusao_destino_at": datetime(2026, 7, 10, 14, 0, tzinfo=TZ_BR),
            "cadastro_destino_at": datetime(2026, 7, 8, 9, 0, tzinfo=TZ_BR),
            "tempo_analise_segundos": 120,
            "workflow_origem": "WF-A",
            "matricula_destino": "C12345A",
            "matricula_origem": "C11111A",
            "resultado_origem": "OK",
            "resultado_destino": "OK",
            "status_destino": "COMPLETED",
            "tipo_conclusao_origem": "Manual",
            "nh_origem": "N1",
            "cliente_origem": "Cliente X",
            "alertas_destino": "Alerta A | Alerta B",
        },
        {
            "protocolo_destino": "P2",
            "conclusao_destino_at": datetime(2026, 7, 10, 15, 0, tzinfo=TZ_BR),
            "cadastro_destino_at": datetime(2026, 7, 10, 8, 0, tzinfo=TZ_BR),
            "tempo_analise_segundos": 180,
            "workflow_origem": "WF-A",
            "matricula_destino": "C12345A",
            "matricula_origem": "C11111A",
            "resultado_origem": "OK",
            "resultado_destino": "NOK",
            "status_destino": "COMPLETED",
            "tipo_conclusao_origem": "Automático",
            "nh_origem": "N1",
            "cliente_origem": "Cliente Y",
            "alertas_destino": "Alerta A",
        },
        {
            "protocolo_destino": "P3",
            "conclusao_destino_at": datetime(2026, 7, 11, 10, 0, tzinfo=TZ_BR),
            "cadastro_destino_at": datetime(2026, 7, 8, 11, 0, tzinfo=TZ_BR),
            "tempo_analise_segundos": 60,
            "workflow_origem": "WF-B",
            "matricula_destino": "C99999B",
            "matricula_origem": "C22222B",
            "resultado_origem": "OK",
            "resultado_destino": "OK",
            "status_destino": "COMPLETED",
            "tipo_conclusao_origem": "Manual",
            "nh_origem": "N2",
            "cliente_origem": "Cliente X",
            "alertas_destino": "",
        },
    ]


class ConsolidadoAggUnitTests(TestCase):
    def test_build_daily_agg_buckets(self):
        total, buckets = build_daily_agg_buckets(_sample_rows())
        self.assertEqual(total, 3)
        day = date(2026, 7, 10)
        self.assertEqual(buckets[(day, DIM_WORKFLOW_ORIGEM, "WF-A")]["count"], 2)
        self.assertEqual(buckets[(day, DIM_MATRICULA_DESTINO, "c12345a")]["count"], 2)
        self.assertEqual(buckets[(day, DIM_VOLUME_DIA, "total")]["count"], 2)
        self.assertEqual(buckets[(day, DIM_NH_ORIGEM, "N1")]["count"], 2)
        self.assertEqual(buckets[(day, DIM_CLIENTE_ORIGEM, "Cliente X")]["count"], 1)
        self.assertEqual(buckets[(day, DIM_ALERTA_DESTINO, "Alerta A")]["count"], 2)
        # Cadastro em dia diferente da conclusão
        cad_day = date(2026, 7, 8)
        self.assertEqual(buckets[(cad_day, DIM_VOLUME_CADASTRO_DIA, "total")]["count"], 2)
        self.assertEqual(buckets[(day, DIM_VOLUME_CADASTRO_DIA, "total")]["count"], 1)

    def test_daily_agg_preserves_full_client_cardinality(self):
        rows = []
        for idx in range(35):
            row = dict(_sample_rows()[0])
            row["protocolo_destino"] = f"CARD-{idx}"
            row["cliente_origem"] = f"Cliente {idx:02d}"
            rows.append(row)
        total, buckets = build_daily_agg_buckets(rows)
        client_keys = {
            key
            for (_day, dimension, key), _values in buckets.items()
            if dimension == DIM_CLIENTE_ORIGEM
        }
        self.assertEqual(total, 35)
        self.assertEqual(len(client_keys), 35)
        self.assertNotIn("(outros)", client_keys)

    def test_serie_diaria_dual_and_workflow_filter(self):
        snap = CaseConsolidadoSnapshot.objects.create(
            periodo_mes="jul-2026",
            captured_at=timezone.now(),
            total_protocolos=3,
            success=True,
            source_file="test",
            message="unit",
        )
        total, buckets = build_daily_agg_buckets(_sample_rows())
        CaseConsolidadoDailyAgg.objects.bulk_create(
            [
                CaseConsolidadoDailyAgg(
                    snapshot=snap,
                    periodo_mes="jul-2026",
                    day=day,
                    dimension=dim,
                    key=key,
                    count=vals["count"],
                    analysis_seconds_sum=vals["seconds"],
                )
                for (day, dim, key), vals in buckets.items()
            ]
        )
        for row in _sample_rows():
            CaseConsolidadoFact.objects.create(
                snapshot=snap,
                periodo_mes="jul-2026",
                protocolo_destino=row["protocolo_destino"],
                workflow_origem=row["workflow_origem"],
                matricula_destino=row["matricula_destino"].lower(),
                resultado_destino=row["resultado_destino"],
                status_destino=row["status_destino"],
                tipo_conclusao_origem=row["tipo_conclusao_origem"],
                nh_origem=row["nh_origem"],
                cliente_origem=row["cliente_origem"],
                cadastro_destino_at=row["cadastro_destino_at"],
                conclusao_destino_at=row["conclusao_destino_at"],
                tempo_analise_segundos=row["tempo_analise_segundos"],
            )

        serie = serialize_serie_diaria(periodo_mes="jul-2026")
        self.assertEqual(serie["total_concluidos"], 3)
        self.assertEqual(serie["total_cadastrados"], 3)
        by_day = {p["day"]: p for p in serie["points"]}
        self.assertEqual(by_day["2026-07-08"]["cadastrados"], 2)
        self.assertEqual(by_day["2026-07-08"]["concluidos"], 0)
        self.assertEqual(by_day["2026-07-10"]["concluidos"], 2)
        self.assertEqual(by_day["2026-07-10"]["cadastrados"], 1)
        self.assertEqual(by_day["2026-07-10"]["count"], 2)

        wf_a = serialize_serie_diaria(periodo_mes="jul-2026", workflow_origem="WF-A")
        self.assertEqual(wf_a["workflow_origem"], "WF-A")
        self.assertEqual(wf_a["total_concluidos"], 2)
        self.assertEqual(wf_a["total_cadastrados"], 2)
        wf_days = {p["day"]: p for p in wf_a["points"]}
        self.assertEqual(wf_days["2026-07-08"]["cadastrados"], 1)
        self.assertNotIn("2026-07-11", wf_days)

    def test_percentile_and_matriz(self):
        self.assertEqual(percentile_nearest_rank([10, 20, 30, 40, 50], 50), 30.0)
        snap = CaseConsolidadoSnapshot.objects.create(
            periodo_mes="jul-2026",
            captured_at=timezone.now(),
            total_protocolos=3,
            success=True,
            source_file="test",
            message="unit",
        )
        for row in _sample_rows():
            CaseConsolidadoFact.objects.create(
                snapshot=snap,
                periodo_mes="jul-2026",
                protocolo_destino=row["protocolo_destino"],
                workflow_origem=row["workflow_origem"],
                matricula_destino=row["matricula_destino"].lower(),
                resultado_origem=row["resultado_origem"],
                resultado_destino=row["resultado_destino"],
                status_destino=row["status_destino"],
                tipo_conclusao_origem=row["tipo_conclusao_origem"],
                nh_origem=row["nh_origem"],
                cliente_origem=row["cliente_origem"],
                cadastro_destino_at=row["cadastro_destino_at"],
                conclusao_destino_at=row["conclusao_destino_at"],
                tempo_analise_segundos=row["tempo_analise_segundos"],
            )
        matriz = serialize_matriz_origem_destino(periodo_mes="jul-2026")
        self.assertEqual(matriz["total_comparavel"], 3)
        self.assertEqual(matriz["total_coincidente"], 2)
        self.assertAlmostEqual(matriz["taxa_coincidencia"], 66.67, places=1)
        self.assertEqual(matriz["total_automatico"], 1)
        self.assertEqual(matriz["total_manual"], 2)
        self.assertEqual(matriz["total_tipo_conclusao_nao_classificado"], 0)

        CaseConsolidadoFact.objects.create(
            snapshot=snap,
            periodo_mes="jul-2026",
            protocolo_destino="P-SEM-RESULTADO",
            resultado_origem="",
            resultado_destino="OK",
            conclusao_destino_at=datetime(2026, 7, 11, 12, 0, tzinfo=TZ_BR),
        )
        matriz = serialize_matriz_origem_destino(periodo_mes="jul-2026")
        self.assertEqual(matriz["total_comparavel"], 3)
        self.assertEqual(matriz["total_nao_comparavel"], 1)
        self.assertEqual(matriz["total_nao_coincidente"], 1)
        self.assertEqual(matriz["total_tipo_conclusao_nao_classificado"], 1)


class ConsolidadoAggSyncTests(TestCase):
    def test_sync_from_excel(self):
        path = Path(self._write_xlsx())
        snap = sync_consolidado_daily_aggs(path, periodo_mes="jul-2026")
        self.assertTrue(snap.success)
        self.assertEqual(snap.periodo_mes, "jul-2026")
        self.assertGreaterEqual(snap.total_protocolos, 2)
        self.assertTrue(isinstance(snap.tempo_stats_json, dict))
        self.assertTrue(snap.tempo_stats_json)
        self.assertTrue(
            CaseConsolidadoDailyAgg.objects.filter(
                snapshot=snap, dimension=DIM_WORKFLOW_ORIGEM
            ).exists()
        )
        self.assertGreaterEqual(
            CaseConsolidadoFact.objects.filter(periodo_mes="jul-2026").count(),
            2,
        )
        self.assertTrue(
            CaseConsolidadoFact.objects.filter(
                periodo_mes="jul-2026", cadastro_destino_at__isnull=False
            ).exists()
        )
        serie = serialize_serie_diaria(periodo_mes="jul-2026")
        self.assertGreaterEqual(serie["total_concluidos"], 2)
        self.assertGreaterEqual(serie["total_cadastrados"], 2)

        # reload replaces previous snapshot for period
        snap2 = sync_consolidado_daily_aggs(path, periodo_mes="jul-2026")
        self.assertEqual(
            CaseConsolidadoSnapshot.objects.filter(periodo_mes="jul-2026").count(),
            1,
        )
        self.assertEqual(snap2.pk, CaseConsolidadoSnapshot.objects.get().pk)

        wf = serialize_por_workflow(periodo_mes="jul-2026")
        self.assertTrue(wf["has_data"])
        self.assertGreater(wf["total"], 0)

        ag = serialize_por_agente(periodo_mes="jul-2026")
        self.assertTrue(ag["has_data"])
        self.assertTrue(any(i.get("matricula") for i in ag["items"]))

        proto = serialize_protocolos(periodo_mes="jul-2026", page=1, page_size=10)
        self.assertGreater(proto["count"], 0)
        self.assertTrue(proto["results"])
        self.assertGreater(proto["total_workflows"], 0)
        self.assertGreater(proto["total_resultados_destino"], 0)
        self.assertGreater(proto["count_com_tempo"], 0)
        self.assertIsNotNone(proto["tma_seconds"])

        ranking = serialize_ranking(dimension=DIM_WORKFLOW_ORIGEM, periodo_mes="jul-2026")
        self.assertGreater(ranking["total"], 0)

        mat = proto["results"][0]["matricula_destino"]
        detalhe = serialize_agente_detalhe(mat, periodo_mes="jul-2026")
        self.assertEqual(detalhe["matricula"], mat.lower())
        self.assertGreaterEqual(detalhe["count"], 1)

        reconciliation = reconcile_case_manager(periodo_mes="jul-2026")
        consolidado_checks = [
            check for check in reconciliation["checks"] if check["name"].startswith("consolidado")
        ]
        self.assertTrue(consolidado_checks)
        self.assertTrue(all(check["ok"] for check in consolidado_checks))
        self.assertTrue(all("protocolo" not in check for check in reconciliation["checks"]))

    def _write_xlsx(self) -> str:
        import os
        import tempfile

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Produtividade"
        headers = [
            "Protocolo Destino",
            "Workflow Origem",
            "Matricula Destino",
            "Resultado Destino",
            "Status Destino",
            "Tipo Conclusao Origem",
            "Data Cadastro Destino",
            "Hora Cadastro Destino",
            "Data Conclusao Destino",
            "Hora Conclusao Destino",
            "Tempo de Analise",
        ]
        ws.append(headers)
        ws.append(
            [
                "proto-1",
                "WF-A",
                "C12345A",
                "OK",
                "COMPLETED",
                "Manual",
                "08/07/2026",
                "09:00:00",
                "10/07/2026",
                "14:00:00",
                "00:02:00",
            ]
        )
        ws.append(
            [
                "proto-2",
                "WF-B",
                "C99999B",
                "NOK",
                "COMPLETED",
                "Automático",
                "09/07/2026",
                "10:00:00",
                "11/07/2026",
                "10:00:00",
                "00:01:00",
            ]
        )
        fd, name = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        wb.save(name)
        return name


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class CaseManagerAnaliticaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.op = self._user("c97001a", ROLE_OP_AGENTE)
        self.proc = self._user("c97002a", ROLE_PROC_USUARIO)
        path = Path(ConsolidadoAggSyncTests()._write_xlsx())
        sync_consolidado_daily_aggs(path, periodo_mes="jul-2026")

    def _user(self, username: str, role: str):
        user = User.objects.create_user(username, email=f"{username}@t.local", password="x")
        agent = Agent.objects.create(
            user_lan_id=username,
            full_name=username,
            active=True,
            hire_date=date(2024, 1, 1),
        )
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional/Alpha",
            job_title="Agente",
            start_date=date(2024, 1, 1),
            active=True,
        )
        Group.objects.get_or_create(name=role_group_name(role))
        user.groups.add(Group.objects.get(name=role_group_name(role)))
        return user

    def test_analitica_allow_deny(self):
        self.client.force_authenticate(user=self.op)
        for path in (
            "/api/v1/case-manager/analitica/resumo/",
            "/api/v1/case-manager/analitica/por-workflow/?periodo_mes=jul-2026",
            "/api/v1/case-manager/analitica/por-agente/?periodo_mes=jul-2026",
            "/api/v1/case-manager/analitica/serie-diaria/?periodo_mes=jul-2026",
            "/api/v1/case-manager/analitica/cruzamento/?periodo_mes=jul-2026",
            "/api/v1/case-manager/analitica/matriz-origem-destino/?periodo_mes=jul-2026",
            "/api/v1/case-manager/analitica/ranking/?dimension=workflow_origem&periodo_mes=jul-2026",
            "/api/v1/case-manager/analitica/protocolos/?periodo_mes=jul-2026&page=1&page_size=10",
            "/api/v1/case-manager/analitica/agente/C12345A/?periodo_mes=jul-2026",
        ):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)

        proto = self.client.get(
            "/api/v1/case-manager/analitica/protocolos/?periodo_mes=jul-2026&page_size=10"
        )
        self.assertGreater(proto.data["count"], 0)
        self.assertLessEqual(len(proto.data["results"]), 10)

        self.client.force_authenticate(user=self.proc)
        resp = self.client.get("/api/v1/case-manager/analitica/por-workflow/")
        self.assertEqual(resp.status_code, 403)
        deny = self.client.get("/api/v1/case-manager/analitica/protocolos/")
        self.assertEqual(deny.status_code, 403)

    def test_period_validation_and_no_silent_fallback(self):
        self.client.force_authenticate(user=self.op)
        invalid = self.client.get(
            "/api/v1/case-manager/analitica/resumo/?date_from=2026-99-01"
        )
        self.assertEqual(invalid.status_code, 400)
        inverted = self.client.get(
            "/api/v1/case-manager/analitica/resumo/?date_from=2026-07-20&date_to=2026-07-01"
        )
        self.assertEqual(inverted.status_code, 400)
        malformed = self.client.get(
            "/api/v1/case-manager/analitica/resumo/?periodo_mes=2026-07"
        )
        self.assertEqual(malformed.status_code, 400)
        missing = self.client.get(
            "/api/v1/case-manager/analitica/resumo/?periodo_mes=ago-2026"
        )
        self.assertEqual(missing.status_code, 200)
        self.assertFalse(missing.data["has_data"])
        self.assertEqual(missing.data["periodo_mes"], "ago-2026")

    def test_cross_month_range_combines_latest_snapshots_and_reports_completeness(self):
        jul = CaseConsolidadoSnapshot.objects.get(periodo_mes="jul-2026")
        aug = CaseConsolidadoSnapshot.objects.create(
            periodo_mes="ago-2026",
            captured_at=timezone.now(),
            total_protocolos=3,
            success=True,
        )
        CaseConsolidadoDailyAgg.objects.create(
            snapshot=aug,
            periodo_mes="ago-2026",
            day=date(2026, 8, 1),
            dimension=DIM_VOLUME_DIA,
            key="__volume__",
            count=3,
            analysis_seconds_sum=90,
        )
        july_count = CaseConsolidadoDailyAgg.objects.filter(
            snapshot=jul,
            day__gte=date(2026, 7, 1),
            dimension=DIM_VOLUME_DIA,
        ).aggregate(total=Sum("count"))["total"] or 0

        self.client.force_authenticate(user=self.op)
        response = self.client.get(
            "/api/v1/case-manager/analitica/resumo/",
            {"date_from": "2026-07-01", "date_to": "2026-08-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_periodo"], july_count + 3)
        self.assertEqual(response.data["periodo_meses"], ["jul-2026", "ago-2026"])
        self.assertTrue(response.data["range_complete"])
        self.assertEqual(response.data["missing_periodos"], [])

        partial = self.client.get(
            "/api/v1/case-manager/analitica/resumo/",
            {"date_from": "2026-07-01", "date_to": "2026-09-01"},
        )
        self.assertFalse(partial.data["range_complete"])
        self.assertEqual(partial.data["missing_periodos"], ["set-2026"])

    def test_protocol_filters_search_and_pagination_cap(self):
        self.client.force_authenticate(user=self.op)
        fact = CaseConsolidadoFact.objects.filter(periodo_mes="jul-2026").first()
        fact.resultado_origem = "ORIGEM-ESPECIAL"
        fact.cliente_origem = "Cliente Pesquisavel"
        fact.save(update_fields=["resultado_origem", "cliente_origem"])

        by_origin = self.client.get(
            "/api/v1/case-manager/analitica/protocolos/",
            {"periodo_mes": "jul-2026", "resultado_origem": "ORIGEM-ESPECIAL"},
        )
        self.assertEqual(by_origin.data["count"], 1)
        self.assertEqual(by_origin.data["results"][0]["resultado_origem"], "ORIGEM-ESPECIAL")
        searched = self.client.get(
            "/api/v1/case-manager/analitica/protocolos/",
            {"periodo_mes": "jul-2026", "q": "Pesquisavel", "page_size": 999},
        )
        self.assertEqual(searched.data["count"], 1)
        self.assertEqual(searched.data["page_size"], 100)

    def test_workflow_time_metrics_respect_date_filter_and_null_coverage(self):
        snap = CaseConsolidadoSnapshot.objects.get(periodo_mes="jul-2026")
        CaseConsolidadoFact.objects.create(
            snapshot=snap,
            periodo_mes="jul-2026",
            protocolo_destino="NULL-TIME",
            workflow_origem="WF-A",
            conclusao_destino_at=datetime(2026, 7, 10, 18, 0, tzinfo=TZ_BR),
            tempo_analise_segundos=None,
        )
        agg = CaseConsolidadoDailyAgg.objects.get(
            snapshot=snap,
            day=date(2026, 7, 10),
            dimension=DIM_WORKFLOW_ORIGEM,
            key="WF-A",
        )
        agg.count += 1
        agg.save(update_fields=["count"])
        result = serialize_por_workflow(
            periodo_mes="jul-2026", date_from=date(2026, 7, 10), date_to=date(2026, 7, 10)
        )
        wf = next(item for item in result["items"] if item["key"] == "WF-A")
        self.assertEqual(wf["count_com_tempo"], 1)
        self.assertEqual(wf["tma_seconds"], 120.0)
        self.assertEqual(wf["cobertura_tempo_pct"], 50.0)
