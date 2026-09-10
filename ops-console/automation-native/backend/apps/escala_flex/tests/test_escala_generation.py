"""Testes da geração automática de escala mensal."""

from __future__ import annotations

from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_OP_AGENTE,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_ASSISTENTE,
    role_group_name,
)
from apps.accounts.models import User
from apps.escala_flex.models import (
    Escala,
    EscalaGenerationConflict,
    EscalaGenerationRun,
    Schedule,
    StatusType,
)
from apps.escala_flex.services.escala_generation import (
    TARGET_JOB_TITLE,
    generate_preview,
    job_title_matches,
    normalize_job_title,
    publish_run,
)
from apps.escala_flex.services.escala_generation.eligibility import (
    collect_vigent_eligible_activities,
    load_eligible_agent_days,
)
from apps.escala_flex.services.escala_generation.publisher import PublishBlockedError
from apps.escala_flex.services.escala_generation.rules import is_5x2_schedule
from apps.escala_flex.services.schedule_utils import is_night_shift_crossing
from apps.workforce.models import Agent, AgentHistory


TARGET = "Assistente de Planejamento Operacional II"


class JobTitleMatchTests(TestCase):
    def test_exact_normalized_match(self):
        self.assertTrue(job_title_matches("  assistente de   planejamento operacional ii "))
        self.assertTrue(job_title_matches("ASSISTENTE DE PLANEJAMENTO OPERACIONAL II"))
        self.assertEqual(
            normalize_job_title("Assistente  De Planejamento Operacional II"),
            normalize_job_title(TARGET_JOB_TITLE),
        )

    def test_rejects_partial_and_other_titles(self):
        self.assertFalse(job_title_matches("Assistente de Planejamento"))
        self.assertFalse(job_title_matches("Analista de Planejamento Operacional II"))
        self.assertFalse(job_title_matches(""))


class EligibilityTests(TestCase):
    def setUp(self):
        self.leader = Agent.objects.create(
            user_lan_id="leadgen", full_name="Líder Gen", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="asst01",
            full_name="Assistente Um",
            active=True,
            hire_date=date(2026, 9, 10),
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Planejamento",
            team_sector="Ops",
            job_title=TARGET,
            job_activity="Prioridades",
            journey="08:00 - 17:00",
            start_date=date(2026, 9, 10),
            active=True,
        )
        self.other = Agent.objects.create(
            user_lan_id="anal01", full_name="Analista", active=True
        )
        AgentHistory.objects.create(
            agent=self.other,
            leader=self.leader,
            team="Planejamento",
            job_title="Analista de Planejamento Operacional II",
            job_activity="Prioridades",
            journey="08:00 - 17:00",
            start_date=date(2026, 1, 1),
            active=True,
        )

    def test_only_exact_job_title(self):
        days, _ = load_eligible_agent_days(reference_month=date(2026, 9, 1))
        agent_ids = {d.agent.id for d in days}
        self.assertIn(self.agent.id, agent_ids)
        self.assertNotIn(self.other.id, agent_ids)

    def test_hire_date_mid_month(self):
        days, _ = load_eligible_agent_days(reference_month=date(2026, 9, 1))
        agent_days = [d.day for d in days if d.agent.id == self.agent.id]
        self.assertEqual(min(agent_days), date(2026, 9, 10))
        self.assertEqual(max(agent_days), date(2026, 9, 30))

    def test_termination_mid_month(self):
        self.agent.hire_date = date(2026, 9, 1)
        self.agent.save(update_fields=["hire_date"])
        hist = self.agent.history.get()
        hist.start_date = date(2026, 9, 1)
        hist.final_date = date(2026, 9, 15)
        hist.save(update_fields=["start_date", "final_date"])
        days, _ = load_eligible_agent_days(reference_month=date(2026, 9, 1))
        agent_days = [d.day for d in days if d.agent.id == self.agent.id]
        self.assertEqual(max(agent_days), date(2026, 9, 15))
        self.assertEqual(min(agent_days), date(2026, 9, 1))

    def test_vigent_activities_ignore_closed_history(self):
        self.agent.hire_date = date(2026, 9, 1)
        self.agent.save(update_fields=["hire_date"])
        current = self.agent.history.get()
        current.job_activity = "Monitoramento de SLA"
        current.start_date = date(2026, 9, 1)
        current.save(update_fields=["job_activity", "start_date"])
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Planejamento",
            job_title=TARGET,
            job_activity="Reclassificação | Correção",
            journey="08:00 - 17:00",
            start_date=date(2026, 9, 1),
            final_date=date(2026, 9, 14),
            active=False,
        )
        eligible, _ = load_eligible_agent_days(reference_month=date(2026, 9, 1))
        activities = collect_vigent_eligible_activities(eligible, target_job_title=TARGET)
        self.assertEqual(activities, ["Monitoramento de SLA"])


class GenerationAndPublishTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=3, name="Deslogado", active=True, logged_in=False)
        self.leader = Agent.objects.create(
            user_lan_id="leadpub", full_name="Líder Pub", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="asstpub",
            full_name="Assistente Pub",
            active=True,
            hire_date=date(2026, 1, 1),
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Planejamento",
            team_sector="Ops",
            job_title=TARGET,
            job_activity="Prioridades",
            journey="08:00 - 17:00",
            start_date=date(2026, 1, 1),
            active=True,
        )

    def test_deterministic_preview(self):
        run1 = generate_preview(
            reference_month=date(2026, 3, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
            },
        )
        values1 = list(
            run1.entries.order_by("agent_id", "date").values_list(
                "agent_id", "date", "day_value"
            )
        )
        run2 = generate_preview(
            reference_month=date(2026, 3, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
            },
        )
        values2 = list(
            run2.entries.order_by("agent_id", "date").values_list(
                "agent_id", "date", "day_value"
            )
        )
        self.assertEqual(values1, values2)
        self.assertEqual(run1.status, EscalaGenerationRun.STATUS_READY)

    def test_excluded_agent_ids_skip_generation(self):
        """Agentes desconsiderados na configuração não entram na prévia."""
        run = generate_preview(
            reference_month=date(2026, 3, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
                "excluded_agent_ids": [str(self.agent.id)],
            },
        )
        self.assertEqual(run.agents_considered, 0)
        self.assertEqual(run.entries.filter(agent=self.agent).count(), 0)

    def test_weekend_and_consecutive_offs(self):
        run = generate_preview(
            reference_month=date(2026, 3, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
                "max_consecutive_work_days": 6,
                "allow_night_shift": True,
            },
        )
        values = list(
            run.entries.filter(agent=self.agent).order_by("date").values_list(
                "date", "day_value"
            )
        )
        # Mar/2026: sábados e domingos preferidos como FOLGA (sem cobertura concorrendo).
        weekend_folgas = [
            day_value
            for day, day_value in values
            if day.weekday() >= 5
        ]
        self.assertTrue(weekend_folgas)
        # Preferência de FOLGA no fim de semana (pode sobrar 1–2 dias por cobertura/rotação).
        self.assertGreaterEqual(
            sum(1 for v in weekend_folgas if v == "FOLGA"),
            max(1, len(weekend_folgas) - 2),
        )
        self.assertGreaterEqual(
            sum(1 for _, v in values if v == "FOLGA"),
            4,
        )
        consecutive_conflicts = run.conflicts.filter(
            conflict_type=EscalaGenerationConflict.TYPE_INVALID_JOURNEY,
            message__icontains="consecutivos",
        ).count()
        self.assertEqual(consecutive_conflicts, 0)

    def test_activity_min_coverage_keeps_people_on_sunday(self):
        agents = []
        for i in range(3):
            agent = Agent.objects.create(
                user_lan_id=f"cov{i:02d}",
                full_name=f"Cobertura {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                team_sector="Ops",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey="06:00 - 12:00",
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
                "allow_night_shift": True,
                "max_consecutive_work_days": 6,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 2,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                        "priority": 1,
                    }
                ],
            },
        )
        # 02/08/2026 = domingo
        sunday = date(2026, 8, 2)
        working = [
            e
            for e in run.entries.filter(date=sunday)
            if e.day_value not in {"FOLGA", "FERIAS", "AFASTADO", "BH", ""}
            and e.agent_id in {a.id for a in agents}
        ]
        self.assertGreaterEqual(len(working), 2)
        below = run.conflicts.filter(
            conflict_type=EscalaGenerationConflict.TYPE_COVERAGE_BELOW_MIN,
            date=sunday,
        )
        self.assertEqual(below.count(), 0)

    def test_weekend_coverage_with_non_exact_journeys(self):
        """Jornadas próximas (ex.: 14:00-20:00) devem cobrir slots no fim de semana."""
        agents = []
        for i in range(6):
            agent = Agent.objects.create(
                user_lan_id=f"plan{i:02d}",
                full_name=f"Plan {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey="14:00 - 20:00",
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 9, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
                "allow_night_shift": True,
                "max_consecutive_work_days": 6,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["17:00 - 23:00"],
                        "priority": 1,
                    }
                ],
            },
        )
        agent_ids = {a.id for a in agents}
        for weekend_day in (date(2026, 9, 5), date(2026, 9, 6)):
            working = [
                e
                for e in run.entries.filter(date=weekend_day, agent_id__in=agent_ids)
                if e.day_value not in {"FOLGA", "FERIAS", "AFASTADO", "BH", ""}
            ]
            self.assertGreaterEqual(
                len(working),
                1,
                f"esperava cobertura em {weekend_day}, obteve {working}",
            )

    def test_no_agent_folgas_all_weekend_days(self):
        """Nenhum diurno deve folgar em todos os FDS do mês enquanto outros cobrem."""
        journeys = [
            "14:00 - 20:00",
            "08:00 - 14:00",
            "11:00 - 17:00",
            "12:00 - 18:00",
            "16:00 - 22:00",
            "06:00 - 12:00",
            "17:00 - 23:00",
            "08:30 - 18:00",
            "14:00 - 20:00",
        ]
        agents = []
        for i, journey in enumerate(journeys):
            agent = Agent.objects.create(
                user_lan_id=f"c1898{i}q" if i == 8 else f"plan{i:02d}",
                full_name=f"Plan {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 9, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
                "allow_night_shift": True,
                "max_consecutive_work_days": 6,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                        "priority": 1,
                    }
                ],
            },
        )
        weekend_days = [
            date(2026, 9, d)
            for d in range(1, 31)
            if date(2026, 9, d).weekday() >= 5
        ]
        for agent in agents:
            history = AgentHistory.objects.filter(agent=agent, active=True).first()
            journey = history.journey if history else ""
            shift = history.journey_shift if history else ""
            if is_5x2_schedule(journey, shift):
                continue
            if is_night_shift_crossing(journey):
                continue
            stats = {"folga": 0, "work": 0}
            for day in weekend_days:
                entry = run.entries.filter(agent=agent, date=day).first()
                if not entry:
                    continue
                if entry.day_value == "FOLGA":
                    stats["folga"] += 1
                elif entry.day_value and ":" in entry.day_value:
                    stats["work"] += 1
            self.assertGreater(
                stats["work"],
                0,
                f"{agent.user_lan_id} ficou só de folga nos FDS ({stats})",
            )
            self.assertLess(
                stats["folga"],
                len(weekend_days),
                f"{agent.user_lan_id} folgou em todos os FDS",
            )

    def test_night_shift_maps_to_nearest_weekend_slot(self):
        """Noturno 19:30–01:30 sobe para slot diurno mais próximo no FDS."""
        night = Agent.objects.create(
            user_lan_id="c18982q",
            full_name="Giovanna",
            active=True,
            hire_date=date(2026, 1, 1),
        )
        AgentHistory.objects.create(
            agent=night,
            leader=self.leader,
            location="Brasília",
            team="Planejamento",
            job_title=TARGET,
            job_activity="Monitoramento de SLA",
            journey="19:30 - 01:30",
            start_date=date(2026, 1, 1),
            active=True,
        )
        helpers = []
        for i, journey in enumerate(
            ("06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00", "14:00 - 20:00")
        ):
            agent = Agent.objects.create(
                user_lan_id=f"day{i:02d}",
                full_name=f"Day {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )
            helpers.append(agent)

        run = generate_preview(
            reference_month=date(2026, 9, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "allow_night_shift": True,
                "max_consecutive_work_days": 6,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                    }
                ],
            },
        )
        weekend_days = [
            date(2026, 9, d)
            for d in range(1, 31)
            if date(2026, 9, d).weekday() >= 5
        ]
        work_days = []
        allowed_weekend = {
            "06:00 - 12:00",
            "06:00-12:00",
            "11:00 - 17:00",
            "11:00-17:00",
            "17:00 - 23:00",
            "17:00-23:00",
            "FOLGA",
        }
        for day in weekend_days:
            entry = run.entries.filter(agent=night, date=day).first()
            self.assertIsNotNone(entry)
            self.assertIn(
                entry.day_value,
                allowed_weekend,
                f"noturno no FDS só pode ir para slot diurno de cobertura, got {entry.day_value}",
            )
            if entry.day_value != "FOLGA":
                work_days.append(day)
        self.assertGreater(
            len(work_days),
            0,
            "noturno não pode folgar em todos os FDS quando há cobertura mínima",
        )
        monday = date(2026, 9, 7)
        weekday_entry = run.entries.filter(agent=night, date=monday).first()
        self.assertIsNotNone(weekday_entry)
        self.assertIn(
            weekday_entry.day_value,
            {"19:30 - 01:30", "19:30-01:30", "FOLGA"},
            "dia útil mantém jornada noturna cadastral ou folga gerada",
        )

    def test_folgas_are_distributed_across_agents(self):
        """Folgas não devem concentrar no mesmo dia para quase todo o time."""
        agents = []
        for i in range(4):
            agent = Agent.objects.create(
                user_lan_id=f"dist{i:02d}",
                full_name=f"Dist {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey="06:00 - 12:00",
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
                "allow_night_shift": True,
                "max_consecutive_work_days": 6,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00"],
                    }
                ],
            },
        )
        agent_ids = {a.id for a in agents}
        offs_by_agent = {a.id: 0 for a in agents}
        offs_by_day = {}
        for e in run.entries.filter(agent_id__in=agent_ids):
            if e.day_value == "FOLGA":
                offs_by_agent[e.agent_id] += 1
                offs_by_day[e.date] = offs_by_day.get(e.date, 0) + 1

        # Todos devem ter pelo menos 1 folga
        self.assertTrue(all(v >= 1 for v in offs_by_agent.values()), offs_by_agent)
        # Nenhum domingo deve ter todos de folga (cobertura mínima 1)
        sundays = [d for d in offs_by_day if d.weekday() == 6]
        for sunday in sundays:
            self.assertLess(offs_by_day[sunday], 4, f"todos folgam em {sunday}")
        # Folgas espalhadas: o dia com mais folgas não concentra o time inteiro
        if offs_by_day:
            self.assertLess(max(offs_by_day.values()), 4)
        # Equilíbrio aproximado: diferença de FOLGAs entre agentes operacionais ≤ 2
        self.assertLessEqual(
            max(offs_by_agent.values()) - min(offs_by_agent.values()),
            1,
            offs_by_agent,
        )

    def test_folgas_prefer_weekend_and_double(self):
        """Prioriza sáb/dom e tenta folga dupla com rotação entre agentes."""
        agents = []
        for i in range(4):
            agent = Agent.objects.create(
                user_lan_id=f"wknd{i:02d}",
                full_name=f"Weekend {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey="06:00 - 12:00",
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "max_consecutive_work_days": 6,
                "extra_offs_per_agent": 0,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00"],
                    }
                ],
            },
        )
        agent_ids = {a.id for a in agents}
        weekend_offs = 0
        weekday_offs = 0
        double_pairs = 0
        for a in agents:
            offs = [
                e.date
                for e in run.entries.filter(agent=a, day_value="FOLGA")
            ]
            weekend_offs += sum(1 for d in offs if d.weekday() >= 5)
            weekday_offs += sum(1 for d in offs if d.weekday() < 5)
            off_set = set(offs)
            for d in offs:
                if d.weekday() == 5 and (d.toordinal() + 1) in {x.toordinal() for x in off_set}:
                    double_pairs += 1
        # Preferência por FDS: mais folgas no fim de semana do que no meio da semana
        # (com cobertura e equilíbrio, pode haver midweek — mas FDS deve liderar).
        self.assertGreaterEqual(weekend_offs * 2, weekday_offs)
        # Pelo menos um agente conseguiu folga dupla sáb+dom.
        self.assertGreaterEqual(double_pairs, 1)
        # Cobertura de domingo: nao zera o slot
        for sunday in [date(2026, 8, 2), date(2026, 8, 9), date(2026, 8, 16), date(2026, 8, 23), date(2026, 8, 30)]:
            working = [
                e
                for e in run.entries.filter(date=sunday, agent_id__in=agent_ids)
                if e.day_value and e.day_value != "FOLGA"
            ]
            self.assertGreaterEqual(len(working), 1, f"sem cobertura em {sunday}")

    def test_5x2_integral_always_off_on_weekends(self):
        """Turno Integral (5x2) folga todos os sábados/domingos e não cobre fim de semana."""
        agent = Agent.objects.create(
            user_lan_id="c93053a",
            full_name="Ana Carolina Alves Da Silva",
            active=True,
            hire_date=date(2026, 1, 1),
        )
        AgentHistory.objects.create(
            agent=agent,
            leader=self.leader,
            location="Brasília",
            team="Planejamento",
            job_title=TARGET,
            job_activity="Monitoramento de SLA",
            journey="08:30 - 18:00",
            journey_shift="Integral",
            start_date=date(2026, 1, 1),
            active=True,
        )
        # Pessoas operacionais para cobrir o fim de semana sem puxar a 5x2.
        for i, journey in enumerate(["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"]):
            a = Agent.objects.create(
                user_lan_id=f"ops5x{i}",
                full_name=f"Ops {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=a,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "max_consecutive_work_days": 6,
                "extra_offs_per_agent": 3,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                    }
                ],
            },
        )
        weekend = [
            e
            for e in run.entries.filter(agent=agent)
            if e.date.weekday() >= 5
        ]
        self.assertTrue(weekend)
        self.assertTrue(all(e.day_value == "FOLGA" for e in weekend), [e.day_value for e in weekend])
        # Dias úteis: trabalha a jornada Integral — sem folga adicional no meio da semana.
        weekdays = [
            e for e in run.entries.filter(agent=agent) if e.date.weekday() < 5
        ]
        midweek_folgas = [e for e in weekdays if e.day_value == "FOLGA"]
        self.assertEqual(
            midweek_folgas,
            [],
            f"5x2 não deve ter folga adicional: {[e.date.isoformat() for e in midweek_folgas]}",
        )
        worked = [e for e in weekdays if e.day_value and ":" in e.day_value]
        self.assertGreaterEqual(len(worked), 15)
        self.assertTrue(all((e.day_value or "").startswith("08:30") for e in worked))

        # Operacionais (exceto Ana/5x2) ficam equilibrados em FOLGAs geradas.
        ops = list(
            Agent.objects.filter(user_lan_id__startswith="ops5x").values_list("id", flat=True)
        )
        ops_offs = {}
        for aid in ops:
            ops_offs[aid] = run.entries.filter(agent_id=aid, day_value="FOLGA").count()
        self.assertLessEqual(
            max(ops_offs.values()) - min(ops_offs.values()),
            1,
            ops_offs,
        )
        summary = run.summary or {}
        self.assertIn("folga_spread", summary)
        self.assertLessEqual(int(summary["folga_spread"]), 1)

    def test_coverage_slot_agents_get_folgas_via_nearby_cover(self):
        """Únicos nos slots 06/11/17 não ficam com bem menos FOLGA que o resto."""
        journeys = [
            ("cov06", "06:00 - 12:00"),
            ("cov11", "11:00 - 17:00"),
            ("cov17", "17:00 - 23:00"),
            ("near14", "14:00 - 20:00"),
            ("near08", "08:00 - 14:00"),
            ("near12", "12:00 - 18:00"),
            ("night01", "23:30 - 05:05"),
        ]
        agents = []
        for lan, journey in journeys:
            agent = Agent.objects.create(
                user_lan_id=lan,
                full_name=lan,
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "allow_night_shift": False,
                "max_consecutive_work_days": 6,
                "extra_offs_per_agent": 0,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                    }
                ],
            },
        )
        by_lan = {a.user_lan_id: a for a in agents}
        detail = {
            row["lan_id"]: row
            for row in (run.summary or {}).get("agents_detail") or []
        }
        offs = {
            lan: int(detail.get(lan, {}).get("folga_balanced")
                     or run.entries.filter(agent=by_lan[lan], day_value="FOLGA").count())
            for lan, _ in journeys
            if lan != "night01"
        }
        # Slots de cobertura não podem ficar bem atrás dos helpers.
        cov_offs = [offs["cov06"], offs["cov11"], offs["cov17"]]
        helper_offs = [offs["near14"], offs["near08"], offs["near12"]]
        self.assertLessEqual(
            max(helper_offs) - min(cov_offs),
            3,
            {"cov": cov_offs, "helpers": helper_offs, "all": offs},
        )
        self.assertLessEqual(
            max(offs.values()) - min(offs.values()),
            3,
            offs,
        )
        # Sem conflito de cobertura aberta (substituto deve ter sido ativado).
        coverage_conflicts = run.conflicts.filter(
            conflict_type=EscalaGenerationConflict.TYPE_COVERAGE_BELOW_MIN,
            resolved=False,
        )
        self.assertEqual(coverage_conflicts.count(), 0, list(coverage_conflicts[:5]))
        # Noturno cadastral não gera night_shift_denied.
        night_denied = run.conflicts.filter(
            conflict_type=EscalaGenerationConflict.TYPE_NIGHT_SHIFT_DENIED,
            agent=by_lan["night01"],
        )
        self.assertEqual(night_denied.count(), 0)
        # FDS: só os 3 slots de cobertura (sem 08-14 / 12-18 / 14-20 / noturno).
        allowed = {"06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00", "FOLGA"}
        for e in run.entries.filter(agent_id__in=[a.id for a in agents]):
            if e.date.weekday() < 5:
                continue
            self.assertIn(
                e.day_value,
                allowed,
                f"{e.agent.user_lan_id} {e.date} day_value={e.day_value}",
            )

    def test_weekend_covers_evening_and_avoids_double_work(self):
        """Fim de semana: cobre 17:00-23:00 e evita mesma pessoa sáb+dom."""
        agents = []
        journeys = [
            "06:00 - 12:00",
            "06:00 - 12:00",
            "11:00 - 17:00",
            "11:00 - 17:00",
            "14:00 - 20:00",
            "17:00 - 23:00",
            "17:00 - 23:00",
        ]
        for i, journey in enumerate(journeys):
            agent = Agent.objects.create(
                user_lan_id=f"wend{i:02d}",
                full_name=f"Weekend Cover {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "max_consecutive_work_days": 6,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                    }
                ],
            },
        )
        agent_ids = {a.id for a in agents}
        for weekend_day in (date(2026, 8, 15), date(2026, 8, 16)):  # sáb/dom
            evening = [
                e
                for e in run.entries.filter(date=weekend_day, agent_id__in=agent_ids)
                if (e.day_value or "").startswith("17:00")
            ]
            self.assertGreaterEqual(
                len(evening), 1, f"faltou 17:00-23:00 em {weekend_day}"
            )

        double = 0
        for a in agents:
            sat = run.entries.filter(agent=a, date=date(2026, 8, 15)).first()
            sun = run.entries.filter(agent=a, date=date(2026, 8, 16)).first()
            if (
                sat
                and sun
                and sat.day_value not in {"FOLGA", "FERIAS", "AFASTADO", "BH", ""}
                and sun.day_value not in {"FOLGA", "FERIAS", "AFASTADO", "BH", ""}
                and ":" in (sat.day_value or "")
                and ":" in (sun.day_value or "")
            ):
                double += 1
        # Maioria não deve trabalhar os dois dias
        self.assertLessEqual(double, 2)

    def test_coverage_prefers_own_schedule_then_closest(self):
        """Com FOLGAs equilibradas, prioriza o próprio horário; proximidade é só desempate."""
        a_morning = Agent.objects.create(
            user_lan_id="slot01", full_name="Slot 06", active=True, hire_date=date(2026, 1, 1)
        )
        a_near = Agent.objects.create(
            user_lan_id="near01", full_name="Near 08", active=True, hire_date=date(2026, 1, 1)
        )
        a_evening = Agent.objects.create(
            user_lan_id="far01", full_name="Far 17", active=True, hire_date=date(2026, 1, 1)
        )
        a_morning2 = Agent.objects.create(
            user_lan_id="slot02", full_name="Slot 06 B", active=True, hire_date=date(2026, 1, 1)
        )
        for agent, journey in (
            (a_morning, "06:00 - 12:00"),
            (a_morning2, "06:00 - 12:00"),
            (a_near, "08:00 - 14:00"),
            (a_evening, "17:00 - 23:00"),
        ):
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "max_consecutive_work_days": 31,
                "min_rest_hours": 11,
                "extra_offs_per_agent": 0,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00"],
                    }
                ],
            },
        )
        # Cobertura 06:00 deve existir; horário cadastral próximo não é regra rígida —
        # quem tem mais FOLGA pode cobrir. Com 2 do slot 06, a maioria dos dias
        # ainda deve ser coberta por alguém do próprio horário.
        own_slot_days = 0
        covered_days = 0
        for day in (date(2026, 8, 2), date(2026, 8, 3), date(2026, 8, 9), date(2026, 8, 10)):
            covering = [
                e
                for e in run.entries.filter(date=day)
                if (e.day_value or "").startswith("06:00")
            ]
            self.assertGreaterEqual(len(covering), 1, f"sem cobertura 06:00 em {day}")
            covered_days += 1
            if any((e.schedule or "").startswith("06:00") for e in covering):
                own_slot_days += 1
        self.assertGreaterEqual(
            own_slot_days,
            max(1, covered_days // 2),
            "próprio horário deveria continuar como prioridade quando há gente do slot",
        )

    def test_coverage_by_schedule_slot_not_night(self):
        """Cobertura mínima por horário diurno; noturno não entra na regra."""
        a1 = Agent.objects.create(
            user_lan_id="hr01", full_name="Manhã A", active=True, hire_date=date(2026, 1, 1)
        )
        a2 = Agent.objects.create(
            user_lan_id="hr02", full_name="Manhã B", active=True, hire_date=date(2026, 1, 1)
        )
        a3 = Agent.objects.create(
            user_lan_id="hr03", full_name="Noturno", active=True, hire_date=date(2026, 1, 1)
        )
        for agent, journey in (
            (a1, "06:00 - 12:00"),
            (a2, "06:00 - 12:00"),
            (a3, "23:30 - 05:30"),
        ):
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )

        run = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "allow_night_shift": True,
                "max_consecutive_work_days": 31,
                "extra_offs_per_agent": 0,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 2,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                    }
                ],
            },
        )
        monday = date(2026, 8, 3)
        morning = [
            e
            for e in run.entries.filter(date=monday, agent__in=[a1, a2, a3])
            if (e.day_value or "").startswith("06:00")
        ]
        self.assertGreaterEqual(len(morning), 2)
        # Noturno pode folgar sem derrubar cobertura do slot 06–12
        night = run.entries.get(date=monday, agent=a3)
        self.assertIn(night.day_value, {"FOLGA", "23:30 - 05:30", "23:30-05:30"})

    def test_extra_offs_increases_folga_target(self):
        agents = []
        for i in range(3):
            agent = Agent.objects.create(
                user_lan_id=f"xoff{i}",
                full_name=f"Extra Off {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey="06:00 - 12:00",
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        base = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "max_consecutive_work_days": 31,
                "extra_offs_per_agent": 0,
            },
        )
        extra = generate_preview(
            reference_month=date(2026, 8, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "replace_existing": True,
                "max_consecutive_work_days": 31,
                "extra_offs_per_agent": 4,
            },
        )
        agent_ids = {a.id for a in agents}

        def total_offs(run):
            return run.entries.filter(agent_id__in=agent_ids, day_value="FOLGA").count()

        self.assertGreater(total_offs(extra), total_offs(base))

    def test_extra_offs_respected_with_activity_coverage(self):
        """Folga adicional permanece mesmo com cobertura mínima por horário."""
        agents = []
        journeys = [
            "06:00 - 12:00",
            "06:00 - 12:00",
            "11:00 - 17:00",
            "11:00 - 17:00",
            "17:00 - 23:00",
            "17:00 - 23:00",
        ]
        for i, journey in enumerate(journeys):
            agent = Agent.objects.create(
                user_lan_id=f"xcov{i}",
                full_name=f"Cov Off {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        coverage_cfg = {
            "include_saturdays": True,
            "include_sundays": True,
            "replace_existing": True,
            "max_consecutive_work_days": 6,
            "activity_coverage": [
                {
                    "activity": "Monitoramento de SLA",
                    "min_coverage": 1,
                    "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                    "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                }
            ],
        }
        base = generate_preview(
            reference_month=date(2026, 9, 1),
            configuration={**coverage_cfg, "extra_offs_per_agent": 0},
        )
        extra = generate_preview(
            reference_month=date(2026, 9, 1),
            configuration={**coverage_cfg, "extra_offs_per_agent": 4},
        )

        def offs_by_agent(run):
            out = {}
            for agent in agents:
                out[agent.id] = run.entries.filter(
                    agent=agent, day_value="FOLGA"
                ).count()
            return out

        base_offs = offs_by_agent(base)
        extra_offs = offs_by_agent(extra)
        increased = sum(
            1
            for agent in agents
            if extra_offs[agent.id] >= base_offs[agent.id] + 2
        )
        self.assertGreaterEqual(
            increased,
            len(agents) // 2,
            {"base": base_offs, "extra": extra_offs},
        )
        self.assertGreater(
            sum(extra_offs.values()),
            sum(base_offs.values()),
        )

    def test_operational_folga_spread_at_most_one(self):
        """Time operacional não deve ficar com 6 vs 8 folgas no mesmo mês."""
        journeys = [
            "14:00 - 20:00",
            "08:00 - 14:00",
            "11:00 - 17:00",
            "12:00 - 18:00",
            "16:00 - 22:00",
            "06:00 - 12:00",
            "17:00 - 23:00",
            "08:00 - 14:00",
            "19:30 - 01:30",
        ]
        agents = []
        for i, journey in enumerate(journeys):
            agent = Agent.objects.create(
                user_lan_id=f"bal{i:02d}",
                full_name=f"Balance {i}",
                active=True,
                hire_date=date(2026, 1, 1),
            )
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Planejamento",
                job_title=TARGET,
                job_activity="Monitoramento de SLA",
                journey=journey,
                start_date=date(2026, 1, 1),
                active=True,
            )
            agents.append(agent)

        run = generate_preview(
            reference_month=date(2026, 9, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": True,
                "include_holidays": True,
                "replace_existing": True,
                "allow_night_shift": True,
                "max_consecutive_work_days": 6,
                "extra_offs_per_agent": 4,
                "activity_coverage": [
                    {
                        "activity": "Monitoramento de SLA",
                        "min_coverage": 1,
                        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "schedules": ["06:00 - 12:00", "11:00 - 17:00", "17:00 - 23:00"],
                    }
                ],
            },
        )
        detail = (run.summary or {}).get("agents_detail") or []
        operational = [
            row
            for row in detail
            if not row.get("is_5x2") and not row.get("is_night")
        ]
        folgas = [int(row.get("folga_balanced") or 0) for row in operational]
        self.assertGreaterEqual(len(folgas), 6, folgas)
        self.assertLessEqual(max(folgas) - min(folgas), 1, folgas)

    def test_publish_creates_escala_and_schedule(self):
        run = generate_preview(
            reference_month=date(2026, 4, 1),
            configuration={
                "include_saturdays": False,
                "include_sundays": False,
                "replace_existing": True,
            },
        )
        # Resolve blocking missing-data conflicts if any by ensuring data present
        # (leader/team/activity/schedule already set)
        blocking = run.conflicts.filter(
            severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
            resolved=False,
        )
        # Existing escala conflicts shouldn't apply
        if blocking.exists():
            # Accept only if they're existing_escala with replace — shouldn't block
            for c in blocking:
                if c.conflict_type != EscalaGenerationConflict.TYPE_EXISTING_ESCALA:
                    self.fail(f"Unexpected blocking conflict: {c.conflict_type} {c.message}")

        result = publish_run(run=run)
        self.assertGreater(result.created + result.updated, 0)
        self.assertTrue(Escala.objects.filter(agent=self.agent, data__month=4).exists())
        self.assertTrue(Schedule.objects.filter(agent=self.agent, date__month=4).exists())
        run.refresh_from_db()
        self.assertEqual(run.status, EscalaGenerationRun.STATUS_PUBLISHED)

    def test_preserve_existing_when_replace_false(self):
        Escala.objects.create(
            agent=self.agent,
            leader=self.leader,
            data=date(2026, 5, 5),
            horario="08:00 - 17:00",
            dia_escala="FOLGA",
            equipe="Planejamento",
        )
        run = generate_preview(
            reference_month=date(2026, 5, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": False,
                "replace_existing": False,
            },
        )
        # Mark existing_escala blocking as... they block publish. Resolve by flipping replace
        # or clearing those conflicts for this test via replace_existing True on second run.
        # Here we assert publish is blocked while unresolved existing conflicts remain.
        with self.assertRaises(PublishBlockedError):
            publish_run(run=run)

        run2 = generate_preview(
            reference_month=date(2026, 5, 1),
            configuration={
                "include_saturdays": True,
                "include_sundays": False,
                "replace_existing": True,
            },
        )
        result = publish_run(run=run2)
        self.assertGreaterEqual(result.updated + result.created, 1)
        preserved_check = Escala.objects.get(agent=self.agent, data=date(2026, 5, 5))
        # replaced value from generation (work or FOLGA depending on weekday)
        self.assertNotEqual(preserved_check.dia_escala, "")


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class GenerationApiRbacTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.analista = User.objects.create_user(
            username="plan.gen", password="x", email="plan.gen@test.local"
        )
        agent = Agent.objects.create(
            user_lan_id="plan.gen", full_name="Plan Gen", active=True
        )
        AgentHistory.objects.create(
            agent=agent,
            team="Planejamento",
            job_title="Analista de Planejamento",
            start_date=date(2024, 1, 1),
            active=True,
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.analista.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))

        self.assistente = User.objects.create_user(
            username="plan.asst", password="x", email="plan.asst@test.local"
        )
        agent2 = Agent.objects.create(
            user_lan_id="plan.asst", full_name="Plan Asst", active=True
        )
        AgentHistory.objects.create(
            agent=agent2,
            team="Planejamento",
            job_title=TARGET,
            job_activity="Prioridades",
            start_date=date(2024, 1, 1),
            active=True,
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ASSISTENTE))
        self.assistente.groups.add(
            Group.objects.get(name=role_group_name(ROLE_PLAN_ASSISTENTE))
        )

        self.op = User.objects.create_user(
            username="op.gen", password="x", email="op.gen@test.local"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        self.op.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))

    def test_analista_can_config_and_preview(self):
        self.client.force_authenticate(user=self.analista)
        cfg = self.client.get(
            "/api/v1/escala-flex/planejamento/generation/config-options/",
            {"year": 2026, "month": 6},
        )
        self.assertEqual(cfg.status_code, 200)
        body = cfg.json()
        self.assertIn(TARGET, body["job_titles"])
        self.assertEqual(body["target_job_title"], TARGET)
        self.assertEqual(body["activities"], ["Prioridades"])
        preview = self.client.post(
            "/api/v1/escala-flex/planejamento/generation/preview/",
            {
                "reference_month": "2026-06-01",
                "configuration": {
                    "include_saturdays": False,
                    "include_sundays": False,
                    "replace_existing": True,
                },
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 201, preview.content)

    def test_assistente_denied_generate(self):
        self.client.force_authenticate(user=self.assistente)
        response = self.client.get(
            "/api/v1/escala-flex/planejamento/generation/config-options/"
        )
        self.assertEqual(response.status_code, 403)

    def test_op_denied_generate(self):
        self.client.force_authenticate(user=self.op)
        response = self.client.post(
            "/api/v1/escala-flex/planejamento/generation/preview/",
            {"reference_month": "2026-06-01", "configuration": {}},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
