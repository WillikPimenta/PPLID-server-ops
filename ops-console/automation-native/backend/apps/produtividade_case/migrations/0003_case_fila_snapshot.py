# Generated manually for Case Manager fila snapshot

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("produtividade_case", "0002_source_and_drop_case_facts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="produtividadecasesynclog",
            name="report_type",
            field=models.CharField(
                choices=[
                    ("consolidado", "Consolidado"),
                    ("prod_hora", "Produtividade por hora"),
                    ("tempo_logado", "Tempo logado (só Excel)"),
                    ("fila_aberta", "Fila em aberto"),
                ],
                db_index=True,
                default="consolidado",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="CaseFilaSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("captured_at", models.DateTimeField(db_index=True)),
                ("total_abertos", models.PositiveIntegerField(default=0)),
                ("success", models.BooleanField(db_index=True, default=True)),
                ("source_file", models.CharField(blank=True, default="", max_length=1024)),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "case_manager_fila_snapshot",
                "ordering": ["-captured_at"],
            },
        ),
        migrations.CreateModel(
            name="CaseFilaAgg",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "dimension",
                    models.CharField(
                        choices=[
                            ("status", "Status"),
                            ("idade_bucket", "Idade na fila"),
                            ("request_type", "Tipo de request"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("key", models.CharField(db_index=True, max_length=64)),
                ("count", models.PositiveIntegerField(default=0)),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aggs",
                        to="produtividade_case.casefilasnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "case_manager_fila_agg",
                "ordering": ["dimension", "-count", "key"],
            },
        ),
        migrations.AddIndex(
            model_name="casefilasnapshot",
            index=models.Index(fields=["success", "-captured_at"], name="case_manage_success_2f0b1c_idx"),
        ),
        migrations.AddIndex(
            model_name="casefilaagg",
            index=models.Index(fields=["snapshot", "dimension"], name="case_manage_snapsho_8a1d2e_idx"),
        ),
        migrations.AddConstraint(
            model_name="casefilaagg",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "dimension", "key"),
                name="uniq_case_fila_agg_snap_dim_key",
            ),
        ),
    ]
