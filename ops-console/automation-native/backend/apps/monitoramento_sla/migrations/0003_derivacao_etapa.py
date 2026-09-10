# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dimensoes_processos", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("monitoramento_sla", "0002_incremental_sync"),
    ]

    operations = [
        migrations.CreateModel(
            name="DerivacaoEtapaImportRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("running", "Em execução"), ("ok", "OK"), ("error", "Erro")],
                        default="running",
                        max_length=16,
                    ),
                ),
                ("files_processed", models.PositiveIntegerField(default=0)),
                ("rows_inserted", models.PositiveIntegerField(default=0)),
                ("rows_skipped_total", models.PositiveIntegerField(default=0)),
                ("rows_rejected", models.PositiveIntegerField(default=0)),
                ("message", models.TextField(blank=True, default="")),
                ("metrics", models.JSONField(blank=True, default=dict)),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="derivacao_etapa_import_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "derivacao_etapa_import_run",
                "ordering": ["-started_at"],
            },
        ),
        migrations.CreateModel(
            name="DerivacaoEtapaDiaria",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("data", models.DateField(db_index=True)),
                ("registros", models.PositiveIntegerField(default=0)),
                ("percentual", models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ("cliente_nome_origem", models.CharField(blank=True, default="", max_length=512)),
                ("workflow_nome_origem", models.CharField(blank=True, default="", max_length=512)),
                ("etapa_nome_origem", models.CharField(blank=True, default="", max_length=512)),
                ("source_file", models.CharField(blank=True, default="", max_length=255)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cliente",
                    models.ForeignKey(
                        db_column="id_cliente",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="derivacao_etapa_diaria",
                        to="dimensoes_processos.dimcliente",
                    ),
                ),
                (
                    "etapa",
                    models.ForeignKey(
                        db_column="id_etapa",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="derivacao_etapa_diaria",
                        to="dimensoes_processos.dimetapa",
                    ),
                ),
                (
                    "import_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="linhas",
                        to="monitoramento_sla.derivacaoetapaimportrun",
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        db_column="id_workflow",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="derivacao_etapa_diaria",
                        to="dimensoes_processos.dimworkflow",
                    ),
                ),
            ],
            options={
                "db_table": "derivacao_etapa_diaria",
                "ordering": ["-data", "cliente_id", "workflow_id", "etapa_id"],
            },
        ),
        migrations.AddIndex(
            model_name="derivacaoetapadiaria",
            index=models.Index(fields=["data", "cliente", "workflow"], name="deriv_etapa_d_cw_idx"),
        ),
        migrations.AddConstraint(
            model_name="derivacaoetapadiaria",
            constraint=models.UniqueConstraint(
                fields=("data", "cliente", "workflow", "etapa"),
                name="uniq_derivacao_etapa_dia_cwe",
            ),
        ),
    ]
