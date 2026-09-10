"""Testes da cobertura de status por ocorrências (excedente para o líder)."""

from datetime import date, datetime, time, timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import OccurrenceType, OperationalOccurrence, StatusEvent, StatusType
from apps.escala_flex.services.status_event_coverage import (
    allocate_coverage_for_events,
    coverage_for_event,
)
from apps.workforce.models import Agent


class StatusEventCoverageTests(TestCase):
    def setUp(self):
        self.agent = Agent.objects.create(
            user_lan_id="agentcov", full_name="Agente Cobertura", active=True
        )
        self.status, _ = StatusType.objects.get_or_create(
            pk=4,
            defaults={"name": "Feedback", "color": "#000", "active": True},
        )
        self.occ_type = OccurrenceType.objects.create(
            pk=9001, name="Feedback Occ", active=True
        )
        self.occ_type.status_types.set([self.status])
        self.day = timezone.localdate()

    def _event(self, *, start: datetime, duration: int, status=None) -> StatusEvent:
        status = status or self.status
        return StatusEvent.objects.create(
            id=uuid4(),
            agent=self.agent,
            status=status,
            start_date=start,
            final_date=start + timedelta(seconds=duration),
            active_event=False,
            total_duration=duration,
        )

    def _occurrence(self, *, forecast: int, approved=True) -> OperationalOccurrence:
        return OperationalOccurrence.objects.create(
            agent=self.agent,
            occurrence_type=self.occ_type,
            date=self.day,
            forecast_seconds=forecast,
            scheduled_time=time(10, 0),
            approved=approved,
            cancelled=False,
            description="teste",
        )

    def test_excess_beyond_approved_occurrence(self):
        """Ocorrência 30min + status 45min → excedente 15min."""
        self._occurrence(forecast=1800)
        start = timezone.make_aware(datetime.combine(self.day, time(10, 0)))
        event = self._event(start=start, duration=2700)

        coverage = coverage_for_event(event)
        self.assertEqual(coverage["covered_duration"], 1800)
        self.assertEqual(coverage["excess_duration"], 900)

    def test_fully_covered_has_zero_excess(self):
        self._occurrence(forecast=1800)
        start = timezone.make_aware(datetime.combine(self.day, time(10, 0)))
        event = self._event(start=start, duration=1200)

        coverage = coverage_for_event(event)
        self.assertEqual(coverage["covered_duration"], 1200)
        self.assertEqual(coverage["excess_duration"], 0)

    def test_pool_allocated_chronologically_across_events(self):
        """Pool de 30min: 1º evento 20min cobre tudo; 2º de 20min fica com 10 cobertos + 10 excedente."""
        self._occurrence(forecast=1800)
        start1 = timezone.make_aware(datetime.combine(self.day, time(9, 0)))
        start2 = timezone.make_aware(datetime.combine(self.day, time(11, 0)))
        e1 = self._event(start=start1, duration=1200)
        e2 = self._event(start=start2, duration=1200)

        allocated = allocate_coverage_for_events([e1, e2])
        self.assertEqual(allocated[e1.id]["covered_duration"], 1200)
        self.assertEqual(allocated[e1.id]["excess_duration"], 0)
        self.assertEqual(allocated[e2.id]["covered_duration"], 600)
        self.assertEqual(allocated[e2.id]["excess_duration"], 600)

    def test_unapproved_occurrence_does_not_cover(self):
        self._occurrence(forecast=1800, approved=False)
        start = timezone.make_aware(datetime.combine(self.day, time(10, 0)))
        event = self._event(start=start, duration=1800)

        coverage = coverage_for_event(event)
        self.assertEqual(coverage["covered_duration"], 0)
        self.assertEqual(coverage["excess_duration"], 1800)

    def test_no_occurrence_all_excess_when_linked(self):
        start = timezone.make_aware(datetime.combine(self.day, time(10, 0)))
        event = self._event(start=start, duration=900)

        coverage = coverage_for_event(event)
        self.assertTrue(coverage["status_linked"])
        self.assertEqual(coverage["covered_duration"], 0)
        self.assertEqual(coverage["excess_duration"], 900)

    def test_unlinked_status_not_approvable(self):
        """Status sem vínculo com tipo de ocorrência não gera excedente aprovável."""
        other_status, _ = StatusType.objects.get_or_create(
            pk=9999,
            defaults={"name": "Sem Vinculo Test", "color": "#000", "active": True},
        )
        for ot in OccurrenceType.objects.filter(status_types=other_status):
            ot.status_types.remove(other_status)

        start = timezone.make_aware(datetime.combine(self.day, time(10, 0)))
        event = self._event(start=start, duration=900, status=other_status)

        coverage = coverage_for_event(event)
        self.assertFalse(coverage["status_linked"])
        self.assertEqual(coverage["covered_duration"], 0)
        self.assertEqual(coverage["excess_duration"], 0)
