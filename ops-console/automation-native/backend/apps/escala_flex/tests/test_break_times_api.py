from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.escala_flex.models import BreakTime, Escala, ScheduleToday, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.workforce.models import Agent, AgentHistory


class BreakTimeApiTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=3, name="Deslogado", active=True, logged_in=False)
        self.leader = Agent.objects.create(
            user_lan_id="lead01", full_name="Líder", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="agt01", full_name="Agente Backoffice", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional BrFlow",
            job_title="Agente Backoffice I",
            job_activity="BrFlow",
            journey="08:00 - 17:00",
            start_date=date.today(),
            active=True,
        )
        self.other = Agent.objects.create(
            user_lan_id="mgr01", full_name="Gerente", active=True
        )
        AgentHistory.objects.create(
            agent=self.other,
            leader=self.leader,
            location="Brasília",
            team="Planejamento",
            job_title="Analista",
            job_activity="BrFlow",
            start_date=date.today(),
            active=True,
        )
        BreakTime.objects.create(
            agent_lan_id="agt01",
            week="12:00 - 13:00",
            weekend="11:00 - 12:00",
            active=True,
        )
        self.today = timezone.localdate()
        Escala.objects.create(
            agent=self.agent,
            leader=self.leader,
            data=self.today,
            horario="08:00 - 17:00",
            dia_escala="08:00 - 17:00",
        )
        ScheduleTodayService.build_for_date(self.today)
        ScheduleToday.objects.filter(agent=self.agent, date=self.today).update(
            work_schedule="08:00 - 14:00",
            overtime=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self._user("lead01"))

    def _user(self, username: str):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        return User.objects.create_user(username=username, password="test")

    def test_list_includes_only_backoffice_agents(self):
        response = self.client.get("/api/v1/escala-flex/break-times/")
        self.assertEqual(response.status_code, 200)
        lan_ids = {row["user_lan_id"] for row in response.data["results"]}
        self.assertEqual(lan_ids, {"agt01"})
        self.assertEqual(response.data["results"][0]["week"], "12:00")
        self.assertEqual(response.data["results"][0]["default_schedule"], "08:00 - 17:00")
        self.assertEqual(response.data["results"][0]["break_exit_min"], "09:00")
        self.assertEqual(response.data["results"][0]["break_exit_max"], "15:45")
        self.assertEqual(response.data["results"][0]["weekend_break_exit_min"], "09:00")
        self.assertEqual(response.data["results"][0]["weekend_break_exit_max"], "15:45")
        self.assertFalse(response.data["results"][0]["overtime"])

    def test_default_schedule_uses_escala_horario_when_journey_differs(self):
        AgentHistory.objects.filter(agent=self.agent).update(journey="CLT 8h")
        Escala.objects.filter(agent=self.agent, data=self.today).update(
            horario="17:00 - 23:00"
        )
        response = self.client.get("/api/v1/escala-flex/break-times/")
        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertEqual(row["default_schedule"], "17:00 - 23:00")
        self.assertEqual(row["break_exit_min"], "18:00")
        self.assertEqual(row["break_exit_max"], "21:45")

    def test_stats_heatmap_counts_break_overlap(self):
        response = self.client.get("/api/v1/escala-flex/break-times/stats/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_agents"], 1)
        heatmap = response.data["heatmap"]
        self.assertEqual(heatmap["slot_minutes"], 20)
        self.assertEqual(len(heatmap["slots"]), 72)
        self.assertEqual(heatmap["hours"][0], "00:00")
        self.assertEqual(heatmap["hours"][-1], "23:00")
        self.assertIn("12:00", heatmap["slots"])
        self.assertIn("12:20", heatmap["slots"])
        weekday = heatmap["rows"][0]
        self.assertEqual(weekday["label"], "Total")
        slot_index = heatmap["slots"].index("12:00")
        self.assertEqual(weekday["counts"][slot_index], 1)
        self.assertEqual(weekday["counts"][slot_index + 1], 0)
        self.assertEqual(len(heatmap["rows"]), 1)
        activities = heatmap["activity_by_slot"][slot_index]
        self.assertEqual(activities.get("BrFlow"), 1)

    def test_stats_heatmap_uses_overtime_extra_duration(self):
        ScheduleToday.objects.filter(agent=self.agent, date=self.today).update(
            work_schedule="08:00 - 17:00",
            overtime=True,
        )
        BreakTime.objects.filter(agent_lan_id="agt01").update(week="12:00", weekend="11:00")

        response = self.client.get("/api/v1/escala-flex/break-times/stats/")
        self.assertEqual(response.status_code, 200)
        heatmap = response.data["heatmap"]
        slot_11 = heatmap["slots"].index("11:00")
        slot_12 = heatmap["slots"].index("12:00")
        counts = heatmap["rows"][0]["counts"]
        self.assertGreater(counts[slot_11], 0)
        self.assertEqual(counts[slot_12], 0)

    def test_list_weekend_bounds_follow_overtime_flag(self):
        ScheduleToday.objects.filter(agent=self.agent, date=self.today).update(
            work_schedule="08:00 - 17:00",
            overtime=True,
        )
        response = self.client.get("/api/v1/escala-flex/break-times/")
        row = response.data["results"][0]
        self.assertTrue(row["overtime"])
        self.assertEqual(row["weekend_break_exit_max"], "15:00")

    def test_update_break_time_stores_exit_time_only(self):
        response = self.client.post(
            "/api/v1/escala-flex/break-times/update/",
            {
                "items": [
                    {
                        "user_lan_id": "agt01",
                        "week": "13:00",
                        "weekend": "12:00",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], 1)
        bt = BreakTime.objects.get(agent_lan_id="agt01", active=True)
        self.assertEqual(bt.week, "13:00")
        self.assertEqual(bt.weekend, "12:00")
        entry = ScheduleToday.objects.get(agent=self.agent, date=self.today)
        self.assertEqual(entry.week_break, "13:00")
        self.assertEqual(entry.weekend_break, "12:00")

    def test_rejects_break_outside_shift_bounds(self):
        response = self.client.post(
            "/api/v1/escala-flex/break-times/update/",
            {
                "items": [
                    {
                        "user_lan_id": "agt01",
                        "week": "08:30",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["updated"], 0)
        self.assertTrue(response.data["errors"])
