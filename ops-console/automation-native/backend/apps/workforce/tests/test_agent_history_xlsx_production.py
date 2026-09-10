"""Testes de produção do import agent_history (XLSX SharePoint + API)."""

from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PLAN_ANALISTA, role_group_name
from apps.workforce.excel_utils import parse_decimal, quantize_productivity_discount
from apps.workforce.models import Agent, AgentHistory, UserProfile
from apps.workforce.services.agent_history_xlsx import (
    build_preflight,
    convert_history_row,
    detect_history_columns,
    legacy_seed_scalar_fields,
    load_and_convert_history_xlsx,
    replace_agent_history,
    resolve_drafts,
    restore_backup,
    write_backup,
)

User = get_user_model()

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agent_history_sharepoint.xlsx"


def _require_fixture():
    if not FIXTURE.exists():
        raise AssertionError(f"Fixture ausente: {FIXTURE}")


def _ensure_agents_for_drafts(drafts) -> dict[str, Agent]:
    lans: set[str] = set()
    for d in drafts:
        if d.agent_lan_id:
            lans.add(d.agent_lan_id)
        if d.leader_lan_id:
            lans.add(d.leader_lan_id)
        if d.facilitator_lan_id:
            lans.add(d.facilitator_lan_id)
    existing = {
        a.user_lan_id: a
        for a in Agent.objects.filter(user_lan_id__in=lans)
    }
    to_create = []
    for lan in sorted(lans):
        if lan in existing:
            continue
        to_create.append(
            Agent(user_lan_id=lan, full_name=f"Agent {lan}", active=True)
        )
    if to_create:
        Agent.objects.bulk_create(to_create, batch_size=500)
    return {
        a.user_lan_id: a
        for a in Agent.objects.filter(user_lan_id__in=lans)
    }


