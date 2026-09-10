from __future__ import annotations

from dataclasses import replace
from importlib import import_module
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteReinspecao,
    ReinspecaoIrregularidadeMapping,
    ReinspecaoMappingRun,
)
from apps.auditoria.services.qualidade_promocao import promover_pendente_reinspecao
from apps.auditoria.services.reinspecao_fila import _apply_etapa_fields
from apps.auditoria.services.reinspecao_import import (
    ParsedReinspecaoRow,
    ReinspecaoImportPreview,
    apply_mapping_to_preview,
)
from apps.auditoria.services.reinspecao_mapping import (
    STATUS_AMBIGUOUS,
    STATUS_MATCHED,
    STATUS_UNMATCHED,
    MappingDataset,
    MappingVariant,
    ReinspecaoMappingResolver,
    _dataset_token,
    export_mapping_json,
    extract_irregularity_key,
    load_mapping_source,
    normalize_display_text,
)
from apps.auditoria.services.reinspecao_mapping_backfill import build_backfill_preview
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeIntranetProjection,
)

User = get_user_model()
SOURCE_HASH = "b" * 64
MAPPING_VERSION = "test-2026-04-08"


def variant(
    code: str,
    *,
    row: int,
    scenario: str = "Cenário seguro",
    stage: str = "Etapa segura",
    description: str = "Descrição canônica",
    classification: str = "IC",
) -> MappingVariant:
    return MappingVariant(
        source_row=row,
        classification=classification,
        code_original=code,
        code_normalized=code.casefold(),
        description_original=description,
        description_normalized=normalize_display_text(description).casefold(),
        island="Ilha",
        stage=stage,
        document_type="Documento",
        category="Procedimento",
        scenario=scenario,
        mapping_version=MAPPING_VERSION,
        source_hash=SOURCE_HASH,
    )


class ReinspecaoMappingResolverTests(SimpleTestCase):
    def test_extracts_code_and_preserves_leading_zero(self):
        extracted = extract_irregularity_key(" IC\u00a0-\u200b00445\u200d-\ufeff Assinatura ")
        self.assertTrue(extracted.parsed)
        self.assertEqual(extracted.classification, "IC")
        self.assertEqual(extracted.code, "00445")
        self.assertEqual(extracted.description, "Assinatura")

    def test_unknown_and_malformed_are_explicitly_unmatched(self):
        resolver = ReinspecaoMappingResolver([variant("445", row=2)])
        unknown = resolver.resolve("CO - 999 - Desconhecida")
        malformed = resolver.resolve("sem código")
        self.assertEqual(unknown.scenario_status, STATUS_UNMATCHED)
        self.assertEqual(malformed.stage_status, STATUS_UNMATCHED)
        self.assertFalse(malformed.extracted.parsed)

    def test_duplicate_is_resolved_per_target_field(self):
        resolver = ReinspecaoMappingResolver(
            [
                variant("220", row=128, stage="Etapa A"),
                variant("220", row=134, stage="Etapa B"),
            ]
        )
        result = resolver.resolve("IC - 220 - Nome divergente")
        self.assertEqual(result.scenario, "Cenário seguro")
        self.assertEqual(result.scenario_status, STATUS_MATCHED)
        self.assertEqual(result.stage, "")
        self.assertEqual(result.stage_status, STATUS_AMBIGUOUS)
        self.assertEqual(len(result.candidates), 2)

    def test_duplicate_with_equal_targets_is_safe(self):
        resolver = ReinspecaoMappingResolver(
            [variant("172", row=47), variant("172", row=70)]
        )
        result = resolver.resolve("CO - 172 - Documento")
        self.assertEqual(result.scenario_status, STATUS_MATCHED)
        self.assertEqual(result.stage_status, STATUS_MATCHED)

    def test_correction_requires_exact_composite_description(self):
        resolver = ReinspecaoMappingResolver(
            [
                variant(
                    "correção",
                    row=152,
                    stage="Correção Nome",
                    scenario="Correção de dados",
                    description="Nome",
                    classification="CORREÇÃO",
                ),
                variant(
                    "correção",
                    row=153,
                    stage="Correção Nome",
                    scenario="Correção de dados",
                    description="Sobrenome",
                    classification="CORREÇÃO",
                ),
            ]
        )
        matched = resolver.resolve("Correção - Correção - Nome")
        unmatched = resolver.resolve("Correção - Correção - Nome completo")
        self.assertEqual(matched.stage_status, STATUS_MATCHED)
        self.assertEqual(unmatched.stage_status, STATUS_UNMATCHED)


