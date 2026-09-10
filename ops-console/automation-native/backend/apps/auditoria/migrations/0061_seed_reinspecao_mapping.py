import json
from pathlib import Path

from django.db import migrations
from django.utils import timezone


SOURCE_FILENAME = "matriz_2026-04-08_19572dd2.json"
EXPECTED_VERSION = "2026-04-08"
EXPECTED_SOURCE_HASH = (
    "19572dd2500c8b513b433c5c502681ac7997f66a5680d3d96b2fb7ef6b566406"
)
EXPECTED_ROWS = 163


def seed_reinspecao_mapping(apps, schema_editor):
    del schema_editor
    Mapping = apps.get_model("auditoria", "ReinspecaoIrregularidadeMapping")
    MappingRun = apps.get_model("auditoria", "ReinspecaoMappingRun")

    # Preserve any matrix that was already reviewed and activated operationally.
    if Mapping.objects.filter(active=True, review_status="approved").exists():
        return

    source_path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "reinspecao_mapping"
        / SOURCE_FILENAME
    )
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    if (
        payload.get("mapping_version") != EXPECTED_VERSION
        or payload.get("source_hash") != EXPECTED_SOURCE_HASH
        or len(rows) != EXPECTED_ROWS
    ):
        raise RuntimeError("A matriz canônica de Reinspeção não corresponde ao manifesto esperado.")

    Mapping.objects.bulk_create(
        [
            Mapping(
                codigo_original=row["code_original"],
                codigo_normalizado=row["code_normalized"],
                classificacao=row["classification"],
                descricao_original=row["description_original"],
                descricao_normalizada=row["description_normalized"],
                ilha=row["island"],
                etapa=row["stage"],
                tipo_documento=row["document_type"],
                categoria=row["category"],
                cenario=row["scenario"],
                active=True,
                mapping_version=EXPECTED_VERSION,
                source_hash=EXPECTED_SOURCE_HASH,
                source_row=row["source_row"],
                review_status="approved",
            )
            for row in rows
        ],
        batch_size=500,
    )
    MappingRun.objects.create(
        kind="import",
        status="applied",
        mapping_version=EXPECTED_VERSION,
        source_hash=EXPECTED_SOURCE_HASH,
        preview_token=payload.get("preview_token") or "",
        manifest={
            "mode": "migration_seed",
            "source_label": SOURCE_FILENAME,
            "rows": EXPECTED_ROWS,
            "review_status": "approved",
        },
        finished_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0060_merge_20260820_1402"),
    ]

    operations = [
        migrations.RunPython(seed_reinspecao_mapping, migrations.RunPython.noop),
    ]
