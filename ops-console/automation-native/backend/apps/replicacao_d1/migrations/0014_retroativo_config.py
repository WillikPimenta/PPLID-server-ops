# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0013_repair_database_only_false_failures"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReplicacaoD1RetroativoConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("retroativo_ativo", models.BooleanField(default=False)),
                ("retroativo_data_inicio", models.DateField(blank=True, null=True)),
                ("retroativo_data_fim", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="replicacao_d1_retroativo_alterado",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Configuração retroativa D-1",
                "verbose_name_plural": "Configuração retroativa D-1",
                "db_table": "replicacao_d1_retroativo_config",
            },
        ),
        migrations.CreateModel(
            name="ReplicacaoD1RetroativoCliente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "cliente",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="retroativo_vinculos",
                        to="replicacao_d1.replicacaod1cliente",
                    ),
                ),
                (
                    "config",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="clientes",
                        to="replicacao_d1.replicacaod1retroativoconfig",
                    ),
                ),
            ],
            options={
                "db_table": "replicacao_d1_retroativo_cliente",
            },
        ),
        migrations.AddConstraint(
            model_name="replicacaod1retroativocliente",
            constraint=models.UniqueConstraint(
                fields=("config", "cliente"),
                name="replicacao_d1_retroativo_cli_uniq",
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="protocolos_retroativos",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