class ReinspecaoMappingImportCommandTests(TestCase):
    def test_repeated_version_is_idempotent_and_changed_version_replaces_active_rows(self):
        ReinspecaoIrregularidadeMapping.objects.all().delete()
        ReinspecaoMappingRun.objects.all().delete()
        source = (
            Path(settings.BASE_DIR)
            / "apps"
            / "auditoria"
            / "data"
            / "reinspecao_mapping"
            / "matriz_2026-04-08_19572dd2.json"
        )
        dataset = load_mapping_source(source)

        for _ in range(2):
            call_command(
                "import_reinspecao_mapping",
                "--source",
                str(source),
                "--apply",
                "--expected-sha256",
                dataset.source_hash,
                "--preview-token",
                dataset.preview_token,
                "--review-status",
                ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
                stdout=StringIO(),
            )

        current = ReinspecaoIrregularidadeMapping.objects.filter(
            source_hash=dataset.source_hash
        )
        self.assertEqual(current.count(), 163)
        self.assertEqual(current.filter(active=True).count(), 163)
        self.assertEqual(
            ReinspecaoMappingRun.objects.filter(
                kind=ReinspecaoMappingRun.KIND_IMPORT
            ).count(),
            2,
        )

        new_version = "2026-04-09-test"
        new_hash = "c" * 64
        changed_variants = tuple(
            replace(
                row,
                stage="Etapa revisada" if index == 0 else row.stage,
                mapping_version=new_version,
                source_hash=new_hash,
            )
            for index, row in enumerate(dataset.variants)
        )
        changed_dataset = MappingDataset(
            mapping_version=new_version,
            source_hash=new_hash,
            sheet_name=dataset.sheet_name,
            source_range=dataset.source_range,
            headers=dataset.headers,
            variants=changed_variants,
            preview_token=_dataset_token(
                version=new_version,
                source_hash=new_hash,
                variants=changed_variants,
            ),
        )
        with TemporaryDirectory() as temp_dir:
            changed_source = Path(temp_dir) / "mapping.json"
            export_mapping_json(changed_dataset, changed_source)
            call_command(
                "import_reinspecao_mapping",
                "--source",
                str(changed_source),
                "--apply",
                "--expected-sha256",
                new_hash,
                "--preview-token",
                changed_dataset.preview_token,
                "--review-status",
                ReinspecaoIrregularidadeMapping.REVIEW_PENDING,
                stdout=StringIO(),
            )
            self.assertEqual(current.filter(active=True).count(), 163)
            self.assertFalse(
                ReinspecaoIrregularidadeMapping.objects.filter(
                    source_hash=new_hash,
                    active=True,
                ).exists()
            )
            call_command(
                "import_reinspecao_mapping",
                "--source",
                str(changed_source),
                "--apply",
                "--expected-sha256",
                new_hash,
                "--preview-token",
                changed_dataset.preview_token,
                "--review-status",
                ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
                stdout=StringIO(),
            )

        self.assertFalse(current.filter(active=True).exists())
        revised = ReinspecaoIrregularidadeMapping.objects.filter(
            source_hash=new_hash,
            active=True,
        )
        self.assertEqual(revised.count(), 163)
        self.assertEqual(revised.order_by("source_row").first().etapa, "Etapa revisada")


