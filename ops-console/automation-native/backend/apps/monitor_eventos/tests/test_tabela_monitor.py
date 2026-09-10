# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.monitor_eventos.services.tabela_monitor import (
    _data_jornada,
    _fim_hora_inclusiva,
    build_tabela_monitor,
    build_tabela_monitor_from_records,
)
from apps.produtividade.models import ProductivityRecord


def _aware(dt: datetime) -> datetime:
    return timezone.make_aware(dt, timezone.get_current_timezone())


def _session(
    matricula: str,
    login: datetime,
    logout: datetime,
    data: datetime | None = None,
) -> dict:
    login = _aware(login)
    logout = _aware(logout)
    return {
        "matricula_usuario": matricula,
        "data_evento": login,
        "data_segundo_evento": logout,
        "evento": "Autenticação com sucesso",
        "segundo_evento": "Logout",
        "data": (data or login).date(),
        "hora": login.hour,
    }


class TabelaMonitorLogicTests(TestCase):
    def test_data_jornada_madrugada(self):
        dt = _aware(datetime(2026, 6, 25, 4, 0, 0))
        self.assertEqual(_data_jornada(dt), datetime(2026, 6, 24).date())

    def test_fim_hora_inclusiva_no_limite(self):
        dt = _aware(datetime(2026, 6, 25, 10, 0, 0))
        fim = _fim_hora_inclusiva(dt)
        self.assertEqual(fim, _aware(datetime(2026, 6, 25, 9, 0, 0)))

    def test_sessao_unica_10_12(self):
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 18, 10, 0, 0),
                datetime(2026, 6, 18, 12, 0, 0),
            )
        ]
        results = build_tabela_monitor_from_records(records)
        hourly = [r for r in results if r["hora"] is not None]
        self.assertEqual(len(hourly), 2)
        self.assertEqual(sum(r["total_logado"] for r in hourly), 7200)
        null_rows = [r for r in results if r["hora"] is None]
        self.assertEqual(len(null_rows), 1)

    def test_multiplas_sessoes_mesmo_dia(self):
        records = [
            _session(
                "c11023q",
                datetime(2026, 6, 25, 9, 0, 25),
                datetime(2026, 6, 25, 9, 15, 28),
            ),
            _session(
                "c11023q",
                datetime(2026, 6, 25, 9, 16, 17),
                datetime(2026, 6, 25, 9, 30, 0),
            ),
            _session(
                "c11023q",
                datetime(2026, 6, 25, 9, 31, 0),
                datetime(2026, 6, 25, 10, 0, 0),
            ),
        ]
        results = build_tabela_monitor_from_records(records)
        hour_9 = next(r for r in results if r["hora"] == 9)
        self.assertGreater(hour_9["total_logado"], 0)
        self.assertEqual(hour_9["meta_hora"], hour_9["total_logado"] + hour_9["tempo_off"])

    def test_hora_sem_logado_na_grade(self):
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 18, 10, 0, 0),
                datetime(2026, 6, 18, 10, 30, 0),
            ),
            _session(
                "c92928a",
                datetime(2026, 6, 18, 12, 0, 0),
                datetime(2026, 6, 18, 12, 30, 0),
            ),
        ]
        results = build_tabela_monitor_from_records(records)
        hour_11 = next(r for r in results if r["hora"] == 11)
        self.assertEqual(hour_11["total_logado"], 0)
        self.assertEqual(hour_11["tempo_off"], 3600)

    def test_build_from_model_queryset(self):
        MonitorEventoRecord.objects.create(
            data=datetime(2026, 6, 18).date(),
            hora=10,
            matricula_usuario="c92928a",
            data_evento=_aware(datetime(2026, 6, 18, 10, 0, 0)),
            evento="Autenticação com sucesso",
            data_segundo_evento=_aware(datetime(2026, 6, 18, 12, 0, 0)),
            segundo_evento="Logout",
        )
        results = build_tabela_monitor()
        self.assertGreaterEqual(len(results), 3)


