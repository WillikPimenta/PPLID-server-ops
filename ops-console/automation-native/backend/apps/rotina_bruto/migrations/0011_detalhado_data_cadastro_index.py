from django.db import migrations, models


INDEX_NAME = "rot_det_cad_report_idx"


def create_index(apps, schema_editor):
    concurrently = (
        "CONCURRENTLY " if schema_editor.connection.vendor == "postgresql" else ""
    )
    schema_editor.execute(
        f"CREATE INDEX {concurrently}IF NOT EXISTS {INDEX_NAME} "
        "ON rotina_detalhado_bruto_record (data_cadastro, report_date)"
    )


def drop_index(apps, schema_editor):
    concurrently = (
        "CONCURRENTLY " if schema_editor.connection.vendor == "postgresql" else ""
    )
    schema_editor.execute(f"DROP INDEX {concurrently}IF EXISTS {INDEX_NAME}")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("rotina_bruto", "0010_ged_irregularidade_msisdn_length"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(create_index, drop_index),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="rotinadetalhadobrutorecord",
                    index=models.Index(
                        fields=["data_cadastro", "report_date"],
                        name=INDEX_NAME,
                    ),
                ),
            ],
        ),
    ]