class ReinspecaoMappingSeedMigrationTests(TestCase):
    def setUp(self):
        ReinspecaoIrregularidadeMapping.objects.all().delete()
        ReinspecaoMappingRun.objects.all().delete()
        self.migration = import_module(
            "apps.auditoria.migrations.0061_seed_reinspecao_mapping"
        )

    def test_seeds_canonical_approved_mapping_idempotently(self):
        from django.apps import apps

        self.migration.seed_reinspecao_mapping(apps, None)
        self.migration.seed_reinspecao_mapping(apps, None)

        rows = ReinspecaoIrregularidadeMapping.objects.filter(
            source_hash=self.migration.EXPECTED_SOURCE_HASH,
            active=True,
            review_status=ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
        )
        self.assertEqual(rows.count(), self.migration.EXPECTED_ROWS)
        self.assertEqual(
            ReinspecaoMappingRun.objects.filter(
                kind=ReinspecaoMappingRun.KIND_IMPORT,
                status=ReinspecaoMappingRun.STATUS_APPLIED,
                source_hash=self.migration.EXPECTED_SOURCE_HASH,
            ).count(),
            1,
        )

    def test_preserves_an_existing_approved_active_mapping(self):
        from django.apps import apps

        existing = variant("999", row=2)
        ReinspecaoIrregularidadeMapping.objects.create(
            codigo_original=existing.code_original,
            codigo_normalizado=existing.code_normalized,
            classificacao=existing.classification,
            descricao_original=existing.description_original,
            descricao_normalizada=existing.description_normalized,
            ilha=existing.island,
            etapa=existing.stage,
            tipo_documento=existing.document_type,
            categoria=existing.category,
            cenario=existing.scenario,
            active=True,
            mapping_version=existing.mapping_version,
            source_hash=existing.source_hash,
            source_row=existing.source_row,
            review_status=ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
        )

        self.migration.seed_reinspecao_mapping(apps, None)

        self.assertEqual(ReinspecaoIrregularidadeMapping.objects.count(), 1)
        self.assertFalse(
            ReinspecaoIrregularidadeMapping.objects.filter(
                source_hash=self.migration.EXPECTED_SOURCE_HASH
            ).exists()
        )


