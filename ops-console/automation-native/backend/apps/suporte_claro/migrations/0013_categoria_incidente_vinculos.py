# Generated manually: categoria/tipo_incidente + vínculos entre incidentes.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("suporte_claro", "0012_registro_retorno_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="categoria",
            field=models.CharField(
                choices=[("demanda", "Demanda"), ("incidente", "Incidente")],
                db_index=True,
                default="demanda",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="tipo_incidente",
            field=models.CharField(
                blank=True,
                choices=[
                    ("lentidao", "Lentidão"),
                    ("travamento", "Travamento"),
                    ("queda", "Queda"),
                    ("erro", "Erro"),
                    ("outro", "Outro"),
                ],
                db_index=True,
                default="",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="SuporteClaroVinculoIncidente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suporte_claro_vinculos",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "from_registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vinculos_from",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
                (
                    "to_registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vinculos_to",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
            ],
            options={
                "db_table": "suporte_claro_vinculo_incidente",
                "ordering": ["-created_at", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="suporteclarovinculoincidente",
            constraint=models.UniqueConstraint(
                fields=("from_registro", "to_registro"),
                name="suporte_claro_vinculo_unique_pair",
            ),
        ),
        migrations.AddConstraint(
            model_name="suporteclarovinculoincidente",
            constraint=models.CheckConstraint(
                check=~models.Q(from_registro=models.F("to_registro")),
                name="suporte_claro_vinculo_no_self",
            ),
        ),
    ]