class TabelaMonitorOciosidadeTests(TestCase):
    def _prod_record(self, matricula: str, day: datetime, hour: int, seconds: int):
        return ProductivityRecord.objects.create(
            matricula_norm=matricula.lower(),
            etapa="Etapa A",
            analysis_seconds=seconds,
            analysis_count=1,
            stage_goal=Decimal("10"),
            recorded_at=day.replace(hour=hour),
            agent_name=matricula,
            team="Equipe A",
        )

    def test_sem_produtividade_tempo_ocioso_zero(self):
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 18, 10, 0, 0),
                datetime(2026, 6, 18, 12, 0, 0),
            )
        ]
        results = build_tabela_monitor_from_records(records, tempo_analise_lookup={})
        hourly = [r for r in results if r["hora"] is not None]
        self.assertEqual(hourly[0]["tempo_logado"], hourly[0]["total_logado"])
        self.assertEqual(hourly[0]["tempo_analise"], 0)
        self.assertEqual(hourly[0]["tempo_ocioso"], hourly[0]["tempo_logado"])
        self.assertEqual(hourly[0]["tempo_logado_dia"], 7200)
        self.assertEqual(hourly[0]["tempo_ocioso_dia"], 7200)

    def test_com_produtividade_calcula_ociosidade(self):
        day = _aware(datetime(2026, 6, 18, 0, 0, 0))
        self._prod_record("c92928a", day, 10, 1800)
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 18, 10, 0, 0),
                datetime(2026, 6, 18, 11, 0, 0),
            )
        ]
        results = build_tabela_monitor_from_records(records)
        hour_10 = next(r for r in results if r["hora"] == 10)
        self.assertEqual(hour_10["tempo_logado"], 3600)
        self.assertEqual(hour_10["tempo_analise"], 1800)
        self.assertEqual(hour_10["tempo_ocioso"], 1800)

    def test_madrugada_alinha_jornada_produtividade(self):
        """Produtividade 00:00–05:14 usa data_jornada anterior (corte 05:15)."""
        civil_day = _aware(datetime(2026, 6, 25, 0, 0, 0))
        self._prod_record("c92928a", civil_day, 4, 1800)
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 25, 4, 0, 0),
                datetime(2026, 6, 25, 5, 0, 0),
            )
        ]
        results = build_tabela_monitor_from_records(records)
        hour_4 = next(r for r in results if r["hora"] == 4)
        self.assertEqual(hour_4["data_jornada"], "2026-06-24")
        self.assertEqual(hour_4["tempo_logado"], 3600)
        self.assertEqual(hour_4["tempo_analise"], 1800)
        self.assertEqual(hour_4["tempo_ocioso"], 1800)

    def test_produtividade_maior_que_logado_ociosidade_zero(self):
        day = _aware(datetime(2026, 6, 18, 0, 0, 0))
        self._prod_record("c92928a", day, 10, 5000)
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 18, 10, 0, 0),
                datetime(2026, 6, 18, 10, 30, 0),
            )
        ]
        results = build_tabela_monitor_from_records(records)
        hour_10 = next(r for r in results if r["hora"] == 10)
        self.assertEqual(hour_10["tempo_ocioso"], 0)

    def test_totais_diarios_somam_horas(self):
        day = _aware(datetime(2026, 6, 18, 0, 0, 0))
        self._prod_record("c92928a", day, 10, 1000)
        self._prod_record("c92928a", day, 11, 500)
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 18, 10, 0, 0),
                datetime(2026, 6, 18, 12, 0, 0),
            )
        ]
        results = build_tabela_monitor_from_records(records)
        hourly = [r for r in results if r["hora"] is not None]
        expected_logado = sum(r["tempo_logado"] for r in hourly)
        expected_ocioso = sum(r["tempo_ocioso"] for r in hourly)
        self.assertEqual(hourly[0]["tempo_logado_dia"], expected_logado)
        self.assertEqual(hourly[0]["tempo_ocioso_dia"], expected_ocioso)

    def test_linha_null_tem_totais_diarios(self):
        records = [
            _session(
                "c92928a",
                datetime(2026, 6, 18, 10, 0, 0),
                datetime(2026, 6, 18, 11, 0, 0),
            )
        ]
        results = build_tabela_monitor_from_records(records, tempo_analise_lookup={})
        null_row = next(r for r in results if r["hora"] is None)
        self.assertIsNone(null_row["tempo_logado"])
        self.assertIsNone(null_row["tempo_ocioso"])
        self.assertIsNone(null_row["tempo_analise"])
        self.assertEqual(null_row["tempo_logado_dia"], 3600)
        self.assertEqual(null_row["tempo_ocioso_dia"], 3600)


class TabelaMonitorAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        MonitorEventoRecord.objects.create(
            data=datetime(2026, 6, 18).date(),
            hora=10,
            matricula_usuario="c92928a",
            data_evento=_aware(datetime(2026, 6, 18, 10, 0, 0)),
            evento="Autenticação com sucesso",
            data_segundo_evento=_aware(datetime(2026, 6, 18, 12, 0, 0)),
            segundo_evento="Logout",
        )

    def test_requires_auth(self):
        response = self.client.get("/api/v1/monitor-eventos/tabela-monitor/")
        self.assertEqual(response.status_code, 403)

    def test_returns_tabela_monitor(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="portal",
            password="test-pass-123",
        )
        self.client.force_authenticate(user=user)
        response = self.client.get(
            "/api/v1/monitor-eventos/tabela-monitor/",
            {"data_jornada": "2026-06-18"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("count", payload)
        self.assertIn("results", payload)
        self.assertGreater(payload["count"], 0)
        row = payload["results"][0]
        for field in (
            "tempo_logado",
            "tempo_ocioso",
            "tempo_analise",
            "tempo_logado_dia",
            "tempo_ocioso_dia",
        ):
            self.assertIn(field, row)

    def test_filter_matricula(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="portal2",
            password="test-pass-123",
        )
        self.client.force_authenticate(user=user)
        response = self.client.get(
            "/api/v1/monitor-eventos/tabela-monitor/",
            {"data_jornada": "2026-06-18", "matricula": "c92928a"},
        )
        self.assertEqual(response.status_code, 200)
        for row in response.json()["results"]:
            self.assertEqual(row["matricula_usuario"], "C92928A")
