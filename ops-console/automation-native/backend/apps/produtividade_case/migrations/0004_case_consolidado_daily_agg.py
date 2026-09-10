# Generated manually for Case Manager consolidado daily aggs

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("produtividade_case", "0003_case_fila_snapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="CaseConsolidadoSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("periodo_mes", models.CharField(db_index=True, max_length=16)),
                ("captured_at", models.DateTimeField(db_index=True)),
                ("total_protocolos", models.PositiveIntegerField(default=0)),
                ("success", models.BooleanField(db_index=True, default=True)),
                ("source_file", models.CharField(blank=True, default="", max_length=1024)),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "case_manager_consolidado_snapshot",
                "ordering": ["-captured_at"],
            },
        ),
        migrations.CreateModel(
            name="CaseConsolidadoDailyAgg",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("periodo_mes", models.CharField(db_index=True, max_length=16)),
                ("day", models.DateField(db_index=True)),
                ("dimension", models.CharField(db_index=True, max_length=48)),
                ("key", models.CharField(db_index=True, max_length=255)),
                ("count", models.PositiveIntegerField(default=0)),
                ("analysis_seconds_sum", models.PositiveBigIntegerField(default=0)),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="daily_aggs",
                        to="produtividade_case.caseconsolidadosnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "case_manager_consolidado_daily_agg",
                "ordering": ["day", "dimension", "-count", "key"],
            },
        ),
        migrations.AddIndex(
            model_name="caseconsolidadosnapshot",
            index=models.Index(fields=["periodo_mes", "-captured_at"], name="case_manage_periodo_7a1b2c_idx"),
        ),
        migrations.AddIndex(
            model_name="caseconsolidadosnapshot",
            index=models.Index(fields=["success", "-captured_at"], name="case_manage_success_3d4e5f_idx"),
        ),
        migrations.AddIndex(
            model_name="caseconsolidadodailyagg",
            index=models.Index(fields=["periodo_mes", "dimension", "day"], name="case_manage_periodo_8g9h0i_idx"),
        ),
        migrations.AddIndex(
            model_name="caseconsolidadodailyagg",
            index=models.Index(fields=["dimension", "key"], name="case_manage_dimensi_1j2k3l_idx"),
        ),
        migrations.AddConstraint(
            model_name="caseconsolidadodailyagg",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "day", "dimension", "key"),
                name="uniq_case_cons_daily_snap_day_dim_key",
            ),
        ),
    ]
