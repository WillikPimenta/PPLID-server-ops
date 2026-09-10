# Generated manually
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("suporte_claro", "0002_registro_origem"),
    ]

    operations = [
        migrations.CreateModel(
            name="SuporteClaroHistorico",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "action",
                    models.CharField(
                        choices=[("status", "Status"), ("edit", "Edicao")],
                        max_length=16,
                    ),
                ),
                ("field_name", models.CharField(blank=True, default="", max_length=64)),
                ("old_value", models.TextField(blank=True, default="")),
                ("new_value", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="historico",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suporte_claro_historico",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "suporte_claro_historico",
                "ordering": ["-created_at"],
            },
        ),
    ]
