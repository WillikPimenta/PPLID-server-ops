# Generated manually

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import apps.auditoria.models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auditoria", "0006_auditoriafalhacadastro_tipo_registro"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditoriaAtividade",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=255)),
                ("arquivo_original", models.FileField(upload_to=apps.auditoria.models.atividade_arquivo_upload_to)),
                ("nome_arquivo_original", models.CharField(max_length=255)),
                ("workflow", models.CharField(blank=True, default="", max_length=255)),
                ("nivel_hierarquico", models.CharField(blank=True, default="", max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pendente", "Pendente"),
                            ("em_andamento", "Em andamento"),
                            ("concluida", "Concluída"),
                        ],
                        db_index=True,
                        default="pendente",
                        max_length=20,
                    ),
                ),
                ("total_protocolos", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auditoria_atividades",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "auditoria_atividade",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="AuditoriaAtividadeImportStaging",
            fields=[
                (
                    "token",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("arquivo", models.FileField(upload_to="auditoria/import_staging/%Y/%m/")),
                ("nome_arquivo", models.CharField(max_length=255)),
                ("preview", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auditoria_import_stagings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "auditoria_atividade_import_staging",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="AuditoriaAtividadeProtocolo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("protocolo", models.CharField(db_index=True, max_length=100)),
                ("workflow", models.CharField(blank=True, default="", max_length=255)),
                ("nivel_hierarquico", models.CharField(blank=True, default="", max_length=255)),
                ("resultado_contestado", models.TextField(blank=True, default="")),
                ("numero_contrato", models.CharField(blank=True, default="", max_length=128)),
                ("resultado_pos_auditoria", models.TextField(blank=True, default="")),
                ("tipo_conclusao", models.CharField(blank=True, default="", max_length=255)),
                ("tipo_falha", models.CharField(blank=True, default="", max_length=255)),
                ("cenario", models.CharField(blank=True, default="", max_length=255)),
                ("detalhamento", models.TextField(blank=True, default="")),
                ("conclusao_contestacao", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pendente", "Pendente"),
                            ("em_analise", "Em análise"),
                            ("concluido", "Concluído"),
                        ],
                        db_index=True,
                        default="pendente",
                        max_length=20,
                    ),
                ),
                ("excel_row", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "atividade",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="protocolos",
                        to="auditoria.auditoriaatividade",
                    ),
                ),
            ],
            options={
                "db_table": "auditoria_atividade_protocolo",
                "ordering": ["excel_row", "protocolo"],
            },
        ),
        migrations.AddConstraint(
            model_name="auditoriaatividadeprotocolo",
            constraint=models.UniqueConstraint(
                fields=("atividade", "protocolo"),
                name="uniq_auditoria_atividade_protocolo",
            ),
        ),
    ]
