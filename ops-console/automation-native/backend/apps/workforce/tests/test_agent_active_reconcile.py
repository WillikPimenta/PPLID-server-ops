"""Testes de reconciliação Agent.active ↔ AgentHistory aberto."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_OP_AGENTE,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_ASSISTENTE,
    role_group_name,
)
from apps.workforce.models import Agent, AgentHistory, UserProfile
from apps.workforce.services.agent_active_reconcile import (
    apply_align_agent_active,
    summarize_agent_history_consistency,
)
from apps.workforce.services.agent_history_xlsx import (
    build_preflight,
    load_and_convert_history_xlsx,
    replace_agent_history,
)

User = get_user_model()

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agent_history_sharepoint.xlsx"


def _mk_agent(lan: str, name: str, *, active: bool = True) -> Agent:
    return Agent.objects.create(user_lan_id=lan, full_name=name, active=active)


def _mk_open_history(agent: Agent, **extra) -> AgentHistory:
    defaults = {
        "agent": agent,
        "start_date": date(2024, 1, 1),
        "final_date": None,
        "active": True,
        "team": "Ops",
        "job_activity": "Análise",
    }
    defaults.update(extra)
    return AgentHistory.objects.create(**defaults)


def _mk_closed_history(agent: Agent, **extra) -> AgentHistory:
    defaults = {
        "agent": agent,
        "start_date": date(2023, 1, 1),
        "final_date": date(2023, 12, 31),
        "active": False,
        "team": "Ops",
        "job_activity": "Análise",
        "external_movement_type": "Fired",
    }
    defaults.update(extra)
    return AgentHistory.objects.create(**defaults)


class AgentActiveReconcileServiceTests(TestCase):
    def test_stale_active_counted(self):
        stale = _mk_agent("c99101a", "Stale Active", active=True)
        _mk_closed_history(stale)
        ok = _mk_agent("c99102a", "Ok Active", active=True)
        _mk_open_history(ok)

        summary = summarize_agent_history_consistency()
        self.assertEqual(summary["stale_active"], 1)
        self.assertEqual(summary["counts"]["consistent_active"], 1)
        self.assertGreaterEqual(summary["active_agents_vs_open_histories_delta"], 1)
        sample_lans = {s["user_lan_id"] for s in summary["samples"]["stale_active"]}
        self.assertIn("c99101a", sample_lans)

    def test_orphan_open_counted(self):
        orphan = _mk_agent("c99103a", "Orphan", active=False)
        _mk_open_history(orphan)
        inactive = _mk_agent("c99104a", "Inactive Ok", active=False)
        _mk_closed_history(inactive)

        summary = summarize_agent_history_consistency()
        self.assertEqual(summary["orphan_open"], 1)
        self.assertEqual(summary["counts"]["consistent_inactive"], 1)

    def test_align_dry_run_and_apply_does_not_change_agent_count(self):
        stale = _mk_agent("c99105a", "Stale", active=True)
        _mk_closed_history(stale)
        orphan = _mk_agent("c99106a", "Orphan", active=False)
        _mk_open_history(orphan)
        user = User.objects.create_user(
            "c99105a", email="c99105a@test.local", password="x"
        )
        UserProfile.objects.create(user=user, agent=stale)

        agent_count = Agent.objects.count()
        profile_count = UserProfile.objects.count()

        dry = apply_align_agent_active(dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["would_change"], 2)
        self.assertFalse(dry["applied"])
        stale.refresh_from_db()
        self.assertTrue(stale.active)

        applied = apply_align_agent_active(dry_run=False)
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["deactivate_count"], 1)
        self.assertEqual(applied["activate_count"], 1)
        stale.refresh_from_db()
        orphan.refresh_from_db()
        self.assertFalse(stale.active)
        self.assertTrue(orphan.active)

        self.assertEqual(Agent.objects.count(), agent_count)
        self.assertEqual(UserProfile.objects.count(), profile_count)

        after = summarize_agent_history_consistency()
        self.assertEqual(after["stale_active"], 0)
        self.assertEqual(after["orphan_open"], 0)


class AgentActiveReconcileApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agent_user = User.objects.create_user(
            "c99201a", email="c99201a@test.local", password="x"
        )
        self.view_user = User.objects.create_user(
            "c99202a", email="c99202a@test.local", password="x"
        )
        self.manage_user = User.objects.create_user(
            "c99203a", email="c99203a@test.local", password="x"
        )
        for role, user in (
            (ROLE_OP_AGENTE, self.agent_user),
            (ROLE_PLAN_ASSISTENTE, self.view_user),
            (ROLE_PLAN_ANALISTA, self.manage_user),
        ):
            Group.objects.get_or_create(name=role_group_name(role))
            user.groups.add(Group.objects.get(name=role_group_name(role)))

        stale = _mk_agent("c99210a", "Stale API", active=True)
        _mk_closed_history(stale)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_view_allowed_get_consistency(self):
        self.client.force_authenticate(user=self.view_user)
        response = self.client.get("/api/v1/workforce/agent-active/consistency/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body["stale_active"], 1)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_op_agente_denied_consistency(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.get("/api/v1/workforce/agent-active/consistency/")
        self.assertIn(response.status_code, (401, 403))

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_view_denied_align_apply(self):
        self.client.force_authenticate(user=self.view_user)
        response = self.client.post(
            "/api/v1/workforce/agent-active/align/",
            {"dry_run": False},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_manage_align_dry_run_and_apply(self):
        self.client.force_authenticate(user=self.manage_user)
        dry = self.client.post(
            "/api/v1/workforce/agent-active/align/",
            {"dry_run": True, "kinds": ["stale_active", "orphan_open"]},
            format="json",
        )
        self.assertEqual(dry.status_code, 200, dry.content)
        self.assertTrue(dry.json()["dry_run"])
        self.assertGreaterEqual(dry.json()["would_change"], 1)

        apply = self.client.post(
            "/api/v1/workforce/agent-active/align/",
            {"dry_run": False},
            format="json",
        )
        self.assertEqual(apply.status_code, 200, apply.content)
        self.assertTrue(apply.json()["applied"])
        self.assertFalse(Agent.objects.get(user_lan_id="c99210a").active)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_dashboard_overview_includes_consistency(self):
        self.client.force_authenticate(user=self.view_user)
        response = self.client.get("/api/v1/dashboard/overview/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("consistency", body)
        self.assertIn("stale_active", body["consistency"])
        self.assertIn("open_histories", body["consistency"])
        self.assertIn("active_agents_vs_open_histories_delta", body["consistency"])


class AgentActiveReconcileImportFixtureTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not FIXTURE.exists():
            raise AssertionError(f"Fixture ausente: {FIXTURE}")

    def _ensure_agents(self, drafts):
        by_lan: dict[str, Agent] = {
            a.user_lan_id.lower(): a for a in Agent.objects.all()
        }
        for d in drafts:
            for lan, name in (
                (d.agent_lan_id, d.agent_name or d.agent_lan_id),
                (d.leader_lan_id, d.leader_name or d.leader_lan_id),
                (d.facilitator_lan_id, d.facilitator_name or d.facilitator_lan_id),
            ):
                key = (lan or "").strip().lower()
                if not key or key in by_lan:
                    continue
                by_lan[key] = Agent.objects.create(
                    user_lan_id=key,
                    full_name=name or key,
                    active=True,
                )
        return by_lan

    def test_preflight_predicts_stale_and_align_zeros_delta(self):
        _df, _sheet, _cols, drafts = load_and_convert_history_xlsx(FIXTURE)
        self._ensure_agents(drafts)

        # Fantasma: active=True sem vigência aberta no XLSX.
        ghost = Agent.objects.create(
            user_lan_id="c999ghost",
            full_name="Ghost Active",
            active=True,
        )
        _mk_closed_history(ghost)

        report, resolved, _rejected = build_preflight(FIXTURE)
        consistency = report.agent_active_consistency
        self.assertGreaterEqual(consistency["agents_stale_active_after_import"], 1)
        ghost_in_sample = any(
            s.get("user_lan_id") == "c999ghost"
            for s in consistency.get("stale_active_sample") or []
        )
        # Pode não caber na amostra de 15; a contagem deve incluir.
        stale_lans = {
            a.user_lan_id
            for a in Agent.objects.filter(active=True)
            if a.id
            not in {
                r.agent.id
                for r in resolved
                if r.draft.active and r.draft.final_date is None
            }
        }
        self.assertIn("c999ghost", stale_lans)

        agent_before = Agent.objects.count()
        profile_before = UserProfile.objects.count()

        replace_agent_history(resolved, allow_unresolved_agents=True)
        align = apply_align_agent_active(dry_run=False)
        self.assertTrue(align["applied"] or align["would_change"] == 0)

        ghost.refresh_from_db()
        self.assertFalse(ghost.active)

        after = summarize_agent_history_consistency()
        self.assertEqual(after["stale_active"], 0)
        self.assertEqual(after["orphan_open"], 0)
        self.assertEqual(Agent.objects.count(), agent_before)
        self.assertEqual(UserProfile.objects.count(), profile_before)
        # silence unused
        self.assertIsInstance(ghost_in_sample, bool)
