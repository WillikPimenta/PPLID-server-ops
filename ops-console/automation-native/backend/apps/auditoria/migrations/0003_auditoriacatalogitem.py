from django.db import migrations, models


def seed_catalog_items(apps, schema_editor):
    AuditoriaCatalogItem = apps.get_model("auditoria", "AuditoriaCatalogItem")
    from apps.auditoria.services.catalog_items import CATALOG_DEFAULTS

    for catalog, values in CATALOG_DEFAULTS.items():
        if AuditoriaCatalogItem.objects.filter(catalog=catalog).exists():
            continue
        AuditoriaCatalogItem.objects.bulk_create(
            [
                AuditoriaCatalogItem(
                    catalog=catalog,
                    value=value,
                    label=value,
                    sort_order=index,
                    active=True,
                )
                for index, value in enumerate(values)
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0002_alter_auditoriafalhacadastro_tipo_falha"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditoriaCatalogItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "catalog",
                    models.CharField(
                        choices=[
                            ("modulo", "Módulo"),
                            ("tipo_falha", "Tipo de falha"),
                            ("novo_resultado", "Novo resultado"),
                            ("sinalizacao", "Sinalização"),
                            ("etapa_falha", "Etapa da falha"),
                            ("nivel_dificuldade", "Nível de dificuldade"),
                            ("tipo_documento", "Tipo de documento"),
                            ("uf_documento", "UF do documento"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("value", models.CharField(max_length=255)),
                ("label", models.CharField(blank=True, default="", max_length=255)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "auditoria_catalog_item",
                "ordering": ["catalog", "sort_order", "value"],
            },
        ),
        migrations.AddConstraint(
            model_name="auditoriacatalogitem",
            constraint=models.UniqueConstraint(
                fields=("catalog", "value"),
                name="uniq_auditoria_catalog_value",
            ),
        ),
        migrations.RunPython(seed_catalog_items, migrations.RunPython.noop),
    ]
