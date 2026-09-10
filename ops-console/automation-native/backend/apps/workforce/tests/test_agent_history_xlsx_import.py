"""Testes do importador agent_history (conversor + replace + backup)."""

from __future__ import annotations

import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.workforce.excel_utils import (
    parse_decimal,
    quantize_productivity_discount,
)
from apps.workforce.models import Agent, AgentHistory, UserProfile
from apps.workforce.services.agent_history_xlsx import (
    convert_history_row,
    legacy_seed_scalar_fields,
    replace_agent_history,
    resolve_drafts,
    restore_backup,
    write_backup,
)

User = get_user_model()


def _row(**overrides):
    base = {
        "user_lan_id": "c90001a",
        "agent": "Agente Teste",
        "leader_lan_id": "c90002a",
        "leader": "Lider Teste",
        "facilitator_lan_id": "",
        "facilitator": "",
        "location": "Brasília",
        "team": "Operacional/Fraud",
        "job_title": "Agente Backoffice I",
        "job_activity": "Análise Visual",
        "journey": "08:00 - 14:00",
        "team_sector": "Fraud",
        "job_title_sector": "Operações",
        "job_title_activity": "Mesa",
        "journey_shift": "Manhã",
        "inss_type": "",
        "external_movement_type": "",
        "start_date": date(2024, 1, 1),
        "final_date": None,
        "productivity_discount": "00:25:00",
        "pcd": False,
        "jira": "",
        "formalization": "",
        "band": "",
        "active": True,
        "id": 5467,
    }
    base.update(overrides)
    return pd.Series(base)


class AgentHistoryConverterTests(TestCase):
    def test_productivity_discount_time_to_hours_quantized(self):
        raw = parse_decimal("00:25:00")
        self.assertEqual(raw, Decimal("0.4166666666666666666666666667"))
        self.assertEqual(quantize_productivity_discount(raw), Decimal("0.42"))

    def test_convert_matches_legacy_seed_scalars(self):
        series = _row()
        columns = {
            "agent_lan": "user_lan_id",
            "agent_name": "agent",
            "leader_lan": "leader_lan_id",
            "leader_name": "leader",
            "facilitator_lan": "facilitator_lan_id",
            "facilitator_name": "facilitator",
            "sharepoint_id": "id",
        }
        draft = convert_history_row(series, excel_row=2, columns=columns)
        legacy = legacy_seed_scalar_fields(series)

        self.assertTrue(draft.ok)
        self.assertEqual(draft.agent_lan_id, "c90001a")
        self.assertEqual(draft.sharepoint_item_id, "5467")
        for key, value in legacy.items():
            self.assertEqual(getattr(draft, key), value, msg=key)

    def test_active_none_is_false(self):
        draft = convert_history_row(
            _row(active=None),
            2,
            {
                "agent_lan": "user_lan_id",
                "agent_name": "agent",
                "leader_lan": "leader_lan_id",
                "leader_name": "leader",
                "facilitator_lan": "facilitator_lan_id",
                "facilitator_name": "facilitator",
                "sharepoint_id": "id",
            },
        )
        self.assertFalse(draft.active)

    def test_final_before_start_warns(self):
        draft = convert_history_row(
            _row(start_date=date(2024, 5, 1), final_date=date(2024, 1, 1)),
            2,
            {
                "agent_lan": "user_lan_id",
                "agent_name": "agent",
                "leader_lan": None,
                "leader_name": None,
                "facilitator_lan": None,
                "facilitator_name": None,
                "sharepoint_id": "id",
            },
        )
        self.assertTrue(any("final_date anterior" in w for w in draft.warnings))


class AgentHistoryReplaceTests(TestCase):
    def setUp(self):
        self.agent = Agent.objects.create(
            user_lan_id="c90001a",
            full_name="Agente Teste",
            active=True,
        )
        self.leader = Agent.objects.create(
            user_lan_id="c90002a",
            full_name="Lider Teste",
            active=True,
        )
        self.user = User.objects.create_user(username="c90001a", password="x")
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.existing = AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            start_date=date(2023, 1, 1),
            final_date=date(2023, 12, 31),
            team="Old Team",
            job_activity="Old",
            active=False,
            sharepoint_item_id="5467",
        )

    def test_replace_reuses_uuid_by_sharepoint_id_and_preserves_agents(self):
        columns = {
            "agent_lan": "user_lan_id",
            "agent_name": "agent",
            "leader_lan": "leader_lan_id",
            "leader_name": "leader",
            "facilitator_lan": "facilitator_lan_id",
            "facilitator_name": "facilitator",
            "sharepoint_id": "id",
        }
        draft = convert_history_row(_row(), 2, columns)
        resolved, rejected, extras = resolve_drafts([draft])
        self.assertEqual(rejected, [])
        self.assertEqual(extras.unresolved_agent_lans, [])
        self.assertEqual(resolved[0].reuse_id, self.existing.id)

        agent_count = Agent.objects.count()
        profile_count = UserProfile.objects.count()
        result = replace_agent_history(resolved)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["uuid_reused"], 1)
        self.assertEqual(Agent.objects.count(), agent_count)
        self.assertEqual(UserProfile.objects.count(), profile_count)
        self.assertEqual(User.objects.count(), 1)

        hist = AgentHistory.objects.get()
        self.assertEqual(hist.id, self.existing.id)
        self.assertEqual(hist.team, "Operacional/Fraud")
        self.assertEqual(hist.sharepoint_item_id, "5467")
        self.assertEqual(hist.productivity_discount, Decimal("0.42"))
        self.assertEqual(hist.leader_id, self.leader.id)

    def test_backup_and_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "backup.json"
            write_backup(backup)
            payload = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["rows"][0]["id"], str(self.existing.id))

            AgentHistory.objects.all().delete()
            self.assertEqual(AgentHistory.objects.count(), 0)

            restored = restore_backup(backup)
            self.assertEqual(restored, 1)
            hist = AgentHistory.objects.get()
            self.assertEqual(hist.id, self.existing.id)
            self.assertEqual(hist.team, "Old Team")
            self.assertEqual(Agent.objects.count(), 2)
            self.assertEqual(UserProfile.objects.count(), 1)

    def test_unresolved_agent_is_rejected(self):
        draft = convert_history_row(
            _row(user_lan_id="desconhecido"),
            2,
            {
                "agent_lan": "user_lan_id",
                "agent_name": "agent",
                "leader_lan": "leader_lan_id",
                "leader_name": "leader",
                "facilitator_lan": None,
                "facilitator_name": None,
                "sharepoint_id": "id",
            },
        )
        resolved, rejected, extras = resolve_drafts([draft])
        self.assertEqual(resolved, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("desconhecido", extras.unresolved_agent_lans)
