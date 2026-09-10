from datetime import date, datetime, time

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import Escala, ScheduleToday, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.operational_dashboard import build_operational_dashboard
from apps.workforce.models import Agent, AgentHistory


class OperationalDashboardTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=1, name="Disponível", active=True, logged_in=True)
        StatusType.objects.create(pk=3, name="Deslogado", active=True, logged_in=False)
        StatusType.objects.create(pk=4, name="Intervalo", active=True, logged_in=True)
        StatusType.objects.create(pk=10, name="Pessoal", active=True, logged_in=True)
        StatusType.objects.create(pk=11, name="Problemas sistêmicos", active=True, logged_in=True)
        self.leader = Agent.objects.create(user_lan_id="lead01", full_name="Líder Teste", active=True)
        self.agent = Agent.objects.create(user_lan_id="agt01", full_name="Agente Teste", active=True)
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional BrFlow",
            job_title="Agente",
            job_activity="BrFlow",
            start_date=date.today(),
            active=True,
        )
        self.today = timezone.localdate()
        Escala.objects.create(
            agent=self.agent,
            data=self.today,
            dia_escala="00:00 - 23:59",
            horario="00:00 - 23:59",
        )
        ScheduleTodayService.build_for_date(self.today)
        self.entry = ScheduleToday.objects.get(agent=self.agent)
        self.entry.work_schedule = "00:00 - 23:59"
        self.entry.week_break = "18:00"
        self.entry.weekend_break = "18:00"
        self.entry.sector = "Backoffice"
        self.entry.save()

    def _at(self, hour: int, minute: int = 0):
        return timezone.make_aware(datetime.combine(self.today, time(hour, minute)))

    def _mark_logged_in(self, hour: int = 8, minute: int = 0):
        self.entry.start_of_work = self._at(hour, minute)
        self.entry.save()

    def test_ausente_without_start_of_work_goes_to_absenteeism_not_alerts(self):
        self.entry.status_id = None
        self.entry.start_of_work = None
        self.entry.save()
        in_shift = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in in_shift["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["ausente_agora"])
        self.assertTrue(row["ausente_dia"])
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertEqual(row["prioridade"], "—")
        self.assertEqual(row["estado"], "Ausente")
        self.assertNotIn(row, in_shift["lists"]["alertas"])
        self.assertIn(row, in_shift["lists"]["ausentes_agora"])
        self.assertIn(row, in_shift["lists"]["ausentes_dia"])

    def test_ausente_dia_before_shift_not_agora(self):
        self.entry.work_schedule = "14:00 - 20:00"
        self.entry.start_of_work = None
        self.entry.status_id = None
        self.entry.save()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["ausente_dia"])
        self.assertFalse(row["ausente_agora"])
        self.assertIn(row, data["lists"]["ausentes_dia"])
        self.assertNotIn(row, data["lists"]["ausentes_agora"])

    def test_logged_out_not_ausente(self):
        self.entry.status_id = 3
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["ausente"])
        self.assertNotIn(row, data["lists"]["ausentes"])

    def test_presente_when_disponivel(self):
        self.entry.status_id = 1
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["presente"])
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertEqual(row["estado"], "Disponível")

    def test_interval_on_time_not_alert(self):
        self.entry.status_id = 4
        self.entry.week_break = "13:00"
        self.entry.overtime = False
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(13, 10))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertIsNone(row["intervalo_atraso_minutos"])

    def test_interval_late_medium_without_he(self):
        self.entry.status_id = 4
        self.entry.week_break = "13:00"
        self.entry.overtime = False
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(13, 16))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_intervalo"])
        self.assertEqual(row["intervalo_atraso_minutos"], 1)
        self.assertEqual(row["prioridade"], "Média")
        self.assertIn(row, data["lists"]["alertas"])

    def test_interval_late_high_without_he(self):
        self.entry.status_id = 4
        self.entry.week_break = "13:00"
        self.entry.overtime = False
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(13, 18))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row["intervalo_atraso_minutos"], 3)
        self.assertEqual(row["prioridade"], "Alta")

    def test_interval_late_medium_with_he(self):
        self.entry.status_id = 4
        self.entry.overtime = True
        self.entry.weekend_break = "12:00"
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(13, 4))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row["intervalo_atraso_minutos"], 4)
        self.assertEqual(row["prioridade"], "Média")

    def test_interval_late_high_with_he(self):
        self.entry.status_id = 4
        self.entry.overtime = True
        self.entry.weekend_break = "12:00"
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(13, 6))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row["intervalo_atraso_minutos"], 6)
        self.assertEqual(row["prioridade"], "Alta")

    def test_em_pausa_not_ausente(self):
        self.entry.status_id = 4
        self.entry.week_break = "18:00"
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["em_pausa"])
        self.assertFalse(row["ausente"])
        self.assertEqual(len(data["lists"]["pausas"]), 1)
        self.assertNotIn(row, data["lists"]["ausentes"])

    def test_deslogado(self):
        self.entry.status_id = 3
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["deslogado"])
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertTrue(row["alerta_saida_antecipada"])
        self.assertTrue(row["informativo"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertEqual(row["estado"], "Deslogado antes do fim da escala (23:59)")
        self.assertNotIn(row, data["lists"]["alertas"])
        self.assertIn(row, data["lists"]["informativos"])
        self.assertNotIn(row, data["lists"]["ausentes_agora"])

    def test_deslogado_without_login_not_in_alertas(self):
        self.entry.status_id = 3
        self.entry.start_of_work = None
        self.entry.save()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["deslogado"])
        self.assertTrue(row["ausente_agora"])
        self.assertEqual(row["estado"], "Deslogado")
        self.assertEqual(row["prioridade"], "—")
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertFalse(row["alerta_saida_antecipada"])
        self.assertNotIn(row, data["lists"]["alertas"])
        self.assertIn(row, data["lists"]["ausentes_agora"])

    def test_hora_extra_agora_only_in_shift(self):
        self.entry.overtime = True
        self.entry.journey = "09:00 - 18:00"
        self.entry.work_schedule = "08:00 - 17:00"
        self.entry.status_id = 1
        self._mark_logged_in()
        in_shift = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in in_shift["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["hora_extra_dia"])
        self.assertTrue(row["hora_extra_agora"])
        self.assertIn(row, in_shift["lists"]["hora_extra_agora"])
        self.assertIn(row, in_shift["lists"]["hora_extra_dia"])

    def test_hora_extra_dia_before_shift_not_agora(self):
        self.entry.overtime = True
        self.entry.work_schedule = "14:00 - 20:00"
        self.entry.status_id = None
        self.entry.save()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["hora_extra_dia"])
        self.assertFalse(row["hora_extra_agora"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertTrue(row["informativo"])
        self.assertIn(row, data["lists"]["hora_extra_dia"])
        self.assertNotIn(row, data["lists"]["hora_extra_agora"])
        self.assertNotIn(row, data["lists"]["alertas"])
        self.assertIn(row, data["lists"]["informativos"])

    def test_escala_divergente_before_shift_still_informativos(self):
        self.entry.journey = "09:00 - 18:00"
        self.entry.work_schedule = "14:00 - 20:00"
        self.entry.status_id = 1
        self._mark_logged_in()
        self.entry.save()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["escala_divergente"])
        self.assertFalse(row["em_turno"])
        self.assertEqual(row["estado"], "Escala divergente do horário base")
        self.assertEqual(row["prioridade"], "Info")
        self.assertTrue(row["informativo"])
        self.assertIn(row, data["lists"]["informativos"])
        self.assertNotIn(row, data["lists"]["alertas"])

    def test_hora_extra_alone_not_alert(self):
        self.entry.overtime = True
        self.entry.journey = "09:00 - 18:00"
        self.entry.work_schedule = "08:00 - 17:00"
        self.entry.status_id = 1
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["hora_extra"])
        self.assertTrue(row["escala_divergente"])
        self.assertFalse(row["alerta_intervalo"])
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertTrue(row["informativo"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertIn(row, data["lists"]["hora_extra"])
        self.assertIn(row, data["lists"]["informativos"])
        self.assertNotIn(row, data["lists"]["alertas"])

    def test_escala_divergente_informativo(self):
        self.entry.journey = "09:00 - 18:00"
        self.entry.work_schedule = "08:00 - 17:00"
        self.entry.status_id = 1
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["escala_divergente"])
        self.assertTrue(row["informativo"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertEqual(row["estado"], "Disponível")
        self.assertIn(row, data["lists"]["informativos"])
        self.assertNotIn(row, data["lists"]["alertas"])

    def test_escala_divergente_keeps_info_with_operational_alert(self):
        self.entry.journey = "09:00 - 18:00"
        self.entry.work_schedule = "14:00 - 20:00"
        self.entry.week_break = "15:00"
        self.entry.status_id = 1
        self._mark_logged_in(hour=14, minute=30)
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(15, 30))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["escala_divergente"])
        self.assertTrue(row["alerta_intervalo_nao_iniciado"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertEqual(row["estado"], "Intervalo previsto para 15:00 não iniciado")
        self.assertTrue(row["informativo"])
        self.assertTrue(row["precisa_acompanhamento"])
        self.assertIn(row, data["lists"]["alertas"])
        self.assertIn(row, data["lists"]["informativos"])

    def test_escala_divergente_absent_still_informativo(self):
        self.entry.journey = "09:00 - 18:00"
        self.entry.work_schedule = "08:00 - 17:00"
        self.entry.status_id = None
        self.entry.start_of_work = None
        self.entry.save()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["escala_divergente"])
        self.assertTrue(row["ausente_agora"])
        self.assertEqual(row["estado"], "Ausente")
        self.assertEqual(row["prioridade"], "Info")
        self.assertTrue(row["informativo"])
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertIn(row, data["lists"]["informativos"])
        self.assertNotIn(row, data["lists"]["alertas"])

    def test_pessoal_baixa_priority(self):
        ref = self._at(12, 0)
        self.entry.status_id = 10
        self.entry.last_change = ref - timezone.timedelta(minutes=1)
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=ref)
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row["pessoal_duracao_minutos"], 1)
        self.assertEqual(row["prioridade"], "Baixa")
        self.assertEqual(row["estado"], "Status Pessoal há 1 min")
        self.assertTrue(row["precisa_acompanhamento"])
        self.assertIn(row, data["lists"]["alertas"])

    def test_pessoal_media_priority(self):
        ref = self._at(12, 0)
        self.entry.status_id = 10
        self.entry.last_change = ref - timezone.timedelta(minutes=3)
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=ref)
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row["pessoal_duracao_minutos"], 3)
        self.assertEqual(row["prioridade"], "Média")
        self.assertIn(row, data["lists"]["alertas"])

    def test_pessoal_exactly_two_minutes_remains_low_priority(self):
        ref = self._at(12, 0)
        self.entry.status_id = 10
        self.entry.last_change = ref - timezone.timedelta(minutes=2)
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=ref)
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row["pessoal_duracao_minutos"], 2)
        self.assertEqual(row["prioridade"], "Baixa")
        self.assertEqual(row["motivo"], "Status Pessoal")

    def test_pessoal_alta_priority(self):
        ref = self._at(12, 0)
        self.entry.status_id = 10
        self.entry.last_change = ref - timezone.timedelta(minutes=6)
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=ref)
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row["pessoal_duracao_minutos"], 6)
        self.assertEqual(row["prioridade"], "Alta")
        self.assertIn(row, data["lists"]["alertas"])

    def test_pessoal_without_last_change_goes_to_configuration_info(self):
        self.entry.status_id = 10
        self.entry.last_change = None
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertTrue(row["alerta_status_sem_inicio"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertEqual(row["estado"], "Horário de início do status indisponível")
        self.assertIn(row, data["lists"]["informativos"])

    def test_sistemico_always_alta(self):
        self.entry.status_id = 11
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_sistemico"])
        self.assertEqual(row["prioridade"], "Alta")
        self.assertTrue(row["precisa_acompanhamento"])
        self.assertIn(row, data["lists"]["alertas"])

    def test_interval_not_started_when_disponivel_after_break_start(self):
        self.entry.status_id = 1
        self.entry.week_break = "13:00"
        self.entry.overtime = False
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(13, 5))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_intervalo_nao_iniciado"])
        self.assertEqual(row["prioridade"], "Alta")
        self.assertEqual(row["estado"], "Intervalo previsto para 13:00 não iniciado")
        self.assertIn(row, data["lists"]["alertas"])

    def test_interval_not_started_not_before_break_start(self):
        self.entry.status_id = 1
        self.entry.week_break = "13:00"
        self.entry.overtime = False
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 50))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["alerta_intervalo_nao_iniciado"])
        self.assertFalse(row["precisa_acompanhamento"])

    def test_interval_out_of_schedule_before_window(self):
        self.entry.status_id = 4
        self.entry.week_break = "13:00"
        self.entry.overtime = False
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 50))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_intervalo_fora_escala"])
        self.assertEqual(row["prioridade"], "Alta")
        self.assertEqual(
            row["estado"], "Intervalo iniciado antes do horário previsto (13:00)"
        )
        self.assertIn(row, data["lists"]["alertas"])

    def test_filter_by_leader(self):
        class Params:
            def get(self, key, default=None):
                return {"leader_lan_id": "lead01"}.get(key, default)

            def getlist(self, key):
                return []

        data = build_operational_dashboard(self.today, Params(), reference_time=self._at(12, 0))
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["leader_lan_id"].lower(), "lead01")

    def _setup_treinamento_occurrence(
        self, *, scheduled_hour=9, forecast_seconds=3600, approved=True
    ):
        from apps.escala_flex.models import OccurrenceType, OperationalOccurrence

        status = StatusType.objects.create(pk=7, name="Treinamento", active=True, logged_in=True)
        occ_type = OccurrenceType.objects.create(pk=7001, name="Treinamento Occ", active=True)
        occ_type.status_types.set([status])
        occurrence = OperationalOccurrence.objects.create(
            agent=self.agent,
            schedule_today=self.entry,
            occurrence_type=occ_type,
            date=self.today,
            forecast_seconds=forecast_seconds,
            scheduled_time=time(scheduled_hour, 0),
            approved=approved,
            cancelled=False,
            description="treinamento",
        )
        self.entry.status_id = 7
        self.entry.last_change = self._at(scheduled_hour, 0)
        self._mark_logged_in()
        return occurrence

    def test_occurrence_within_window_not_alert(self):
        self._setup_treinamento_occurrence()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(9, 30))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["alerta_ocorrencia_excedente"])
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertNotIn(row, data["lists"]["alertas"])

    def test_occurrence_past_end_raises_alert_with_time(self):
        """Ocorrência 09:00–10:00; às 10:05 ainda em Treinamento → alerta +5 min."""
        self._setup_treinamento_occurrence()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 5))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_ocorrencia_excedente"])
        self.assertEqual(row["ocorrencia_excesso_minutos"], 5)
        self.assertEqual(
            row["estado"], "Treinamento excedeu o tempo da ocorrência em 5 min"
        )
        self.assertEqual(row["prioridade"], "Alta")
        self.assertTrue(row["precisa_acompanhamento"])
        self.assertIn(row, data["lists"]["alertas"])

    def test_occurrence_excess_priority_tiers(self):
        self._setup_treinamento_occurrence()
        baixa = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 1))
        row_baixa = next(r for r in baixa["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row_baixa["ocorrencia_excesso_minutos"], 1)
        self.assertEqual(row_baixa["prioridade"], "Baixa")

        media = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 3))
        row_media = next(r for r in media["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row_media["ocorrencia_excesso_minutos"], 3)
        self.assertEqual(row_media["prioridade"], "Média")

        alta = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 5))
        row_alta = next(r for r in alta["rows"] if r["user_lan_id"] == "agt01")
        self.assertEqual(row_alta["ocorrencia_excesso_minutos"], 5)
        self.assertEqual(row_alta["prioridade"], "Alta")

    def test_linked_status_without_occurrence_alerts(self):
        StatusType.objects.create(pk=7, name="Treinamento", active=True, logged_in=True)
        from apps.escala_flex.models import OccurrenceType

        occ_type = OccurrenceType.objects.create(pk=7002, name="Treinamento Occ 2", active=True)
        occ_type.status_types.set([StatusType.objects.get(pk=7)])
        self.entry.status_id = 7
        self.entry.last_change = self._at(10, 0)
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 12))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_ocorrencia_excedente"])
        self.assertEqual(row["ocorrencia_excesso_minutos"], 12)
        self.assertEqual(row["estado"], "Treinamento sem ocorrência cadastrada")
        self.assertIn(row, data["lists"]["alertas"])

    def test_linked_status_with_pending_occurrence_has_specific_state(self):
        self._setup_treinamento_occurrence(approved=None)
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 12))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_ocorrencia_excedente"])
        self.assertEqual(row["ocorrencia_situacao"], "pendente")
        self.assertEqual(
            row["estado"], "Treinamento com ocorrência pendente de aprovação"
        )
        self.assertEqual(row["motivo"], "Ocorrência pendente de aprovação")

    def test_linked_status_with_rejected_occurrence_has_specific_state(self):
        self._setup_treinamento_occurrence(approved=False)
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 12))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_ocorrencia_excedente"])
        self.assertEqual(row["ocorrencia_situacao"], "recusada")
        self.assertEqual(row["estado"], "Treinamento com ocorrência recusada")
        self.assertEqual(row["motivo"], "Ocorrência recusada")

    def test_logged_in_before_schedule_goes_to_informativos(self):
        """Escala 11:00–17:00, login às 10:03, agora 10:30 → Informativos."""
        self.entry.work_schedule = "11:00 - 17:00"
        self.entry.journey = "11:00 - 17:00"
        self.entry.status_id = 1
        self.entry.start_of_work = self._at(10, 3)
        self.entry.save()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(10, 30))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["em_turno"])
        self.assertTrue(row["alerta_login_fora_escala"])
        self.assertTrue(row["informativo"])
        self.assertTrue(row["informativo_agora"])
        self.assertTrue(row["informativo_dia"])
        self.assertEqual(
            row["estado"], "Logado fora da escala — horário previsto 11:00 - 17:00"
        )
        self.assertEqual(row["prioridade"], "Info")
        self.assertIn(row, data["lists"]["informativos"])
        self.assertIn(row, data["lists"]["informativos_agora"])
        self.assertIn(row, data["lists"]["informativos_dia"])
        self.assertNotIn(row, data["lists"]["alertas"])
        self.assertNotIn(row, data["lists"]["ausentes_agora"])

    def test_alert_outside_shift_only_in_alertas_dia(self):
        """Pessoal após o fim do turno entra em alertas do dia, não no horário atual."""
        self.entry.work_schedule = "08:00 - 14:00"
        self.entry.journey = "08:00 - 14:00"
        self.entry.status_id = 10
        self.entry.last_change = self._at(13, 50)
        self._mark_logged_in(8, 0)
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(15, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["em_turno"])
        self.assertTrue(row["precisa_acompanhamento_dia"])
        self.assertFalse(row["precisa_acompanhamento"])
        self.assertIn(row, data["lists"]["alertas_dia"])
        self.assertNotIn(row, data["lists"]["alertas_agora"])
        self.assertNotIn(row, data["lists"]["alertas"])

    def test_active_agent_without_break_configuration_goes_to_informativos(self):
        self.entry.status_id = 1
        self.entry.week_break = ""
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_config_intervalo_ausente"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertEqual(row["estado"], "Horário de intervalo não configurado")
        self.assertIn(row, data["lists"]["informativos_agora"])

    def test_active_agent_without_valid_schedule_goes_to_informativos(self):
        self.entry.status_id = 1
        self.entry.work_schedule = ""
        self.entry.journey = ""
        self._mark_logged_in()
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertTrue(row["alerta_config_escala_invalida"])
        self.assertEqual(row["prioridade"], "Info")
        self.assertEqual(row["estado"], "Escala inválida ou não configurada")
        self.assertIn(row, data["lists"]["informativos_dia"])

    def test_equal_standard_and_realized_schedule_is_not_divergent(self):
        self.entry.status_id = 1
        self.entry.journey = "09:00 - 18:00"
        self.entry.work_schedule = "09:00 - 18:00"
        self._mark_logged_in(hour=9)
        data = build_operational_dashboard(self.today, {}, reference_time=self._at(12, 0))
        row = next(r for r in data["rows"] if r["user_lan_id"] == "agt01")
        self.assertFalse(row["escala_divergente"])
