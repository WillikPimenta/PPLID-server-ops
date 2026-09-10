from __future__ import annotations

import json
import os
import platform
import re
import statistics
import time
import tracemalloc
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import django
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaControleRegistro,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaFalha,
)


User = get_user_model()
VOLUMES = (
    ("small", 20, 5),
    ("medium", 200, 5),
    ("large", 1000, 5),
)
TABLES = (
    "auditoria_atividade",
    "auditoria_atividade_protocolo",
    "auditoria_controle_registro",
    "auditoria_falha_cadastro",
    "qualidade_pendente_auditoria_falha",
)


def _query_fingerprint(sql: str) -> str:
    value = re.sub(r"'[^']*'", "?", sql)
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "?", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:500]


def _select_from_logged_sql(sql: str) -> str | None:
    cleaned = sql.strip().rstrip(";")
    if cleaned.upper().startswith("SELECT "):
        return cleaned
    if cleaned.upper().startswith("DECLARE ") and " FOR SELECT " in cleaned.upper():
        marker = cleaned.upper().index(" FOR SELECT ") + len(" FOR ")
        return cleaned[marker:]
    return None


class _QueryRecorder:
    """Counts and times every DB execution without Django's 9,000-query log cap."""

    def __init__(self) -> None:
        self.count = 0
        self.sql_ms = 0.0
        self.fingerprints: Counter[str] = Counter()
        self.slowest: list[dict[str, Any]] = []

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.count += 1
            self.sql_ms += elapsed_ms
            self.fingerprints[_query_fingerprint(sql)] += 1
            if not many and _select_from_logged_sql(sql):
                self.slowest.append(
                    {"elapsed_ms": elapsed_ms, "sql": sql, "params": params}
                )
                self.slowest.sort(key=lambda item: item["elapsed_ms"], reverse=True)
                del self.slowest[12:]


