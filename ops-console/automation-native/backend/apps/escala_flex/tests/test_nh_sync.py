"""Testes de sincronização de NH com o Painel."""

from apps.escala_flex.models import CurrentActivity, HierarchicalLevel, ScheduleToday
from apps.escala_flex.services.schedule_today import ScheduleTodayService

from .test_services import ScheduleTodayServiceTests


class NHSyncTests(ScheduleTodayServiceTests):
    def test_build_sets_hierarchical_level_without_overwriting_job_activity(self):
        level = HierarchicalLevel.objects.create(
            name="Reclassificação | Documento De Identificação | Termo",
            sharepoint_id=1201,
            active=True,
        )
        CurrentActivity.objects.create(
            agent=self.agent,
            hierarchical_level=level,
            current_activity="legado",
        )
        ScheduleTodayService.build_for_date(self.today)
        entry = ScheduleToday.objects.get(agent=self.agent)
        self.assertEqual(entry.hierarchical_level_id, level.pk)
        self.assertEqual(entry.job_activity, "BrFlow")
        self.assertNotEqual(entry.current_activity, level.name)
