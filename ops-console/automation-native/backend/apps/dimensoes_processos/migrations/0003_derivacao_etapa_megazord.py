# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def add_run_kind_column(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute("PRAGMA table_info(derivacao_etapa_import_run)")
            cols = [row[1] for row in cursor.fetchall()]
            if "run_kind" in cols:
                return
        else:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
                LIMIT 1
                """,
                ["derivacao_etapa_import_run", "run_kind"],
            )
            if cursor.fetchone():
                return
    schema_editor.execute(
        "ALTER TABLE derivacao_etapa_import_run "
        "ADD COLUMN run_kind varchar(16) NOT NULL DEFAULT 'import'"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("dimensoes_processos", "0002_dimetapa_manual"),
        ("monitoramento_sla", "0003_derivacao_etapa"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="DerivacaoEtapaImportRun",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        (
                            "run_kind",
                            models.CharField(
                                choices=[("scan", "Scan comparativo"), ("import", "Importação")],
                                default="import",
                                max_length=16,
                            ),
                        ),
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
                                to="dimensoes_processos.derivacaoetapaimportrun",
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
            ],
            database_operations=[
                migrations.RunPython(add_run_kind_column, migrations.RunPython.noop),
            ],
        ),
        migrations.CreateModel(
            name="DimNomeAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "dimensao",
                    models.CharField(
                        choices=[("cliente", "Cliente"), ("workflow", "Workflow"), ("etapa", "Etapa")],
                        max_length=16,
                    ),
                ),
                ("nome_origem", models.CharField(max_length=512)),
                ("nome_origem_key", models.CharField(max_length=512)),
                ("ativo", models.BooleanField(default=True)),
                ("notas", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "cliente",
                    models.ForeignKey(
                        blank=True,
                        db_column="id_cliente",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nome_aliases",
                        to="dimensoes_processos.dimcliente",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dim_nome_aliases_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "etapa",
                    models.ForeignKey(
                        blank=True,
                        db_column="id_etapa",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nome_aliases",
                        to="dimensoes_processos.dimetapa",
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        blank=True,
                        db_column="id_workflow",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nome_aliases",
                        to="dimensoes_processos.dimworkflow",
                    ),
                ),
            ],
            options={
                "db_table": "dim_nome_alias",
                "ordering": ["dimensao", "nome_origem"],
            },
        ),
        migrations.AddConstraint(
            model_name="dimnomealias",
            constraint=models.UniqueConstraint(
                fields=("dimensao", "nome_origem_key"),
                name="uniq_dim_nome_alias_dim_key",
            ),
        ),
        migrations.CreateModel(
            name="DerivacaoEtapaComparativo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "dimensao",
                    models.CharField(
                        choices=[("cliente", "Cliente"), ("workflow", "Workflow"), ("etapa", "Etapa")],
                        max_length=16,
                    ),
                ),
                ("nome_origem", models.CharField(max_length=512)),
                ("nome_origem_key", models.CharField(max_length=512)),
                ("linhas_csv", models.PositiveIntegerField(default=0)),
                ("registros_total", models.PositiveIntegerField(default=0)),
                ("id_resolvido", models.PositiveIntegerField(blank=True, null=True)),
                ("nome_megazord", models.CharField(blank=True, default="", max_length=512)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ok", "OK"),
                            ("unmatched", "Sem match"),
                            ("ambiguous", "Ambíguo"),
                            ("alias", "Alias"),
                        ],
                        default="unmatched",
                        max_length=16,
                    ),
                ),
                ("match_strategy", models.CharField(blank=True, default="", max_length=32)),
                ("sugestoes", models.JSONField(blank=True, default=list)),
                (
                    "scan_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comparativo_linhas",
                        to="dimensoes_processos.derivacaoetapaimportrun",
                    ),
                ),
            ],
            options={
                "db_table": "derivacao_etapa_comparativo",
                "ordering": ["dimensao", "-linhas_csv", "nome_origem"],
            },
        ),
        migrations.AddIndex(
            model_name="derivacaoetapacomparativo",
            index=models.Index(fields=["scan_run", "dimensao", "status"], name="deriv_comp_run_dim_st_idx"),
        ),
        migrations.AddConstraint(
            model_name="derivacaoetapacomparativo",
            constraint=models.UniqueConstraint(
                fields=("scan_run", "dimensao", "nome_origem_key"),
                name="uniq_deriv_comp_run_dim_key",
            ),
        ),
    ]
