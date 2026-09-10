from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.access.constants import ROLE_PLAN_ASSISTENTE, ROLE_PLAN_GERENCIA, role_group_name
from apps.planejamento_demandas.models import JiraDemanda, JiraDemandaSyncRun
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True)
class JiraDemandaApiTests(APITestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(
            username="dj_view", password="x", email="dj_view@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_GERENCIA))
        self.viewer.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_GERENCIA)))

        self.denied = User.objects.create_user(
            username="dj_denied", password="x", email="dj_denied@example.com"
        )

        JiraDemanda.objects.create(
            issue_key="PPLID-100",
            project_key="PPLID",
            summary="Ajuste headcount desligamento",
            status_name="Em andamento",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            categoria=JiraDemanda.CATEGORIA_HEADCOUNT,
            portal_path="/planejamento/megazord/headcount",
        )

    def test_status_kind_filters(self):
        JiraDemanda.objects.create(
            issue_key="PPLID-300",
            project_key="PPLID",
            summary="Done issue",
            status_name="Resolvida",
            status_kind=JiraDemanda.STATUS_KIND_DONE,
            is_open=False,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-301",
            project_key="PPLID",
            summary="Cancelled issue",
            status_name="Cancelada",
            status_kind=JiraDemanda.STATUS_KIND_CANCELLED,
            is_open=False,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"status": "open"})
        self.assertEqual(r.data["count"], 1)
        r = self.client.get(url, {"status": "done"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-300")
        r = self.client.get(url, {"status": "cancelled"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-301")

    def test_facets_includes_status_counts(self):
        JiraDemanda.objects.create(
            issue_key="PPLID-302",
            project_key="PPLID",
            summary="Cancelled",
            status_name="Cancelada",
            status_kind=JiraDemanda.STATUS_KIND_CANCELLED,
            is_open=False,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-facets")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("cancelled", r.data)
        self.assertIn("done", r.data)
        self.assertIn("primary_project", r.data)

    def test_list_ok(self):
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-100")

    def test_facets_ok(self):
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-facets")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["open"], 1)

    def test_denied_without_permission(self):
        self.client.force_authenticate(user=self.denied)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_team_queue_filter(self):
        JiraDemanda.objects.create(
            issue_key="PPLID-200",
            project_key="PPLID",
            summary="Team issue",
            status_name="Open",
            is_open=True,
            assignee_username="C93233A",
            reporter_username="C93233A",
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-201",
            project_key="PPLID",
            summary="Other issue",
            status_name="Open",
            is_open=True,
            assignee_username="OTHER",
            in_team_queue=False,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"queue": "team"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-200")

    def test_team_roster_ok(self):
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-team-roster")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("members", r.data)

    def test_sync_starts_async(self):
        from unittest.mock import patch

        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-sync")
        with patch(
            "apps.planejamento_demandas.services.sync._sync_credentials",
            return_value=object(),
        ), patch(
            "apps.planejamento_demandas.views.schedule_sync_run",
            return_value=True,
        ):
            r = self.client.post(url)
        self.assertEqual(r.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(r.data["status"], JiraDemandaSyncRun.STATUS_QUEUED)

    def test_sync_run_detail(self):
        run = JiraDemandaSyncRun.objects.create(
            status=JiraDemandaSyncRun.STATUS_SUCCESS,
            projects="PPLID",
            total_fetched=10,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-sync-detail", args=[run.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["id"], run.pk)

    def test_cancel_running_sync(self):
        run = JiraDemandaSyncRun.objects.create(
            status=JiraDemandaSyncRun.STATUS_RUNNING,
            heartbeat_at=timezone.now(),
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-sync-cancel", args=[run.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run.refresh_from_db()
        self.assertIsNotNone(run.cancel_requested_at)

    def test_history_reconciles_stale_running_sync(self):
        old = timezone.now() - timezone.timedelta(minutes=5)
        run = JiraDemandaSyncRun.objects.create(
            status=JiraDemandaSyncRun.STATUS_RUNNING,
            heartbeat_at=old,
        )
        JiraDemandaSyncRun.objects.filter(pk=run.pk).update(started_at=old)
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get(reverse("planejamento-demandas-jira-sync-historico"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        run.refresh_from_db()
        self.assertEqual(run.status, JiraDemandaSyncRun.STATUS_INTERRUPTED)
        self.assertEqual(run.error_code, "stale_heartbeat")

    def test_list_includes_queue_metrics(self):
        from django.utils import timezone as tz

        JiraDemanda.objects.create(
            issue_key="PPLID-400",
            project_key="PPLID",
            summary="Old open",
            status_name="Em andamento",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            created_at_jira=tz.now() - tz.timedelta(days=10),
            updated_at_jira=tz.now() - tz.timedelta(days=8),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"status": "open", "q": "PPLID-400"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["count"], 1)
        item = r.data["results"][0]
        self.assertEqual(item["queue_age_days"], 10)
        self.assertIn("Na fila", item["queue_age_label"])
        self.assertIn("stale", item["staleness"])

    def test_stale_days_filter(self):
        from django.utils import timezone as tz

        JiraDemanda.objects.create(
            issue_key="PPLID-401",
            project_key="PPLID",
            summary="Stale",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            updated_at_jira=tz.now() - tz.timedelta(days=10),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-402",
            project_key="PPLID",
            summary="Fresh",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            updated_at_jira=tz.now() - tz.timedelta(days=1),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"stale_days": "7"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-401")

    def test_created_within_filter(self):
        from django.utils import timezone as tz

        JiraDemanda.objects.create(
            issue_key="PPLID-601",
            project_key="PPLID",
            summary="Recent",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            created_at_jira=tz.now() - tz.timedelta(days=2),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-602",
            project_key="PPLID",
            summary="Old",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            created_at_jira=tz.now() - tz.timedelta(days=20),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"created_within": "7"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-601")

    def test_sort_created(self):
        from django.utils import timezone as tz

        JiraDemanda.objects.create(
            issue_key="PPLID-701",
            project_key="PPLID",
            summary="Older",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            created_at_jira=tz.now() - tz.timedelta(days=5),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-702",
            project_key="PPLID",
            summary="Newer",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            created_at_jira=tz.now() - tz.timedelta(days=1),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"sort": "created", "q": "PPLID-70"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        keys = [row["issue_key"] for row in r.data["results"]]
        self.assertEqual(keys, ["PPLID-702", "PPLID-701"])

    def test_kanban_grouping(self):
        from django.utils import timezone as tz

        JiraDemanda.objects.create(
            issue_key="PPLID-801",
            project_key="PPLID",
            summary="Andamento",
            status_name="Em andamento",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            updated_at_jira=tz.now(),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-802",
            project_key="PPLID",
            summary="Pausada",
            status_name="Pausado",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            updated_at_jira=tz.now(),
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-kanban")
        r = self.client.get(url, {"status": "open", "q": "PPLID-80"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["total"], 2)
        names = [col["status_name"] for col in r.data["columns"]]
        self.assertEqual(names, ["Em andamento", "Pausado"])
        self.assertEqual(r.data["columns"][0]["items"][0]["issue_key"], "PPLID-801")

    def test_ownership_team_assigned_filter(self):
        from apps.planejamento_demandas.services.projects import jira_demandas_team_lan_ids

        lan = jira_demandas_team_lan_ids()[0]
        JiraDemanda.objects.create(
            issue_key="PPLID-501",
            project_key="PPLID",
            summary="Team assignee",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            assignee_username=lan,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-502",
            project_key="PPLID",
            summary="External assignee",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            assignee_username="EXTERNAL",
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"queue": "team", "ownership": "team_assigned", "status": "active"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-501")

    def test_active_status_excludes_inactive_open(self):
        JiraDemanda.objects.create(
            issue_key="PPLID-600",
            project_key="PPLID",
            summary="Active work",
            status_name="Em andamento",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-601",
            project_key="PPLID",
            summary="Paused",
            status_name="Pausado",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-602",
            project_key="PPLID",
            summary="Waiting",
            status_name="Aguardando informações",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"queue": "team", "status": "active"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-600")
        r = self.client.get(url, {"queue": "team", "status": "open"})
        self.assertEqual(r.data["count"], 3)

    def test_search_with_q_includes_closed_even_with_active_status(self):
        JiraDemanda.objects.create(
            issue_key="PPLID-650",
            project_key="PPLID",
            summary="Still open",
            status_name="Em andamento",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-651",
            project_key="PPLID",
            summary="Resolved item",
            status_name="Resolvida",
            status_kind=JiraDemanda.STATUS_KIND_DONE,
            is_open=False,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"queue": "team", "status": "active"})
        self.assertEqual(r.data["count"], 1)
        r = self.client.get(url, {"queue": "team", "status": "active", "q": "PPLID-651"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-651")

    def test_facets_chip_counts_ignore_ownership_filter(self):
        from apps.planejamento_demandas.services.projects import jira_demandas_team_lan_ids

        lan = jira_demandas_team_lan_ids()[0]
        JiraDemanda.objects.create(
            issue_key="PPLID-610",
            project_key="PPLID",
            summary="Team assignee",
            status_name="Em andamento",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            assignee_username=lan,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-611",
            project_key="PPLID",
            summary="Unassigned",
            status_name="Em andamento",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            assignee_username="",
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-facets")
        r = self.client.get(url, {"queue": "team", "status": "active", "ownership": "team_assigned"})
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["team_assigned_open"], 1)
        self.assertEqual(r.data["unassigned_open"], 1)

    def test_member_role_assignee_filter(self):
        JiraDemanda.objects.create(
            issue_key="PPLID-503",
            project_key="PPLID",
            summary="Assignee only",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            assignee_username="C93233A",
            reporter_username="OTHER",
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        JiraDemanda.objects.create(
            issue_key="PPLID-504",
            project_key="PPLID",
            summary="Reporter only",
            status_name="Open",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            assignee_username="OTHER",
            reporter_username="C93233A",
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(
            url,
            {"team_member": "C93233A", "member_role": "assignee", "status": "open"},
        )
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["issue_key"], "PPLID-503")

    def test_comment_endpoint(self):
        from unittest.mock import patch

        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-comment", args=["PPLID-100"])
        with patch(
            "apps.planejamento_demandas.views.comment_demanda",
            return_value={"ok": True, "comment_id": "99"},
        ), patch(
            "apps.planejamento_demandas.views.list_demanda_comments",
            return_value=[{"id": "99", "author": "Test", "created": "", "body": "Retorno"}],
        ):
            r = self.client.post(url, {"body": "Retorno ao solicitante"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["comments"][0]["body"], "Retorno")

    def test_inactive_status_filter(self):
        JiraDemanda.objects.create(
            issue_key="PPLID-700",
            project_key="PPLID",
            summary="Paused",
            status_name="Pausado",
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            is_open=True,
            in_team_queue=True,
            categoria=JiraDemanda.CATEGORIA_OUTRO,
        )
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-list")
        r = self.client.get(url, {"status": "inactive", "q": "PPLID-700"})
        self.assertEqual(r.data["count"], 1)

    def test_export_csv(self):
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-export-csv")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("text/csv", r["Content-Type"])
        self.assertIn("PPLID-100", r.content.decode("utf-8"))

    def test_config_includes_user_lan(self):
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-config")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("current_user_lan_id", r.data)

    @override_settings(JIRA_BASE_URL="https://jira.example", JIRA_API_TOKEN="")
    def test_config_allows_user_token_without_service_token(self):
        agent = Agent.objects.create(
            full_name="Viewer Agent",
            user_lan_id="dj_view",
            email="dj_view@example.com",
            jira_api_token="user-pat",
        )
        UserProfile.objects.create(user=self.viewer, agent=agent)
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-config")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.data["service_token_configured"])
        self.assertTrue(r.data["user_token_configured"])
        self.assertTrue(r.data["can_sync"])

    @override_settings(JIRA_BASE_URL="https://jira.example", JIRA_API_TOKEN="service-pat")
    def test_config_requires_user_token_not_service_token(self):
        self.client.force_authenticate(user=self.viewer)
        url = reverse("planejamento-demandas-jira-config")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data["service_token_configured"])
        self.assertFalse(r.data["user_token_configured"])
        self.assertFalse(r.data["can_sync"])

    def test_sync_denied_for_assistente(self):
        assistente = User.objects.create_user(
            username="dj_assist", password="x", email="dj_assist@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ASSISTENTE))
        assistente.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ASSISTENTE)))
        self.client.force_authenticate(user=assistente)
        url = reverse("planejamento-demandas-jira-sync")
        r = self.client.post(url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
