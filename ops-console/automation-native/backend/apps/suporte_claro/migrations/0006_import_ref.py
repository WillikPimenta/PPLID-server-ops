# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("suporte_claro", "0005_chamado_externo_multiplo"),
    ]

    operations = [
        migrations.AlterField(
            model_name="suporteclarohistorico",
            name="action",
            field=models.CharField(
                choices=[
                    ("status", "Status"),
                    ("edit", "Edicao"),
                    ("import", "Importacao"),
                ],
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="SuporteClaroImportRef",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("linha_id", models.CharField(db_index=True, max_length=128, unique=True)),
                ("imported_at", models.DateTimeField(auto_now_add=True)),
                (
                    "imported_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suporte_claro_imports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="import_refs",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
            ],
            options={
                "db_table": "suporte_claro_import_ref",
                "ordering": ["-imported_at"],
            },
        ),
    ]
