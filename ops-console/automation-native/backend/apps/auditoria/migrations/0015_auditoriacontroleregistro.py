from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0014_protocolo_consideracoes_finais"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditoriaControleRegistro",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "tipo",
                    models.CharField(
                        choices=[
                            ("remocao_base_negativa", "Remoção - Base negativa"),
                            ("remocao_base_positiva", "Remoção - Base positiva"),
                            ("solicitacoes_idas_bio", "Solicitações IDAS e BIO"),
                        ],
                        db_index=True,
                        max_length=64,
                    ),
                ),
                ("dados", models.JSONField(blank=True, default=dict)),
                ("situacao", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auditoria_controles_criados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "auditoria_controle_registro",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