@override_settings(ACCESS_ENFORCEMENT=False)
class ReinspecaoMappingFlowTests(TestCase):
    def setUp(self):
        ReinspecaoIrregularidadeMapping.objects.all().delete()
        self.user = User.objects.create_superuser(
            username="mapping_user",
            email="mapping@test.local",
            password="test12345",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        rows = [
            variant("445", row=2),
            variant("220", row=128, stage="Etapa A"),
            variant("220", row=134, stage="Etapa B"),
        ]
        ReinspecaoIrregularidadeMapping.objects.bulk_create(
            [
                ReinspecaoIrregularidadeMapping(
                    codigo_original=row.code_original,
                    codigo_normalizado=row.code_normalized,
                    classificacao=row.classification,
                    descricao_original=row.description_original,
                    descricao_normalizada=row.description_normalized,
                    ilha=row.island,
                    etapa=row.stage,
                    tipo_documento=row.document_type,
                    categoria=row.category,
                    cenario=row.scenario,
                    active=True,
                    mapping_version=row.mapping_version,
                    source_hash=row.source_hash,
                    source_row=row.source_row,
                    review_status=ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
                )
                for row in rows
            ]
        )

    def test_preview_reports_coverage_and_ambiguity(self):
        preview = ReinspecaoImportPreview(
            sheet_name="CSV",
            header_row=1,
            total_falhas=2,
            rows=[
                ParsedReinspecaoRow("1", "c11111q", "IC - 445 - Assinatura", excel_row=2),
                ParsedReinspecaoRow("2", "c11111q", "IC - 220 - Nome", excel_row=3),
            ],
        )
        apply_mapping_to_preview(preview)
        self.assertEqual(preview.mapping_summary["matched"], 2)
        self.assertEqual(preview.mapping_summary["ambiguous_stage"], 1)
        self.assertEqual(preview.rows[0].codigo_irregularidade, "445")
        self.assertEqual(preview.rows[1].etapa_mapeada, "")

    def test_api_import_persists_mapping_without_changing_dedupe_key(self):
        content = (
            "Protocolo;Data da contestação;Data de resposta;Matricula do Inspetor;"
            "Descrição das Irregularidades\n"
            "9001;20/08/2026;;C11111Q;IC - 445 - Assinatura\n"
        ).encode("utf-8")
        response = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/validar/",
            {"file": SimpleUploadedFile("mapping.csv", content, content_type="text/csv")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["preview"]["mapping_summary"]["matched"], 1)
        confirmed = self.client.post(
            "/api/v1/qualidade/auditoria/reinspecao/importacao/confirmar/",
            {"import_token": response.data["import_token"]},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.data)
        pending = QualidadePendenteReinspecao.objects.get(protocolo="9001")
        self.assertEqual(pending.codigo_irregularidade, "445")
        self.assertEqual(pending.motivo_falha, "Cenário seguro")
        self.assertEqual(pending.etapa_falha, "Etapa segura")
        self.assertIn("import_dedupe_key", pending.brflow_parsed)

    def test_promotion_preserves_mapping_metadata(self):
        pending = QualidadePendenteReinspecao.objects.create(
            protocolo="9002",
            usuario="c11111q",
            descricao_irregularidades="IC - 445 - Assinatura",
            tipo_falha="reinspecao",
            codigo_irregularidade="445",
            motivo_falha="Cenário seguro",
            etapa_falha="Etapa segura",
            mapping_scenario_status=STATUS_MATCHED,
            mapping_stage_status=STATUS_MATCHED,
            mapping_version=MAPPING_VERSION,
            mapping_source_hash=SOURCE_HASH,
        )
        treated = promover_pendente_reinspecao(pending, user=self.user)
        self.assertEqual(treated.codigo_irregularidade, "445")
        self.assertEqual(treated.motivo_falha, "Cenário seguro")
        self.assertEqual(treated.mapping_source_hash, SOURCE_HASH)

    def test_completion_does_not_clear_or_overwrite_canonical_mapping(self):
        pending = QualidadePendenteReinspecao(
            protocolo="9004",
            usuario="c11111q",
            descricao_irregularidades="IC - 445 - Assinatura",
            codigo_irregularidade="445",
            motivo_falha="Cenário seguro",
            etapa_falha="Etapa segura",
            mapping_scenario_status=STATUS_MATCHED,
            mapping_stage_status=STATUS_MATCHED,
            mapping_version=MAPPING_VERSION,
            mapping_source_hash=SOURCE_HASH,
        )
        _apply_etapa_fields(
            pending,
            {
                "situacao": "Procedente",
                "agente": "c11111q",
                "motivo_falha": "Outro cenário",
                "etapa_falha": "Outra etapa",
            },
        )
        self.assertEqual(pending.motivo_falha, "Cenário seguro")
        self.assertEqual(pending.etapa_falha, "Etapa segura")
        self.assertEqual(pending.mapping_scenario_status, "conflict")
        self.assertEqual(pending.mapping_stage_status, "conflict")

    def test_backfill_and_rollback_preserve_manual_change(self):
        source = AuditoriaFalhaCadastro.objects.create(
            protocolo="9003",
            usuario="c11111q",
            descricao_irregularidades="IC - 445 - Assinatura",
            tipo_falha="reinspecao",
            tipo_registro="reinspecao",
            origem="reinspecao",
        )
        auditado = QualidadeAuditado.objects.create(
            data=date(2026, 8, 20), protocolo="9003", cenario="", etapa=""
        )
        QualidadeIntranetProjection.objects.create(
            source=source,
            auditado=auditado,
            mapping_version=6,
        )
        preview = build_backfill_preview()
        call_command(
            "backfill_reinspecao_mapping",
            "--apply",
            "--mapping-version",
            MAPPING_VERSION,
            "--expected-source-hash",
            SOURCE_HASH,
            "--preview-token",
            preview.preview_token,
            stdout=StringIO(),
        )
        source.refresh_from_db()
        auditado.refresh_from_db()
        self.assertEqual(source.codigo_irregularidade, "445")
        self.assertEqual(auditado.cenario, "Cenário seguro")
        second_preview = build_backfill_preview()
        self.assertEqual(second_preview.report["counts"].get("changed", 0), 0)
        run = ReinspecaoMappingRun.objects.get(kind=ReinspecaoMappingRun.KIND_BACKFILL)

        source.motivo_falha = "Valor manual posterior"
        source.save(update_fields=["motivo_falha", "updated_at"])
        call_command(
            "backfill_reinspecao_mapping",
            "--rollback",
            "--run-id",
            str(run.pk),
            stdout=StringIO(),
        )
        source.refresh_from_db()
        self.assertEqual(source.motivo_falha, "Valor manual posterior")
        self.assertEqual(source.codigo_irregularidade, "")
