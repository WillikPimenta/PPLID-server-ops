from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.auditoria.models import QualidadePendenteReinspecao, ReinspecaoOcorrencia
from apps.auditoria.services.reinspecao_ocorrencias import (
    link_reinspecao_occurrence,
    reserve_reinspecao_occurrence,
)
from apps.auditoria.services.qualidade_promocao import promover_pendente_reinspecao


class ReinspecaoOcorrenciaTests(TestCase):
    def setUp(self):
        self.data_contestacao = timezone.make_aware(datetime(2026, 8, 28, 9, 42, 15))
        self.payload = {
            "contexto": "reinspecao",
            "protocolo": "18426529",
            "descricao_irregularidades": "IC - 175 - CPF ausente",
            "data_contestacao": self.data_contestacao,
            "source": "bot_production",
            "source_file": "ged.csv",
            "source_hash": "a" * 64,
        }

    def test_reservation_is_idempotent_by_canonical_key(self):
        first = reserve_reinspecao_occurrence(**self.payload)
        second = reserve_reinspecao_occurrence(**self.payload)

        self.assertTrue(first.acquired)
        self.assertEqual(first.action, "created")
        self.assertFalse(second.acquired)
        self.assertEqual(second.action, "seen")
        self.assertEqual(ReinspecaoOcorrencia.objects.count(), 1)
        occurrence = ReinspecaoOcorrencia.objects.get()
        self.assertEqual(occurrence.seen_count, 2)
        self.assertEqual(len(occurrence.occurrence_key), 64)

    def test_link_pending_is_idempotent_and_validates_identity(self):
        reservation = reserve_reinspecao_occurrence(**self.payload)
        pendente = QualidadePendenteReinspecao.objects.create(
            contexto="reinspecao",
            protocolo=self.payload["protocolo"],
            descricao_irregularidades=self.payload["descricao_irregularidades"],
            data_contestacao=self.data_contestacao,
            usuario="c19131q",
            tipo_falha="reinspecao",
        )

        linked, changed = link_reinspecao_occurrence(
            reservation.ocorrencia,
            pendente=pendente,
        )
        repeated, changed_again = link_reinspecao_occurrence(linked, pendente=pendente)

        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(repeated.status, ReinspecaoOcorrencia.STATUS_PENDENTE)
        self.assertEqual(repeated.pendente_id, pendente.pk)

    def test_integrity_conflict_recovers_existing_reservation(self):
        existing = reserve_reinspecao_occurrence(**self.payload).ocorrencia
        existing.seen_count = 1
        existing.save(update_fields=["seen_count"])

        with (
            patch.object(ReinspecaoOcorrencia.objects, "select_for_update") as locked_manager,
            patch.object(
                ReinspecaoOcorrencia.objects,
                "create",
                side_effect=IntegrityError("occurrence_key unique"),
            ),
        ):
            locked_manager.return_value.filter.return_value.first.return_value = None
            locked_manager.return_value.get.return_value = existing
            result = reserve_reinspecao_occurrence(**self.payload)

        self.assertEqual(result.action, "conflict")
        self.assertFalse(result.acquired)
        existing.refresh_from_db()
        self.assertEqual(existing.seen_count, 2)
        self.assertEqual(ReinspecaoOcorrencia.objects.count(), 1)

    def test_promotion_moves_ledger_link_from_pending_to_treated(self):
        reservation = reserve_reinspecao_occurrence(**self.payload)
        pendente = QualidadePendenteReinspecao.objects.create(
            contexto="reinspecao",
            protocolo=self.payload["protocolo"],
            descricao_irregularidades=self.payload["descricao_irregularidades"],
            data_contestacao=self.data_contestacao,
            usuario="c19131q",
            tipo_falha="reinspecao",
        )
        link_reinspecao_occurrence(reservation.ocorrencia, pendente=pendente)

        tratado = promover_pendente_reinspecao(pendente)

        occurrence = ReinspecaoOcorrencia.objects.get(pk=reservation.ocorrencia.pk)
        self.assertEqual(occurrence.status, ReinspecaoOcorrencia.STATUS_TRATADA)
        self.assertIsNone(occurrence.pendente_id)
        self.assertEqual(occurrence.tratado_id, tratado.pk)
        self.assertFalse(QualidadePendenteReinspecao.objects.filter(pk=pendente.pk).exists())
