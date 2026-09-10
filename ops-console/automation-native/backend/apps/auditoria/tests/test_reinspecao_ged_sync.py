"""Testes do sync GED irregularidade bruto → qualidade_pendente_reinspecao."""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteReinspecao,
    ReinspecaoGedDivergencia,
    ReinspecaoOcorrencia,
)
from apps.auditoria.services.reinspecao_ged_finalizados import protocolos_finalizados_ged
from apps.auditoria.services.reinspecao_ged_sync import sync_reinspecao_ged_to_db
from apps.rotina_bruto.models import RotinaGedIrregularidadeTratadoRecord
from apps.auditoria.testing.reinspecao_mapping import seed_test_reinspecao_mapping


def _build_ged_csv_bytes() -> bytes:
    return (
        "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
        "Descrição das Irregularidades\n"
        "18426529;15/07/2026 17:34;-;C19131Q;IC - 175 - CPF ausente\n"
        "22417758;21/07/2026 11:23;21/07/2026 11:23;C96333A;IC - respondido\n"
        "23718475;16/07/2026 16:45;;c96333a;IC - ok\n"
        "26000001;17/07/2026 10:00;-;12345;IC - matricula invalida\n"
        "27000001;18/07/2026 10:00;-;C19131Q;\n"
    ).encode("utf-8")


