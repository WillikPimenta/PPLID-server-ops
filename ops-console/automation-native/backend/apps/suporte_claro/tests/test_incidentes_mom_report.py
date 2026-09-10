# -*- coding: utf-8 -*-
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.suporte_claro.models import (
    SuporteClaroChamadoExterno,
    SuporteClaroRegistro,
    SuporteClaroVinculoIncidente,
)
from apps.suporte_claro.services.incidentes_analytics import (
    build_incidentes_mom_report,
    canonical_incident_type,
    motivo_key_for,
    normalize_motivo_text,
)


class IncidentesMomReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="c900inc",
            password="x",
            first_name="Inc",
            last_name="Tester",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _inc(self, *, titulo, tipo, when, status="concluido"):
        return SuporteClaroRegistro.objects.create(
            titulo=titulo,
            protocolo="TMP",
            irregularidade=titulo,
            received_at=when,
            origem=SuporteClaroRegistro.ORIGEM_TEAMS,
            categoria=SuporteClaroRegistro.CATEGORIA_INCIDENTE,
            tipo_incidente=tipo,
            status=status,
            created_by=self.user,
        )

    def test_normalize_and_key(self):
        self.assertEqual(normalize_motivo_text("  Casos INCOMPLETOS!! "), "casos incompletos")
        reg = self._inc(
            titulo="Casos incompletos no Confer",
            tipo="erro",
            when=timezone.now(),
        )
        self.assertTrue(motivo_key_for(reg).startswith("erro::casos incompletos"))

    def test_mom_groups_same_motivo_across_months(self):
        aug = timezone.make_aware(datetime(2026, 8, 5, 10, 0))
        jul = timezone.make_aware(datetime(2026, 7, 12, 10, 0))
        self._inc(titulo="Casos incompletos no Confer", tipo="erro", when=jul)
        self._inc(titulo="CASOS INCOMPLETOS NO CONFER", tipo="erro", when=aug)
        self._inc(titulo="Lentidão no Confer", tipo="lentidao", when=aug)

        report = build_incidentes_mom_report(ref_month=date(2026, 8, 1))
        self.assertEqual(report.current_total, 2)
        self.assertEqual(report.previous_total, 1)
        self.assertEqual(report.recurring_motivos, 1)
        self.assertEqual(report.new_motivos, 1)
        recurring = next(b for b in report.buckets if b.is_recurring)
        self.assertEqual(recurring.current_count, 1)
        self.assertEqual(recurring.previous_count, 1)

    def test_report_consolidates_travamento_with_lentidao(self):
        aug = timezone.make_aware(datetime(2026, 8, 5, 10, 0))
        self._inc(titulo="Lentidão no CRM", tipo="lentidao", when=aug)
        travamento = self._inc(titulo="Travamento no CRM", tipo="travamento", when=aug)

        self.assertEqual(canonical_incident_type("travamento"), "lentidao")
        self.assertEqual(motivo_key_for(travamento), "lentidao::lentidao no crm")

        report = build_incidentes_mom_report(
            ref_month=date(2026, 8, 1),
            tipo_incidente="lentidao",
        )
        self.assertEqual(report.current_total, 2)
        self.assertEqual(len(report.buckets), 1)
        self.assertEqual(report.buckets[0].tipo, "lentidao")
        self.assertEqual(report.buckets[0].tipo_label, "Lentidão")

    def test_report_keeps_outro_and_missing_type_separate_from_erro(self):
        aug = timezone.make_aware(datetime(2026, 8, 5, 10, 0))
        self._inc(titulo="Falha genérica A", tipo="outro", when=aug)
        self._inc(titulo="Falha genérica B", tipo="", when=aug)

        report = build_incidentes_mom_report(
            ref_month=date(2026, 8, 1),
            tipo_incidente="outro",
        )

        self.assertEqual(report.current_total, 2)
        self.assertTrue(all(bucket.tipo == "outro" for bucket in report.buckets))
        self.assertTrue(all(item.tipo_label == "Outro" for item in report.all_items))

        error_report = build_incidentes_mom_report(
            ref_month=date(2026, 8, 1),
            tipo_incidente="erro",
        )
        self.assertEqual(error_report.current_total, 0)

    def test_vinculo_merges_different_titles(self):
        aug = timezone.make_aware(datetime(2026, 8, 4, 10, 0))
        jul = timezone.make_aware(datetime(2026, 7, 10, 10, 0))
        a = self._inc(titulo="Caso em RC no GED", tipo="erro", when=jul)
        b = self._inc(titulo="RC GED sem espelho Confer", tipo="erro", when=aug)
        low, high = sorted([a.id, b.id])
        SuporteClaroVinculoIncidente.objects.create(
            from_registro_id=low,
            to_registro_id=high,
            created_by=self.user,
        )
        report = build_incidentes_mom_report(ref_month=date(2026, 8, 1))
        self.assertEqual(report.recurring_motivos, 1)
        bucket = next(bkt for bkt in report.buckets if bkt.is_recurring)
        self.assertEqual(bucket.current_count + bucket.previous_count, 2)

    def test_history_builds_monthly_series_and_keeps_older_items(self):
        jun = timezone.make_aware(datetime(2026, 6, 10, 10, 0))
        jul = timezone.make_aware(datetime(2026, 7, 10, 10, 0))
        aug = timezone.make_aware(datetime(2026, 8, 10, 10, 0))
        self._inc(titulo="Erro recorrente", tipo="erro", when=jun)
        self._inc(titulo="Erro recorrente", tipo="erro", when=jul)
        self._inc(titulo="Erro recorrente", tipo="erro", when=aug)

        report = build_incidentes_mom_report(
            ref_month=date(2026, 8, 1),
            history_months=3,
        )

        self.assertEqual(report.history_labels, ["06/26", "07/26", "08/26"])
        self.assertEqual(report.history_totals, [1, 1, 1])
        self.assertEqual(len(report.all_items), 3)
        self.assertEqual(report.buckets[0].monthly_counts, [1, 1, 1])

    def test_report_keeps_all_external_tickets_and_applies_filters(self):
        aug = timezone.make_aware(datetime(2026, 8, 5, 10, 0))
        kept = self._inc(titulo="Erro de integração", tipo="erro", when=aug)
        self._inc(
            titulo="Queda sem relação",
            tipo="queda",
            when=aug,
            status="em_atendimento",
        )
        SuporteClaroChamadoExterno.objects.create(
            registro=kept,
            sistema=SuporteClaroRegistro.CHAMADO_SERVICE,
            codigo="INC0001",
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
            ordem=0,
        )
        SuporteClaroChamadoExterno.objects.create(
            registro=kept,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
            codigo="PPLID-1",
            natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO,
            ordem=1,
        )

        report = build_incidentes_mom_report(
            ref_month=date(2026, 8, 1),
            status_filter="concluido",
            tipo_incidente="erro",
        )

        self.assertEqual(report.current_total, 1)
        self.assertEqual(report.service_now_count, 1)
        self.assertEqual(report.without_service_now_count, 0)
        self.assertEqual(report.all_items[0].chamados_codigos, ["INC0001", "PPLID-1"])

        without_service = build_incidentes_mom_report(
            ref_month=date(2026, 8, 1),
            ticket_scope="sem_servicenow",
        )
        self.assertEqual(without_service.current_total, 1)
        self.assertEqual(without_service.service_now_count, 0)

    def test_pdf_endpoint(self):
        when = timezone.now()
        self._inc(titulo="Lentidão no Confer", tipo="lentidao", when=when)
        response = self.client.get(
            "/api/v1/suporte-claro/registros/incidentes-report.pdf",
            {"month": when.strftime("%Y-%m")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("incidentes", response["Content-Disposition"])

    def test_pdf_endpoint_rejects_invalid_ticket_scope(self):
        response = self.client.get(
            "/api/v1/suporte-claro/registros/incidentes-report.pdf",
            {"ticket_scope": "invalido"},
        )
        self.assertEqual(response.status_code, 400)

    def test_pdf_endpoint_rejects_invalid_history_window(self):
        response = self.client.get(
            "/api/v1/suporte-claro/registros/incidentes-report.pdf",
            {"history_months": "5"},
        )
        self.assertEqual(response.status_code, 400)