class RealXlsxConverterTests(TestCase):
    """Testes em cima do XLSX real (export SharePoint)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _require_fixture()

    def test_fixture_converts_all_rows_without_type_errors(self):
        _df, sheet, columns, drafts = load_and_convert_history_xlsx(FIXTURE)
        self.assertEqual(sheet, "query (3)")
        self.assertEqual(columns["agent_lan"], "user_lan_id")
        self.assertEqual(columns["leader_lan"], "leader_lan_id")
        self.assertEqual(columns["sharepoint_id"], "id")
        self.assertEqual(len(drafts), 2665)
        self.assertEqual(sum(1 for d in drafts if d.errors), 0)
        self.assertTrue(all(d.agent_lan_id for d in drafts))
        self.assertTrue(all(d.start_date for d in drafts))

    def test_sample_rows_match_legacy_seed_scalars(self):
        df, _sheet, columns, _drafts = load_and_convert_history_xlsx(FIXTURE)
        # Inclui linha com ProductivityDiscount tempo e Active None.
        indices = [0, 1, 50, 200, 1000, 2664]
        for idx in indices:
            row = df.iloc[idx]
            draft = convert_history_row(row, int(idx) + 2, columns)
            legacy = legacy_seed_scalar_fields(row)
            for key, value in legacy.items():
                self.assertEqual(getattr(draft, key), value, msg=f"row={idx} field={key}")

    def test_productivity_discount_from_fixture_quantized(self):
        df, _sheet, columns, drafts = load_and_convert_history_xlsx(FIXTURE)
        with_disc = [d for d in drafts if d.productivity_discount is not None]
        self.assertGreaterEqual(len(with_disc), 100)
        for d in with_disc[:30]:
            self.assertEqual(d.productivity_discount, d.productivity_discount.quantize(Decimal("0.01")))
        # Caso canônico do export: 00:25:00 → 0.42
        series = df.iloc[1]
        raw = parse_decimal(series.get("productivity_discount"))
        self.assertEqual(quantize_productivity_discount(raw), Decimal("0.42"))


class RealXlsxReplaceProductionGuardsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _require_fixture()

    def test_full_replace_preserves_agents_users_and_open_active_unique(self):
        _df, _sheet, _cols, drafts = load_and_convert_history_xlsx(FIXTURE)
        by_lan = _ensure_agents_for_drafts(drafts)

        # Usuário + profile “de produção” que não podem sumir.
        user = User.objects.create_user(
            username="c90001a-ahi",
            email="c90001a-ahi@test.local",
            password="x",
        )
        anchor = next(iter(by_lan.values()))
        UserProfile.objects.create(user=user, agent=anchor)

        # Histórico antigo para forçar replace.
        old = AgentHistory.objects.create(
            agent=anchor,
            start_date=date(2020, 1, 1),
            final_date=date(2020, 12, 31),
            team="LEGADO",
            active=False,
            sharepoint_item_id="legacy-1",
        )
        agent_before = Agent.objects.count()
        profile_before = UserProfile.objects.count()
        user_before = User.objects.count()

        resolved, rejected, extras = resolve_drafts(drafts, by_lan=by_lan)
        self.assertEqual(extras.unresolved_agent_lans, [])
        self.assertEqual(len(rejected), 0)
        self.assertEqual(len(resolved), 2665)

        result = replace_agent_history(resolved)
        self.assertEqual(result["inserted"], 2665)
        self.assertEqual(Agent.objects.count(), agent_before)
        self.assertEqual(UserProfile.objects.count(), profile_before)
        self.assertEqual(User.objects.count(), user_before)
        self.assertEqual(AgentHistory.objects.count(), 2665)
        self.assertFalse(AgentHistory.objects.filter(id=old.id).exists())

        # Constraint de negócio: no máx. 1 aberto por agente.
        from django.db.models import Count

        dupes = (
            AgentHistory.objects.filter(active=True, final_date__isnull=True)
            .values("agent_id")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
        )
        self.assertEqual(list(dupes), [])

        # SharePoint ID persistido na maioria das linhas.
        with_sp = AgentHistory.objects.exclude(sharepoint_item_id="").count()
        self.assertEqual(with_sp, 2665)

    def test_backup_restore_roundtrip_after_replace(self):
        _df, _sheet, _cols, drafts = load_and_convert_history_xlsx(FIXTURE)
        by_lan = _ensure_agents_for_drafts(drafts)
        # Subconjunto para teste mais rápido de backup (ainda usa o arquivo real).
        subset = drafts[:40]
        resolved, rejected, _extras = resolve_drafts(subset, by_lan=by_lan)
        self.assertEqual(rejected, [])
        replace_agent_history(resolved)
        self.assertEqual(AgentHistory.objects.count(), len(resolved))

        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "backup.json"
            write_backup(backup)
            AgentHistory.objects.all().delete()
            self.assertEqual(AgentHistory.objects.count(), 0)
            restored = restore_backup(backup)
            self.assertEqual(restored, len(resolved))
            self.assertEqual(AgentHistory.objects.count(), len(resolved))

    def test_transaction_rolls_back_on_failure(self):
        _df, _sheet, _cols, drafts = load_and_convert_history_xlsx(FIXTURE)
        by_lan = _ensure_agents_for_drafts(drafts)
        subset = drafts[:10]
        resolved, _, _ = resolve_drafts(subset, by_lan=by_lan)
        replace_agent_history(resolved)
        before_ids = set(AgentHistory.objects.values_list("id", flat=True))
        before_count = AgentHistory.objects.count()

        # Força falha após delete simulando objeto inválido (start_date None).
        broken = list(resolved)
        broken[0].draft.start_date = None  # type: ignore[assignment]
        with self.assertRaises(Exception):
            replace_agent_history(broken)

        self.assertEqual(AgentHistory.objects.count(), before_count)
        self.assertEqual(set(AgentHistory.objects.values_list("id", flat=True)), before_ids)


class AgentHistoryImportApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _require_fixture()

    def setUp(self):
        self.client = APIClient()
        self.agent_user = User.objects.create_user(
            "c94002a",
            email="c94002a-ahi@test.local",
            password="test123",
        )
        self.plan_user = User.objects.create_user(
            "c94003a",
            email="c94003a-ahi@test.local",
            password="test123",
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.agent_user.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))
        self.plan_user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))

        # Seed mínimo de agents para as primeiras linhas do fixture.
        _df, _sheet, _cols, drafts = load_and_convert_history_xlsx(FIXTURE)
        self.sample_drafts = drafts[:25]
        self.by_lan = _ensure_agents_for_drafts(self.sample_drafts)

    def _upload_bytes(self) -> SimpleUploadedFile:
        content = FIXTURE.read_bytes()
        return SimpleUploadedFile(
            "agent_history.xlsx",
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_op_agente_denied_preflight(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/workforce/agent-history/import/preflight/",
            {"file": self._upload_bytes()},
            format="multipart",
        )
        self.assertIn(response.status_code, (403, 401))

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_plan_analista_preflight_and_confirm_flow(self):
        # Garante agents para o arquivo inteiro (produção: já existiriam).
        _df, _sheet, _cols, drafts = load_and_convert_history_xlsx(FIXTURE)
        _ensure_agents_for_drafts(drafts)

        self.client.force_authenticate(user=self.plan_user)
        pre = self.client.post(
            "/api/v1/workforce/agent-history/import/preflight/",
            {"file": self._upload_bytes()},
            format="multipart",
        )
        self.assertEqual(pre.status_code, 200, pre.content)
        body = pre.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["preflight"]["rows_read"], 2665)
        self.assertEqual(body["preflight"]["rows_with_errors"], 0)
        import_id = body["import_id"]

        agent_before = Agent.objects.count()
        history_before = AgentHistory.objects.count()
        confirm = self.client.post(
            "/api/v1/workforce/agent-history/import/confirm/",
            {"import_id": import_id, "allow_partial": "false"},
            format="json",
        )
        self.assertEqual(confirm.status_code, 200, confirm.content)
        data = confirm.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["inserted"], 2665)
        self.assertEqual(Agent.objects.count(), agent_before)
        self.assertEqual(AgentHistory.objects.count(), 2665)

        backup_path = data["backup_path"]
        # Backup = estado ANTERIOR à carga (desfazer import).
        restore = self.client.post(
            "/api/v1/workforce/agent-history/import/restore/",
            {"backup_path": backup_path},
            format="json",
        )
        self.assertEqual(restore.status_code, 200, restore.content)
        self.assertEqual(restore.json()["restored"], history_before)
        self.assertEqual(AgentHistory.objects.count(), history_before)
        self.assertEqual(Agent.objects.count(), agent_before)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_confirm_blocks_when_unresolved_without_partial(self):
        # Sem criar agents → todas as linhas ficam sem resolução.
        Agent.objects.all().delete()
        self.client.force_authenticate(user=self.plan_user)
        pre = self.client.post(
            "/api/v1/workforce/agent-history/import/preflight/",
            {"file": self._upload_bytes()},
            format="multipart",
        )
        self.assertEqual(pre.status_code, 200)
        import_id = pre.json()["import_id"]
        self.assertTrue(pre.json()["blocking_unresolved_agents"])
        confirm = self.client.post(
            "/api/v1/workforce/agent-history/import/confirm/",
            {"import_id": import_id, "allow_partial": "false"},
            format="json",
        )
        self.assertEqual(confirm.status_code, 400)
        self.assertIn("não resolvidas", confirm.json()["message"].lower())

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_restore_rejects_path_outside_backups(self):
        self.client.force_authenticate(user=self.plan_user)
        response = self.client.post(
            "/api/v1/workforce/agent-history/import/restore/",
            {"backup_path": str(FIXTURE)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