@override_settings(ACCESS_ENFORCEMENT=False)
class ReinspecaoGedSyncTests(TestCase):
    def setUp(self):
        seed_test_reinspecao_mapping()

    def test_sync_inserts_filtered_rows_without_duplicates(self):
        csv_path = Path(self._tmpdir()) / "ged-irregularidade-bruto_202606_1.csv"
        csv_path.write_bytes(_build_ged_csv_bytes())

        first = sync_reinspecao_ged_to_db(path=csv_path)
        self.assertEqual(first["created"], 3)
        self.assertEqual(first["skipped_filtered"], 2)
        self.assertEqual(first["skipped_finalized_ged"], 0)
        self.assertEqual(QualidadePendenteReinspecao.objects.count(), 3)
        self.assertEqual(ReinspecaoOcorrencia.objects.count(), 3)
        self.assertEqual(
            ReinspecaoOcorrencia.objects.filter(
                status=ReinspecaoOcorrencia.STATUS_PENDENTE,
                pendente__isnull=False,
                source="bot_production",
            ).count(),
            3,
        )
        self.assertEqual(first["skipped_ledger"], 0)

        pendentes = list(QualidadePendenteReinspecao.objects.order_by("protocolo"))
        self.assertEqual(pendentes[0].usuario, "c19131q")
        self.assertEqual(pendentes[1].usuario, "c96333a")
        self.assertEqual(pendentes[2].usuario, "sistema")
        self.assertEqual(pendentes[0].brflow_parsed.get("source"), "bot_production")
        self.assertEqual(pendentes[0].codigo_irregularidade, "175")
        self.assertEqual(pendentes[0].motivo_falha, "Não sinalizada - Irregularidade")
        self.assertEqual(pendentes[0].etapa_falha, "Reclassificação")
        self.assertEqual(pendentes[0].mapping_version, "test-v1")
        self.assertEqual(pendentes[0].mapping_source_hash, "a" * 64)
        self.assertIsNotNone(pendentes[0].mapping_applied_at)
        self.assertEqual(first["source_hash"], hashlib.sha256(_build_ged_csv_bytes()).hexdigest())
        self.assertEqual(pendentes[0].brflow_parsed["source_hash"], first["source_hash"])

        second = sync_reinspecao_ged_to_db(path=csv_path)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skipped_existing"], 3)
        self.assertEqual(second["skipped_finalized_ged"], 0)
        self.assertEqual(QualidadePendenteReinspecao.objects.count(), 3)
        self.assertEqual(ReinspecaoOcorrencia.objects.count(), 3)

    def test_sync_skips_protocolos_ja_tratados(self):
        csv_path = Path(self._tmpdir()) / "ged-irregularidade-bruto_202606_2.csv"
        csv_path.write_bytes(_build_ged_csv_bytes())

        AuditoriaFalhaCadastro.objects.create(
            protocolo="18426529",
            origem="reinspecao",
            usuario="c19131q",
            descricao_irregularidades="IC - 175 - CPF ausente",
            data_contestacao=timezone.make_aware(datetime(2026, 7, 15, 17, 34)),
        )

        result = sync_reinspecao_ged_to_db(path=csv_path)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["skipped_finalized_ged"], 0)
        self.assertEqual(result["divergences"]["created"], 1)
        self.assertEqual(ReinspecaoGedDivergencia.objects.count(), 1)
        self.assertFalse(
            QualidadePendenteReinspecao.objects.filter(protocolo="18426529").exists()
        )

    def test_sync_imports_additional_irregularidade_same_protocol(self):
        csv_path = Path(self._tmpdir()) / "ged-irregularidade-bruto_202606_5.csv"
        csv_path.write_bytes(
            (
                "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
                "Descrição das Irregularidades\n"
                "36553981;22/07/2026 09:05;-;C19011Q;IC - 175 - CPF/CNPJ no CPF/RG Ausente\n"
                "36553981;22/07/2026 09:05;-;C19011Q;IC - 668 - Plano no GED diverge\n"
            ).encode("utf-8")
        )

        result = sync_reinspecao_ged_to_db(path=csv_path)
        self.assertEqual(result["created"], 2)
        self.assertEqual(
            QualidadePendenteReinspecao.objects.filter(protocolo="36553981").count(),
            2,
        )

    def test_sync_does_not_block_with_legacy_day_only_ged_snapshot(self):
        RotinaGedIrregularidadeTratadoRecord.objects.create(
            report_date=date(2026, 6, 1),
            periodo=1,
            protocolo=18426529,
            data_resposta=date(2026, 7, 22),
        )
        csv_path = Path(self._tmpdir()) / "ged-irregularidade-bruto_202606_3.csv"
        csv_path.write_bytes(_build_ged_csv_bytes())

        result = sync_reinspecao_ged_to_db(path=csv_path)
        self.assertEqual(result["created"], 3)
        self.assertEqual(result["skipped_finalized_ged"], 0)
        self.assertEqual(result["skipped_existing"], 0)
        self.assertTrue(
            QualidadePendenteReinspecao.objects.filter(protocolo="18426529").exists()
        )

    def test_sync_answered_exact_occurrence_resolves_divergence(self):
        data_contestacao = timezone.make_aware(datetime(2026, 7, 15, 17, 34))
        AuditoriaFalhaCadastro.objects.create(
            protocolo="18426529",
            origem="reinspecao",
            tipo_registro="reinspecao",
            tipo_falha="reinspecao",
            usuario="c19131q",
            descricao_irregularidades="IC - 175 - CPF ausente",
            data_contestacao=data_contestacao,
        )
        tmpdir = Path(self._tmpdir())
        open_path = tmpdir / "ged-open.csv"
        open_path.write_bytes(
            (
                "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
                "Descrição das Irregularidades\n"
                "18426529;15/07/2026 17:34;-;C19131Q;IC - 175 - CPF ausente\n"
            ).encode()
        )
        answered_path = tmpdir / "ged-answered.csv"
        answered_path.write_bytes(
            (
                "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
                "Descrição das Irregularidades\n"
                "18426529;15/07/2026 17:34;16/07/2026 09:00;C19131Q;"
                "IC - 175 - CPF ausente\n"
            ).encode()
        )

        first = sync_reinspecao_ged_to_db(path=open_path)
        second = sync_reinspecao_ged_to_db(path=answered_path)

        self.assertEqual(first["divergences"]["created"], 1)
        self.assertEqual(second["divergences"]["resolved"], 1)
        self.assertEqual(second["created"], 0)
        divergence = ReinspecaoGedDivergencia.objects.get()
        self.assertEqual(divergence.status, ReinspecaoGedDivergencia.STATUS_RESOLVIDA)

    def test_sync_pending_exact_occurrence_is_normal_skip_without_divergence(self):
        QualidadePendenteReinspecao.objects.create(
            protocolo="18426529",
            usuario="c19131q",
            descricao_irregularidades="IC - 175 - CPF ausente",
            data_contestacao=timezone.make_aware(datetime(2026, 7, 15, 17, 34)),
            contexto="reinspecao",
            tipo_falha="reinspecao",
        )
        csv_path = Path(self._tmpdir()) / "ged-pending.csv"
        csv_path.write_bytes(_build_ged_csv_bytes())

        result = sync_reinspecao_ged_to_db(path=csv_path)

        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["divergences"]["created"], 0)
        self.assertFalse(ReinspecaoGedDivergencia.objects.exists())

    def test_sync_keeps_ic668_mirror_response_eligible_for_divergence(self):
        descricao = (
            "IC - 668 - Plano no GED diverge do Plano no Contrato de Habilitação"
        )
        data_contestacao = timezone.make_aware(datetime(2026, 8, 20, 10, 0))
        AuditoriaFalhaCadastro.objects.create(
            protocolo="66800001",
            origem="reinspecao",
            tipo_registro="reinspecao",
            tipo_falha="reinspecao",
            usuario="c19131q",
            descricao_irregularidades=descricao,
            data_contestacao=data_contestacao,
        )
        csv_path = Path(self._tmpdir()) / "ged-ic668-mirror.csv"
        csv_path.write_bytes(
            (
                "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
                "Descrição das Irregularidades\n"
                f"66800001;20/08/2026 10:00;20/08/2026 10:00;C19131Q;{descricao}\n"
            ).encode()
        )

        result = sync_reinspecao_ged_to_db(path=csv_path)

        self.assertEqual(result["divergences"]["created"], 1)
        self.assertEqual(result["created"], 0)
        self.assertEqual(ReinspecaoGedDivergencia.objects.count(), 1)

    def test_sync_refuses_new_rows_without_active_approved_mapping(self):
        from apps.auditoria.models import ReinspecaoIrregularidadeMapping

        ReinspecaoIrregularidadeMapping.objects.all().delete()
        csv_path = Path(self._tmpdir()) / "ged-no-mapping.csv"
        csv_path.write_bytes(_build_ged_csv_bytes())

        with self.assertRaisesMessage(ValueError, "Nenhuma matriz de Reinspeção"):
            sync_reinspecao_ged_to_db(path=csv_path)

        self.assertFalse(QualidadePendenteReinspecao.objects.exists())

    def test_sync_does_not_block_same_protocol_from_ambiguous_legacy_snapshot(self):
        RotinaGedIrregularidadeTratadoRecord.objects.create(
            report_date=date(2026, 6, 1),
            periodo=1,
            protocolo=23718475,
            data_resposta=date(2026, 7, 20),
        )
        csv_path = Path(self._tmpdir()) / "ged-irregularidade-bruto_202606_4.csv"
        csv_path.write_bytes(
            (
                "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
                "Descrição das Irregularidades\n"
                "18426529;15/07/2026 17:34;-;C19131Q;IC - aberto\n"
                "23718475;16/07/2026 16:45;-;c96333a;IC - finalizado no GED\n"
            ).encode("utf-8")
        )

        result = sync_reinspecao_ged_to_db(path=csv_path)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["skipped_finalized_ged"], 0)
        self.assertEqual(
            set(
                QualidadePendenteReinspecao.objects.values_list("protocolo", flat=True)
            ),
            {"18426529", "23718475"},
        )

    def test_protocolos_finalizados_ged_helper(self):
        RotinaGedIrregularidadeTratadoRecord.objects.create(
            report_date=date(2026, 6, 1),
            periodo=2,
            protocolo=26000001,
            data_resposta=date(2026, 7, 18),
        )
        RotinaGedIrregularidadeTratadoRecord.objects.create(
            report_date=date(2026, 6, 1),
            periodo=2,
            protocolo=27000001,
            data_resposta=None,
        )

        finalized = protocolos_finalizados_ged(["26000001", "27000001", "invalid"])
        self.assertEqual(finalized, {"26000001"})

    def _tmpdir(self) -> str:
        import tempfile

        return tempfile.mkdtemp()
