from unittest.mock import patch

from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.planejamento_demandas.models import JiraDemanda, JiraDemandaSyncRun
from apps.planejamento_demandas.services.sync import _sync_jql, _upsert_demanda, create_sync_run
from apps.planejamento_demandas.services.sync_runner import (
    reconcile_stale_runs,
    request_sync_cancel,
)
from apps.workforce.models import Agent, UserProfile


class JiraSyncControlTests(TestCase):
    def test_reconcile_stale_run_marks_interrupted(self):
        old = timezone.now() - timezone.timedelta(minutes=5)
        run = JiraDemandaSyncRun.objects.create(
            status=JiraDemandaSyncRun.STATUS_RUNNING,
            heartbeat_at=old,
        )
        JiraDemandaSyncRun.objects.filter(pk=run.pk).update(started_at=old)
        self.assertEqual(reconcile_stale_runs(), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, JiraDemandaSyncRun.STATUS_INTERRUPTED)
        self.assertIsNotNone(run.finished_at)

    def test_fresh_run_is_not_reconciled(self):
        run = JiraDemandaSyncRun.objects.create(
            status=JiraDemandaSyncRun.STATUS_RUNNING,
            heartbeat_at=timezone.now(),
        )
        self.assertEqual(reconcile_stale_runs(), 0)
        run.refresh_from_db()
        self.assertEqual(run.status, JiraDemandaSyncRun.STATUS_RUNNING)

    def test_cancel_queued_run_is_terminal_immediately(self):
        run = JiraDemandaSyncRun.objects.create(status=JiraDemandaSyncRun.STATUS_QUEUED)
        changed, accepted = request_sync_cancel(run.pk)
        self.assertTrue(accepted)
        self.assertEqual(changed.status, JiraDemandaSyncRun.STATUS_CANCELLED)

    @override_settings(JIRA_BASE_URL="https://jira.example", JIRA_API_TOKEN="token")
    def test_create_run_uses_incremental_checkpoint_after_success(self):
        previous = JiraDemandaSyncRun.objects.create(status=JiraDemandaSyncRun.STATUS_SUCCESS)
        run = create_sync_run(project_keys=["PPLID"])
        self.assertEqual(run.mode, JiraDemandaSyncRun.MODE_INCREMENTAL)
        self.assertIsNotNone(run.checkpoint_at)
        self.assertIn("updated >= -", run.jql)
        self.assertLess(run.checkpoint_at, previous.started_at)

    @override_settings(JIRA_BASE_URL="https://jira.example")
    def test_create_run_accepts_user_token_without_service_token(self):
        user = get_user_model().objects.create_user(
            username="sync_user",
            password="x",
            email="sync_user@example.com",
        )
        agent = Agent.objects.create(
            full_name="Sync User",
            user_lan_id="sync_user",
            email="sync_user@example.com",
            jira_api_token="user-pat",
        )
        UserProfile.objects.create(user=user, agent=agent)
        run = create_sync_run(user=user, project_keys=["PPLID"])
        self.assertEqual(run.mode, JiraDemandaSyncRun.MODE_FULL)
        self.assertEqual(run.user_id, user.pk)

    def test_sync_jql_deduplicates_between_phases_and_updates_progress(self):
        parsed = {
            "issue_key": "PPLID-1",
            "project_key": "PPLID",
            "summary": "Teste",
            "status_name": "Open",
        }
        issue = {"key": "PPLID-1", "fields": {}}
        run = JiraDemandaSyncRun.objects.create(
            status=JiraDemandaSyncRun.STATUS_RUNNING,
            heartbeat_at=timezone.now(),
            phase_count=2,
        )
        seen: set[str] = set()
        with patch(
            "apps.planejamento_demandas.services.sync.search_issues",
            return_value={"issues": [issue], "total": 1},
        ), patch(
            "apps.planejamento_demandas.services.sync.parse_issue",
            return_value=parsed,
        ):
            first = _sync_jql("jql", run=run, phase="GERAL", phase_index=1, phase_count=2, seen_keys=seen)
            second = _sync_jql(
                "jql",
                run=run,
                phase="TIME",
                phase_index=2,
                phase_count=2,
                seen_keys=seen,
                base_fetched=first[0],
                base_created=first[1],
                base_updated=first[2],
                base_duplicates=first[3],
                base_pages=first[4],
            )
        self.assertEqual(first[0], 1)
        self.assertEqual(second[0], 0)
        self.assertEqual(second[3], 1)
        self.assertEqual(JiraDemanda.objects.filter(issue_key="PPLID-1").count(), 1)
        run.refresh_from_db()
        self.assertEqual(run.progress_percent, 100)
        self.assertEqual(run.duplicate_count, 1)

    def test_upsert_demanda_recovers_from_integrity_error(self):
        parsed = {
            "issue_key": "PPLID-99",
            "project_key": "PPLID",
            "project_name": "",
            "summary": "Concorrência",
            "description_excerpt": "",
            "status_name": "Open",
            "status_kind": JiraDemanda.STATUS_KIND_OPEN,
            "is_open": True,
            "assignee_display": "",
            "assignee_username": "",
            "reporter_display": "",
            "reporter_username": "",
            "in_team_queue": False,
            "priority_name": "",
            "issue_type": "",
            "labels": [],
            "components": [],
            "categoria": JiraDemanda.CATEGORIA_OUTRO,
            "portal_path": "",
            "jira_url": "",
            "created_at_jira": None,
            "updated_at_jira": None,
        }
        JiraDemanda.objects.create(issue_key="PPLID-99", project_key="PPLID", summary="Existente", status_name="Open")
        real_update_or_create = JiraDemanda.objects.update_or_create

        def flaky_update_or_create(*args, **kwargs):
            if not getattr(flaky_update_or_create, "raised", False):
                flaky_update_or_create.raised = True
                raise IntegrityError("duplicate key")
            return real_update_or_create(*args, **kwargs)

        with patch.object(JiraDemanda.objects, "update_or_create", side_effect=flaky_update_or_create):
            self.assertFalse(_upsert_demanda(parsed))
        row = JiraDemanda.objects.get(issue_key="PPLID-99")
        self.assertEqual(row.summary, "Concorrência")
