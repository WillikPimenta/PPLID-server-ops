# -*- coding: utf-8 -*-
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0014_chamado_tratado_por"),
    ]

    operations = [
        migrations.CreateModel(
            name="SuporteClaroEmail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("endereco", models.CharField(max_length=254)),
                ("comentario", models.TextField(blank=True, default="")),
                ("ordem", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="emails",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
            ],
            options={
                "db_table": "suporte_claro_email",
                "ordering": ["ordem", "id"],
            },
        ),
    ]
