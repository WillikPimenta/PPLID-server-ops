import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_ADM_PORTAL, ROLE_QUAL_USUARIO, role_group_name
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeRegistroEstado,
    QualidadeRegistroHistorico,
)
from apps.qualidade_operacional.services.record_admin import apply_states_to_instances

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True)
class QualidadeRecordAdminApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="qo_admin", email="qo_admin@example.com", password="x")
        self.regular = User.objects.create_user(username="qo_regular", email="qo_regular@example.com", password="x")
        admin_group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        regular_group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_USUARIO))
        self.admin.groups.add(admin_group)
        self.regular.groups.add(regular_group)
        self.client = APIClient()
        self.auditado = QualidadeAuditado.objects.create(
            data=date(2026, 8, 20), data_analise=date(2026, 8, 20), protocolo="ADM-100",
            matricula="c100", tipo_analise="Fraud", source_file="auditados.tsv",
        )
        self.falha = QualidadeFalha.objects.create(
            data=date(2026, 8, 20), data_analise=date(2026, 8, 20), protocolo="ADM-100",
            matricula="c100", tipo_falha="Manual", source_file="falhas.tsv",
        )

    def _preview(self, kind, pk, action, changes=None):
        response = self.client.post(
            f"/api/v1/qualidade-operacional/admin/registros/{kind}/{pk}/preview/",
            {"action": action, "changes": changes or {}, "reason": "Solicitacao operacional aprovada", "ticket": "PPLID-999", "expected_revision": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def _execute(self, kind, pk, preview, *, idem=None):
        return self.client.post(
            f"/api/v1/qualidade-operacional/admin/registros/{kind}/{pk}/actions/",
            {
                "action": preview["action"], "changes": preview.get("changes") and {key: item["after"] for key, item in preview["changes"].items()} or {},
                "reason": "Solicitacao operacional aprovada", "ticket": "PPLID-999",
                "expected_revision": preview["revision"], "idempotency_key": str(idem or uuid.uuid4()),
                "preview_token": preview["preview_token"], "confirmation": preview["confirmation_phrase"],
            },
            format="json",
        )

    def test_non_admin_cannot_search_or_manage(self):
        self.client.force_authenticate(self.regular)
        response = self.client.get("/api/v1/qualidade-operacional/admin/registros/", {"q": "ADM-100"})
        self.assertEqual(response.status_code, 403)

    def test_search_requires_bounded_filter(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/qualidade-operacional/admin/registros/")
        self.assertEqual(response.status_code, 400)

    def test_suppress_auditado_hides_it_and_related_failure_then_restore(self):
        self.client.force_authenticate(self.admin)
        preview = self._preview("auditado", self.auditado.pk, "suppress")
        self.assertEqual(next(item["value"] for item in preview["impact"] if item["key"] == "related_failures"), 1)
        response = self._execute("auditado", self.auditado.pk, preview)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(QualidadeAuditado.objects.filter(pk=self.auditado.pk).exists())
        self.assertFalse(QualidadeFalha.objects.filter(pk=self.falha.pk).exists())
        self.assertTrue(QualidadeAuditado.all_objects.get(pk=self.auditado.pk).admin_suppressed)
        history = QualidadeRegistroHistorico.objects.get(estado__kind="auditado")
        self.assertEqual(len(history.changes["cascade_failures"]["after"]), 1)

        detail = self.client.get(f"/api/v1/qualidade-operacional/admin/registros/auditado/{self.auditado.pk}/")
        restore_preview = self.client.post(
            f"/api/v1/qualidade-operacional/admin/registros/auditado/{self.auditado.pk}/preview/",
            {"action": "restore", "reason": "Solicitacao operacional aprovada", "ticket": "PPLID-999", "expected_revision": detail.data["revision"]},
            format="json",
        ).data
        restored = self._execute("auditado", self.auditado.pk, restore_preview)
        self.assertEqual(restored.status_code, 201, restored.data)
        self.assertTrue(QualidadeAuditado.objects.filter(pk=self.auditado.pk).exists())
        self.assertTrue(QualidadeFalha.objects.filter(pk=self.falha.pk).exists())

    def test_update_survives_recreated_tsv_fact_and_idempotency_is_target_scoped(self):
        self.client.force_authenticate(self.admin)
        preview = self._preview("falha", self.falha.pk, "update", {"cenario": "Corrigido"})
        idem = uuid.uuid4()
        response = self._execute("falha", self.falha.pk, preview, idem=idem)
        self.assertEqual(response.status_code, 201, response.data)
        replay = self._execute("falha", self.falha.pk, preview, idem=idem)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertFalse(replay.data["created"])

        original_identity = QualidadeFalha.all_objects.get(pk=self.falha.pk).admin_identity
        incoming = QualidadeFalha(
            data=date(2026, 8, 20), data_analise=date(2026, 8, 20), protocolo="ADM-100",
            matricula="c100", tipo_falha="Manual", source_file="novo_nome.tsv",
        )
        apply_states_to_instances([incoming], "falha")
        self.assertEqual(incoming.admin_identity, original_identity)
        self.assertEqual(incoming.cenario, "Corrigido")

        other = QualidadeFalha.objects.create(protocolo="ADM-200", matricula="c200")
        mismatch = self.client.post(
            f"/api/v1/qualidade-operacional/admin/registros/falha/{other.pk}/actions/",
            {
                "action": preview["action"], "changes": {"cenario": "Corrigido"},
                "reason": "Solicitacao operacional aprovada", "ticket": "PPLID-999", "expected_revision": "0",
                "idempotency_key": str(idem), "preview_token": preview["preview_token"], "confirmation": preview["confirmation_phrase"],
            }, format="json",
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(QualidadeRegistroEstado.objects.count(), 1)

    def test_duplicate_tsv_identity_is_materialized_atomically(self):
        duplicate = QualidadeFalha.objects.create(
            data=date(2026, 8, 20), data_analise=date(2026, 8, 20), protocolo="ADM-100",
            matricula="c100", tipo_falha="Manual", source_file="outro.tsv",
        )
        self.client.force_authenticate(self.admin)
        preview = self._preview("falha", self.falha.pk, "suppress")
        self.assertEqual(next(item["value"] for item in preview["impact"] if item["key"] == "identity_peers"), 2)
        response = self._execute("falha", self.falha.pk, preview)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(QualidadeFalha.all_objects.get(pk=self.falha.pk).admin_suppressed)
        self.assertTrue(QualidadeFalha.all_objects.get(pk=duplicate.pk).admin_suppressed)

    def test_ambiguous_tsv_cascade_is_blocked(self):
        QualidadeFalha.objects.create(
            data=date(2026, 8, 20), data_analise=date(2026, 8, 21), protocolo="ADM-100",
            matricula="c100", tipo_falha="Automatico", source_file="falhas-2.tsv",
        )
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/qualidade-operacional/admin/registros/auditado/{self.auditado.pk}/preview/",
            {"action": "suppress", "reason": "Solicitacao operacional aprovada", "ticket": "PPLID-999", "expected_revision": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("related_failures", response.data["errors"])

    def test_child_cascade_cannot_be_restored_directly(self):
        self.client.force_authenticate(self.admin)
        response = self._execute("auditado", self.auditado.pk, self._preview("auditado", self.auditado.pk, "suppress"))
        self.assertEqual(response.status_code, 201, response.data)
        child = QualidadeFalha.all_objects.get(pk=self.falha.pk)
        response = self.client.post(
            f"/api/v1/qualidade-operacional/admin/registros/falha/{child.pk}/preview/",
            {"action": "restore", "reason": "Solicitacao operacional aprovada", "ticket": "PPLID-999", "expected_revision": str(child.admin_revision)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_numeric_change_is_normalized_between_preview_and_execute(self):
        self.client.force_authenticate(self.admin)
        preview = self._preview("falha", self.falha.pk, "update", {"id_cliente": "123"})
        response = self.client.post(
            f"/api/v1/qualidade-operacional/admin/registros/falha/{self.falha.pk}/actions/",
            {
                "action": "update", "changes": {"id_cliente": "123"},
                "reason": "Solicitacao operacional aprovada", "ticket": "PPLID-999",
                "expected_revision": preview["revision"], "idempotency_key": str(uuid.uuid4()),
                "preview_token": preview["preview_token"], "confirmation": preview["confirmation_phrase"],
            }, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(QualidadeFalha.all_objects.get(pk=self.falha.pk).id_cliente, 123)
        history = QualidadeRegistroHistorico.objects.get(estado__kind="falha")
        self.assertEqual(history.changes["id_cliente"], {"before": None, "after": 123})
