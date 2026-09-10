# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.jira_copy import (
    build_batch_jira_copy,
    build_registro_jira_copy,
    build_registro_portal_url,
    build_weekly_jira_copy,
    compute_sla_info,
    is_registro_eligible_for_jira_formalization,
    jira_pendentes_all_queryset,
    jira_pendentes_queryset,
    jira_pendentes_today_count,
    profile_label,
    resolve_jira_profile_key,
)

User = get_user_model()


class JiraCopyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="agent001",
            password="pass12345",
            email="agent001@test.local",
            first_name="Ana",
            last_name="Silva",
        )
        self.received_at = timezone.now().replace(microsecond=0)

    def test_profile_label(self):
        self.assertEqual(profile_label("planejamento"), "Planejamento (PPLID)")
        self.assertEqual(profile_label("processos"), "Processos e Riscos")

    @patch("apps.escala_flex.services.permissions.build_operational_profile")
    def test_resolve_profile_processos(self, mock_profile):
        mock_profile.return_value = MagicMock(team="Processos HC")
        self.assertEqual(resolve_jira_profile_key(self.user), "processos")

    @patch("apps.escala_flex.services.permissions.build_operational_profile")
    def test_resolve_profile_planejamento_default(self, mock_profile):
        mock_profile.return_value = MagicMock(team="Planejamento HC")
        self.assertEqual(resolve_jira_profile_key(self.user), "planejamento")

    def test_build_registro_jira_copy(self):
        registro = SuporteClaroRegistro.objects.create(
            protocolo="PROT-123",
            irregularidade="Cliente sem retorno",
            avaliacao="Aguardando",
            received_at=self.received_at,
            sent_by="Maria",
            origem="teams",
            status="aberto",
            created_by=self.user,
        )
        copy = build_registro_jira_copy(registro)
        self.assertIn("[Suporte Claro]", copy["summary"])
        self.assertIn("PROT-123", copy["summary"])
        self.assertIn("Formalização diária", copy["description"])
        self.assertIn("── Atendimento ──", copy["description"])
        self.assertIn("Cliente sem retorno", copy["description"])
        self.assertEqual(copy["registro_id"], registro.id)

    @patch("apps.suporte_claro.services.jira_copy.settings")
    def test_build_registro_jira_copy_includes_portal_url(self, mock_settings):
        mock_settings.PPLID_FRONTEND_URL = "http://portal.test"
        registro = SuporteClaroRegistro.objects.create(
            protocolo="LINK-1",
            irregularidade="Teste",
            avaliacao="Ok",
            received_at=self.received_at,
            origem="teams",
            status=SuporteClaroRegistro.STATUS_CONCLUIDO,
            created_by=self.user,
        )
        copy = build_registro_jira_copy(registro)
        expected = build_registro_portal_url(registro.id)
        self.assertEqual(copy["portal_url"], expected)
        self.assertIn(expected, copy["description"])

    def test_format_sla_labels_multiday(self):
        from apps.suporte_claro.services.jira_copy import _format_sla_labels

        compact, total = _format_sla_labels(24 * 60 + 18)  # 1d 18min
        self.assertEqual(compact, "1d 18min")
        self.assertEqual(total, "24h 18min")

        compact2, total2 = _format_sla_labels(42 * 60 + 18)  # 1d 18h 18min
        self.assertEqual(compact2, "1d 18h 18min")
        self.assertEqual(total2, "42h 18min")

    def test_build_registro_jira_copy_includes_sla_when_retorno(self):
        received = self.received_at
        registro = SuporteClaroRegistro.objects.create(
            protocolo="SLA-1",
            irregularidade="Demanda teste",
            avaliacao="Cliente orientado",
            received_at=received,
            origem="teams",
            created_by=self.user,
        )
        sla = compute_sla_info(registro)
        self.assertFalse(sla["sla_pending"])
        self.assertIsNotNone(sla["sla_minutes"])
        copy = build_registro_jira_copy(registro)
        self.assertIn("── SLA ──", copy["description"])
        self.assertIn("Tempo de atendimento:", copy["description"])
        self.assertEqual(copy["sla_label"], sla["sla_label"])

    def test_build_weekly_jira_copy_filters_by_user_and_period(self):
        SuporteClaroRegistro.objects.create(
            protocolo="W-1",
            irregularidade="Caso A",
            received_at=self.received_at,
            origem="email",
            created_by=self.user,
        )
        other = User.objects.create_user(username="other", password="pass")
        SuporteClaroRegistro.objects.create(
            protocolo="W-2",
            irregularidade="Caso B",
            received_at=self.received_at,
            origem="email",
            created_by=other,
        )

        day = timezone.localdate()
        copy = build_weekly_jira_copy(self.user, date_from=day, date_to=day)
        self.assertEqual(copy["count"], 1)
        self.assertIn("formalização semanal", copy["summary"].lower())
        self.assertIn("W-1", copy["description"])
        self.assertNotIn("W-2", copy["description"])

    def test_build_batch_jira_copy(self):
        registro_a = SuporteClaroRegistro.objects.create(
            protocolo="BATCH-A",
            irregularidade="A",
            received_at=self.received_at,
            origem="teams",
            created_by=self.user,
        )
        registro_b = SuporteClaroRegistro.objects.create(
            protocolo="BATCH-B",
            irregularidade="B",
            received_at=self.received_at,
            origem="email",
            created_by=self.user,
        )
        items = build_batch_jira_copy([registro_a, registro_b])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["protocolo"], "BATCH-A")
        self.assertEqual(items[1]["registro_id"], registro_b.id)

    def test_jira_pendentes_queryset_requires_concluido_com_retorno(self):
        eligible = SuporteClaroRegistro.objects.create(
            protocolo="PEND-OK",
            irregularidade="Sem Jira",
            avaliacao="Cliente orientado",
            received_at=self.received_at,
            origem="teams",
            status=SuporteClaroRegistro.STATUS_CONCLUIDO,
            created_by=self.user,
        )
        SuporteClaroRegistro.objects.create(
            protocolo="PEND-ABERTO",
            irregularidade="Aberto",
            received_at=self.received_at,
            origem="email",
            status=SuporteClaroRegistro.STATUS_ABERTO,
            created_by=self.user,
        )
        SuporteClaroRegistro.objects.create(
            protocolo="LINK-1",
            irregularidade="Com Jira",
            avaliacao="Ok",
            received_at=self.received_at,
            origem="email",
            status=SuporteClaroRegistro.STATUS_CONCLUIDO,
            chamado_sistema="jira",
            chamado_codigo="PPLID-99",
            created_by=self.user,
        )
        day = timezone.localdate()
        ids = list(jira_pendentes_queryset(day).values_list("id", flat=True))
        self.assertIn(eligible.id, ids)
        self.assertEqual(len(ids), 1)
        self.assertTrue(is_registro_eligible_for_jira_formalization(eligible))

    def test_jira_pendentes_all_includes_previous_days(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        start = timezone.make_aware(datetime.combine(yesterday, datetime.min.time()))
        eligible_old = SuporteClaroRegistro.objects.create(
            protocolo="PEND-OLD",
            irregularidade="Sem Jira",
            avaliacao="Cliente orientado",
            received_at=start,
            origem="teams",
            status=SuporteClaroRegistro.STATUS_CONCLUIDO,
            created_by=self.user,
        )
        eligible_today = SuporteClaroRegistro.objects.create(
            protocolo="PEND-TODAY",
            irregularidade="Sem Jira",
            avaliacao="Cliente orientado",
            received_at=self.received_at,
            origem="teams",
            status=SuporteClaroRegistro.STATUS_CONCLUIDO,
            created_by=self.user,
        )
        all_ids = list(jira_pendentes_all_queryset().values_list("id", flat=True))
        today_ids = list(jira_pendentes_queryset(timezone.localdate()).values_list("id", flat=True))
        self.assertIn(eligible_old.id, all_ids)
        self.assertIn(eligible_today.id, all_ids)
        self.assertNotIn(eligible_old.id, today_ids)
        self.assertIn(eligible_today.id, today_ids)
        self.assertEqual(jira_pendentes_today_count(), 1)
