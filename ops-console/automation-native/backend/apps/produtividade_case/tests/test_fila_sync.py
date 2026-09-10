# -*- coding: utf-8 -*-
import json
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PROC_USUARIO, role_group_name
from apps.common.bot_db_sync_lanes import LANE_LOW
from apps.produtividade_case.models import CaseFilaAgg, CaseFilaSampleItem, CaseFilaSnapshot
from apps.produtividade_case.services.fila_sync import (
    serialize_painel,
    serialize_resumo,
    sync_fila_from_json,
)
from apps.produtividade_case.services.analitica_detail import serialize_fila_amostra
from apps.produtividade_case.services.mvp_metrics import serialize_fila_aging
from apps.produtividade_case.services.sync_runner import run_sync_with_audit
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from datetime import date

from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


class FilaSyncTests(TestCase):
    def test_sync_fila_from_json_creates_snapshot_and_aggs(self):
        path = Path(self._tmp_json())
        snap = sync_fila_from_json(path)
        self.assertTrue(snap.success)
        self.assertEqual(snap.total_abertos, 5)
        self.assertEqual(
            CaseFilaAgg.objects.filter(snapshot=snap, dimension="status").count(),
            1,
        )
        self.assertEqual(
            CaseFilaAgg.objects.filter(snapshot=snap, dimension="idade_bucket").count(),
            2,
        )
        self.assertEqual(CaseFilaSampleItem.objects.filter(snapshot=snap).count(), 2)
        item = CaseFilaSampleItem.objects.get(snapshot=snap, protocolo_id="oid-1")
        self.assertEqual(item.protocolo_origem, "ORIG-1")
        self.assertEqual(item.cliente_origem, "Cliente A")
        self.assertIsNotNone(item.cadastro_origem_at)
        resumo = serialize_resumo(snap)
        self.assertEqual(resumo["total_abertos"], 5)
        self.assertEqual(resumo["by_status"][0]["key"], "PENDING")
        amostra = serialize_fila_amostra(idade_bucket="0-1h")
        self.assertEqual(amostra["count"], 1)
        self.assertEqual(amostra["results"][0]["protocolo_id"], "oid-1")

        sorted_desc = serialize_fila_amostra(ordering="-protocolo_id")
        self.assertEqual(sorted_desc["ordering"], "-protocolo_id")
        self.assertEqual(len(sorted_desc["results"]), 2)

        by_q = serialize_fila_amostra(q="oid-2")
        self.assertEqual(by_q["count"], 1)
        self.assertEqual(by_q["results"][0]["protocolo_id"], "oid-2")
        by_client = serialize_fila_amostra(q="Cliente B")
        self.assertEqual(by_client["count"], 1)
        self.assertEqual(by_client["results"][0]["cliente_origem"], "Cliente B")

        painel = serialize_painel(snap)
        self.assertEqual(painel["total_clientes"], 2)
        self.assertEqual(painel["total_workflows"], 2)
        self.assertEqual(painel["clientes"][0]["cliente"], "Cliente A")
        self.assertEqual(painel["clientes"][0]["workflows"][0]["pct"], 100.0)

    def test_latest_snapshot_tiebreak_prefers_newer_id(self):
        """Re-sync do mesmo JSON: captured_at igual — lista usa o snapshot com itens."""
        from apps.produtividade_case.services.fila_sync import latest_successful_snapshot

        path = self._tmp_json()
        snap1 = sync_fila_from_json(path)
        # segundo sync do mesmo arquivo (mesmo captured_at no payload)
        snap2 = sync_fila_from_json(path)
        latest = latest_successful_snapshot()
        self.assertEqual(snap1.pk, snap2.pk)
        self.assertEqual(latest.pk, snap2.pk)
        self.assertEqual(CaseFilaSnapshot.objects.count(), 1)
        amostra = serialize_fila_amostra()
        self.assertGreater(amostra["count"], 0)

    def test_painel_exposes_oldest_and_sla_contract(self):
        snap = sync_fila_from_json(self._tmp_json())
        painel = serialize_painel(snap)
        workflow = painel["clientes"][0]["workflows"][0]
        for field in (
            "oldest_protocol_id", "oldest_created_at", "oldest_age_seconds",
            "sla_limit_seconds", "sla_elapsed_seconds", "sla_remaining_seconds",
            "sla_pct", "sla_status",
        ):
            self.assertIn(field, workflow)
        self.assertEqual(workflow["oldest_protocol_id"], "ORIG-1")
        self.assertEqual(workflow["sla_status"], "unmapped")
        self.assertEqual(painel["total_sla_unmapped"], 2)

    def test_status_hides_absolute_source_and_exposes_truncation(self):
        path = Path(self._tmp_json())
        snap = sync_fila_from_json(path)
        resumo = serialize_resumo(snap)
        self.assertEqual(resumo["source_file"], path.name)
        self.assertEqual(resumo["items_count"], 2)
        self.assertTrue(resumo["items_truncated"])
        self.assertGreaterEqual(resumo["items_limit"], resumo["items_count"])

    def test_run_sync_with_audit_fila_aberta(self):
        path = self._tmp_json()
        ok, log, skipped = run_sync_with_audit(
            report_type="fila_aberta",
            path=path,
            force=True,
        )
        self.assertTrue(ok)
        self.assertFalse(skipped)
        self.assertTrue(log.success)
        self.assertEqual(CaseFilaSnapshot.objects.filter(success=True).count(), 1)

    def test_serialize_fila_aging_precomputed_and_safe_fallback(self):
        path = Path(self._tmp_json())
        snap = sync_fila_from_json(path)
        self.assertIsNotNone(snap.aging_count)
        self.assertGreaterEqual(snap.aging_count, 1)
        aging = serialize_fila_aging(snap)
        self.assertEqual(aging["aging_count"], snap.aging_count)
        self.assertIsNotNone(aging["aging_mediano_seconds"])

        # Snapshot sem pré-cálculo: fallback via values_list
        old = CaseFilaSnapshot.objects.create(
            captured_at=timezone.now(),
            total_abertos=1,
            success=True,
            aging_count=None,
        )
        CaseFilaSampleItem.objects.create(
            snapshot=old,
            protocolo_id="race-1",
            created_ts=timezone.now(),
            cadastro_origem_at=timezone.now(),
        )
        aging_fb = serialize_fila_aging(old)
        self.assertGreaterEqual(aging_fb["aging_count"], 1)

        # Race durante scan: não derruba o resumo
        with patch(
            "apps.produtividade_case.services.mvp_metrics._iter_fila_aging_timestamps",
            side_effect=CaseFilaSampleItem.DoesNotExist("race"),
        ):
            safe = serialize_fila_aging(old)
        self.assertEqual(safe["aging_count"], 0)
        self.assertIsNone(safe["aging_mediano_seconds"])

        resumo = serialize_resumo(snap)
        self.assertIn("aging_count", resumo)
        self.assertGreaterEqual(resumo["aging_count"], 1)

    def test_run_sync_uses_lane_low(self):
        path = self._tmp_json()
        with patch(
            "apps.common.bot_db_sync_gate.bot_db_sync_slot"
        ) as slot_mock:
            from contextlib import contextmanager

            @contextmanager
            def _cm(*_a, **_k):
                yield

            slot_mock.side_effect = lambda *a, **k: _cm()
            ok, log, skipped = run_sync_with_audit(
                report_type="fila_aberta",
                path=path,
                force=True,
            )
        self.assertTrue(ok)
        self.assertFalse(skipped)
        self.assertTrue(log.success)
        slot_mock.assert_called()
        kwargs = slot_mock.call_args.kwargs
        self.assertEqual(kwargs.get("lane"), LANE_LOW)

    def _tmp_json(self) -> str:
        import tempfile

        payload = {
            "captured_at": timezone.now().isoformat(),
            "total_abertos": 5,
            "duration_seconds": 0.12,
            "by_status": [{"key": "PENDING", "count": 5}],
            "by_idade_bucket": [
                {"key": "0-1h", "count": 2},
                {"key": "1-4h", "count": 3},
            ],
            "by_request_type": [{"key": "AUDIT", "count": 5}],
            "sample_items": [
                {
                    "protocolo_id": "oid-1",
                    "protocolo_origem": "ORIG-1",
                    "transaction_status": "PENDING",
                    "idade_bucket": "0-1h",
                    "created_ts": timezone.now().isoformat(),
                    "cadastro_origem_at": timezone.now().isoformat(),
                    "workflow_origem": "WF-A",
                    "cliente_origem": "Cliente A",
                },
                {
                    "protocolo_id": "oid-2",
                    "protocolo_origem": "ORIG-2",
                    "transaction_status": "PENDING",
                    "idade_bucket": "1-4h",
                    "created_ts": timezone.now().isoformat(),
                    "cadastro_origem_at": timezone.now().isoformat(),
                    "workflow_origem": "WF-B",
                    "cliente_origem": "Cliente B",
                },
            ],
        }
        fd, name = tempfile.mkstemp(suffix=".json")
        Path(name).write_text(json.dumps(payload), encoding="utf-8")
        import os

        os.close(fd)
        return name


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class CaseManagerApiRbacTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.op = self._user("c96001a", ROLE_OP_AGENTE)
        self.proc = self._user("c96002a", ROLE_PROC_USUARIO)
        sync_fila_from_json(self._write_json())

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

    def _write_json(self) -> Path:
        import tempfile

        payload = {
            "captured_at": timezone.now().isoformat(),
            "total_abertos": 1,
            "by_status": [{"key": "OPEN", "count": 1}],
            "by_idade_bucket": [{"key": "0-1h", "count": 1}],
            "by_request_type": [{"key": "AUDIT", "count": 1}],
        }
        fd, name = tempfile.mkstemp(suffix=".json")
        p = Path(name)
        p.write_text(json.dumps(payload), encoding="utf-8")
        import os

        os.close(fd)
        return p

    def test_status_and_resumo_allow(self):
        self.client.force_authenticate(user=self.op)
        st = self.client.get("/api/v1/case-manager/status/")
        self.assertEqual(st.status_code, 200)
        self.assertTrue(st.data["has_data"])
        self.assertEqual(st.data["total_abertos"], 1)

        re = self.client.get("/api/v1/case-manager/fila/resumo/")
        self.assertEqual(re.status_code, 200)
        self.assertEqual(re.data["by_status"][0]["key"], "OPEN")

        se = self.client.get("/api/v1/case-manager/fila/serie/?hours=24")
        self.assertEqual(se.status_code, 200)
        self.assertGreaterEqual(len(se.data["points"]), 1)

        am = self.client.get("/api/v1/case-manager/fila/amostra/")
        self.assertEqual(am.status_code, 200)

        painel = self.client.get("/api/v1/case-manager/fila/painel/")
        self.assertEqual(painel.status_code, 200)
        self.assertIn("clientes", painel.data)

    def test_status_deny(self):
        self.client.force_authenticate(user=self.proc)
        response = self.client.get("/api/v1/case-manager/status/")
        self.assertEqual(response.status_code, 403)