@override_settings(ACCESS_ENFORCEMENT=False)
class DashboardPerformanceBenchmarks(TestCase):
    """Synthetic benchmark; must only run in the dedicated disposable test DB."""

    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        db_name = str(connection.settings_dict.get("NAME") or "")
        if db_name != "test_pplid_codex_performance":
            raise RuntimeError(f"Unsafe benchmark database: {db_name!r}")
        cls.user = User.objects.create_superuser(
            username="performance_dashboard_user",
            email="performance-dashboard@test.local",
            password="test-only-password",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _seed_until(self, target_activities_per_dashboard: int, children_per_activity: int) -> None:
        existing = AuditoriaAtividade.objects.filter(
            nome__startswith="PERF-",
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
        ).count()
        if existing >= target_activities_per_dashboard:
            return

        statuses = (
            AuditoriaAtividade.STATUS_PENDENTE,
            AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            AuditoriaAtividade.STATUS_CONCLUIDA,
        )
        new_count = target_activities_per_dashboard - existing
        received = date.today() - timedelta(days=2)
        audit_activities = AuditoriaAtividade.objects.bulk_create(
            [
                AuditoriaAtividade(
                    tipo=AuditoriaAtividade.TIPO_AUDITORIA,
                    nome=f"PERF-AUD-{i}",
                    cliente=f"Cliente {i % 20:02d}",
                    workflow=f"Workflow {i % 10:02d}",
                    status=statuses[i % len(statuses)],
                    data_recepcao=received,
                    created_by=self.user,
                )
                for i in range(existing, target_activities_per_dashboard)
            ],
            batch_size=1000,
        )
        contest_activities = AuditoriaAtividade.objects.bulk_create(
            [
                AuditoriaAtividade(
                    tipo=AuditoriaAtividade.TIPO_CONTESTACAO,
                    nome=f"PERF-CON-{i}",
                    cliente=f"Cliente {i % 20:02d}",
                    workflow=f"Workflow {i % 10:02d}",
                    status=statuses[i % len(statuses)],
                    data_recepcao=received,
                    total_protocolos=children_per_activity,
                    created_by=self.user,
                )
                for i in range(existing, target_activities_per_dashboard)
            ],
            batch_size=1000,
        )

        pending_rows: list[QualidadePendenteAuditoriaFalha] = []
        treated_rows: list[AuditoriaFalhaCadastro] = []
        for activity in audit_activities:
            for child in range(children_per_activity):
                common = {
                    "atividade": activity,
                    "protocolo": f"AUD-{activity.pk}-{child}",
                    "tipo_falha": "Colaborador" if child % 2 else "Automatico",
                    "usuario": f"user-{child}",
                    "created_by": self.user,
                }
                if child % 2:
                    treated_rows.append(
                        AuditoriaFalhaCadastro(
                            **common,
                            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
                            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
                            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
                        )
                    )
                else:
                    pending_rows.append(QualidadePendenteAuditoriaFalha(**common))
        QualidadePendenteAuditoriaFalha.objects.bulk_create(pending_rows, batch_size=1000)
        AuditoriaFalhaCadastro.objects.bulk_create(treated_rows, batch_size=1000)

        protocol_rows: list[AuditoriaAtividadeProtocolo] = []
        for activity in contest_activities:
            for child in range(children_per_activity):
                protocol_rows.append(
                    AuditoriaAtividadeProtocolo(
                        atividade=activity,
                        protocolo=f"CON-{activity.pk}-{child}",
                        excel_row=child + 1,
                        status=(
                            AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
                            if child % 2
                            else AuditoriaAtividadeProtocolo.STATUS_PENDENTE
                        ),
                        situacao=(
                            AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE
                            if child % 3 == 0
                            else AuditoriaAtividadeProtocolo.SITUACAO_IMPROCEDENTE
                        ),
                    )
                )
        AuditoriaAtividadeProtocolo.objects.bulk_create(protocol_rows, batch_size=1000)
        AuditoriaControleRegistro.objects.bulk_create(
            [
                AuditoriaControleRegistro(
                    tipo=(
                        AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA
                        if i % 3 == 0
                        else AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA
                        if i % 3 == 1
                        else AuditoriaControleRegistro.TIPO_SOLICITACOES_IDAS_BIO
                    ),
                    situacao="Finalizada" if i % 2 else "Nova",
                    dados={
                        "data_abertura": (date.today() - timedelta(days=2)).isoformat(),
                        "data_conclusao": (date.today() - timedelta(days=1)).isoformat(),
                    },
                    created_by=self.user,
                )
                for i in range(existing, target_activities_per_dashboard)
            ],
            batch_size=1000,
        )

    def _measure_endpoint(self, path: str, *, repeats: int = 3) -> dict[str, Any]:
        # One unrecorded request warms PostgreSQL and Python code paths consistently.
        warmup = self.client.get(path)
        self.assertEqual(warmup.status_code, 200, warmup.data)

        samples: list[dict[str, Any]] = []
        captured_queries: list[dict[str, str]] = []
        captured_recorder: _QueryRecorder | None = None
        response_data: Any = None
        for sample_index in range(repeats):
            recorder = _QueryRecorder()
            cpu_start = time.process_time()
            wall_start = time.perf_counter()
            with connection.execute_wrapper(recorder):
                response = self.client.get(path)
            wall_ms = (time.perf_counter() - wall_start) * 1000
            cpu_ms = (time.process_time() - cpu_start) * 1000
            self.assertEqual(response.status_code, 200, response.data)
            if sample_index == 0:
                captured_recorder = recorder
                response_data = response.data
            samples.append(
                {
                    "wall_ms": round(wall_ms, 3),
                    "cpu_ms": round(cpu_ms, 3),
                    "query_count": recorder.count,
                    "sql_ms": round(recorder.sql_ms, 3),
                }
            )

        # Memory is measured in a separate request so tracemalloc overhead does not
        # contaminate endpoint wall/CPU timings.
        tracemalloc.start()
        memory_response = self.client.get(path)
        _current, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(memory_response.status_code, 200, memory_response.data)

        fingerprints = captured_recorder.fingerprints if captured_recorder else Counter()
        repeats_found = [
            {"count": count, "fingerprint": fingerprint}
            for fingerprint, count in fingerprints.most_common()
            if count > 1
        ]
        return {
            "samples": samples,
            "median": {
                key: round(statistics.median(sample[key] for sample in samples), 3)
                for key in ("wall_ms", "cpu_ms", "query_count", "sql_ms")
            },
            "python_peak_bytes": peak_bytes,
            "repeated_queries": repeats_found,
            "query_fingerprints": [
                {"count": count, "fingerprint": fingerprint}
                for fingerprint, count in fingerprints.most_common()
            ],
            "response_summary": response_data.get("entrega", {}) if response_data else {},
            "plans": self._explain_slowest(
                captured_recorder.slowest if captured_recorder else []
            ),
        }

    def _explain_slowest(self, queries: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
        candidates: list[tuple[float, str, Any]] = []
        seen: set[str] = set()
        for query in queries:
            sql = _select_from_logged_sql(query["sql"])
            if not sql:
                continue
            fingerprint = _query_fingerprint(sql)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            candidates.append((float(query["elapsed_ms"]), sql, query.get("params")))
            if len(candidates) >= limit:
                break

        plans: list[dict[str, Any]] = []
        for logged_ms, sql, params in candidates:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"EXPLAIN (ANALYZE true, BUFFERS true, FORMAT JSON) {sql}",
                        params,
                    )
                    plan = cursor.fetchone()[0][0]
                plans.append(
                    {
                        "logged_ms": round(logged_ms, 3),
                        "fingerprint": _query_fingerprint(sql),
                        "plan": plan,
                    }
                )
            except Exception as exc:  # pragma: no cover - evidence fallback
                plans.append(
                    {
                        "logged_ms": round(logged_ms, 3),
                        "fingerprint": _query_fingerprint(sql),
                        "explain_error": str(exc),
                    }
                )
        return plans

    def _index_inventory(self) -> list[dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    indexname,
                    tablename,
                    indexdef,
                    pg_relation_size(quote_ident(indexname)::regclass) AS size_bytes
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = ANY(%s)
                ORDER BY tablename, indexname
                """,
                [list(TABLES)],
            )
            columns = [column.name for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _table_counts(self) -> dict[str, int]:
        with connection.cursor() as cursor:
            result = {}
            for table in TABLES:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                result[table] = cursor.fetchone()[0]
            return result

    def test_benchmark_dashboards_at_three_volumes(self):
        stage = os.environ.get("PPLID_PERF_STAGE", "unspecified")
        results: dict[str, Any] = {
            "stage": stage,
            "environment": {
                "database_name": connection.settings_dict["NAME"],
                "database_vendor": connection.vendor,
                "postgresql_version": connection.pg_version,
                "python": platform.python_version(),
                "django": django.get_version(),
                "platform": platform.platform(),
                "logical_cpu_count": os.cpu_count(),
                "memory_note": "tracemalloc mede apenas pico de alocacoes Python; RSS do processo/sistema indisponivel",
            },
            "methodology": {
                "warmups": 1,
                "measured_repeats": 3,
                "date_filter": "today-1 through today+1",
                "data": "synthetic disposable PostgreSQL test database",
            },
            "volumes": [],
        }
        start = (date.today() - timedelta(days=1)).isoformat()
        end = (date.today() + timedelta(days=1)).isoformat()
        for label, activities_per_dashboard, children_per_activity in VOLUMES:
            self._seed_until(activities_per_dashboard, children_per_activity)
            suffix = f"?start_date={start}&end_date={end}"
            results["volumes"].append(
                {
                    "label": label,
                    "activities_per_dashboard": activities_per_dashboard,
                    "children_per_activity": children_per_activity,
                    "table_counts": self._table_counts(),
                    "auditoria": self._measure_endpoint(
                        "/api/v1/qualidade/auditoria/dashboard/" + suffix
                    ),
                    "contestacao": self._measure_endpoint(
                        "/api/v1/qualidade/contestacao/dashboard/" + suffix
                    ),
                }
            )
        results["indexes"] = self._index_inventory()

        output_value = os.environ.get("PPLID_PERF_OUTPUT", "").strip()
        if output_value:
            output = Path(output_value).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(results, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"PERFORMANCE_REPORT={output}")
        compact = [
            {
                "label": row["label"],
                "auditoria": row["auditoria"]["median"],
                "contestacao": row["contestacao"]["median"],
            }
            for row in results["volumes"]
        ]
        print("PERFORMANCE_SUMMARY=" + json.dumps(compact, default=str))
