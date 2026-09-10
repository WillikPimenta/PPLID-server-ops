from __future__ import annotations

from apps.auditoria.models import ReinspecaoIrregularidadeMapping


def seed_test_reinspecao_mapping() -> None:
    ReinspecaoIrregularidadeMapping.objects.all().delete()
    rows = []
    for source_row, code in enumerate(("175", "445", "668"), start=2):
        rows.append(
            ReinspecaoIrregularidadeMapping(
                codigo_original=code,
                codigo_normalizado=code,
                classificacao="IC",
                descricao_original=f"Irregularidade {code}",
                descricao_normalizada=f"irregularidade {code}",
                ilha="Reclassificação",
                etapa="Reclassificação",
                tipo_documento="Documento de Identificação",
                categoria="Procedimento",
                cenario="Não sinalizada - Irregularidade",
                active=True,
                mapping_version="test-v1",
                source_hash="a" * 64,
                source_row=source_row,
                review_status=ReinspecaoIrregularidadeMapping.REVIEW_APPROVED,
            )
        )
    ReinspecaoIrregularidadeMapping.objects.bulk_create(rows)
