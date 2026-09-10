# Generated manually for Case Manager facts + fila sample

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("produtividade_case", "0004_case_consolidado_daily_agg"),
    ]

    operations = [
        migrations.CreateModel(
            name="CaseConsolidadoFact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("periodo_mes", models.CharField(db_index=True, max_length=16)),
                ("protocolo_destino", models.CharField(db_index=True, max_length=128)),
                ("protocolo_origem", models.CharField(blank=True, default="", max_length=128)),
                ("workflow_origem", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("nh_origem", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("cliente_origem", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("matricula_origem", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("matricula_destino", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("resultado_destino", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("status_destino", models.CharField(blank=True, default="", max_length=64)),
                ("tipo_conclusao_origem", models.CharField(blank=True, default="", max_length=32)),
                ("alertas_destino", models.CharField(blank=True, default="", max_length=500)),
                ("conclusao_destino_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("inspecao_at", models.DateTimeField(blank=True, null=True)),
                ("tempo_analise_segundos", models.PositiveIntegerField(blank=True, null=True)),
                ("source_file", models.CharField(blank=True, default="", max_length=1024)),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="facts",
                        to="produtividade_case.caseconsolidadosnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "case_manager_consolidado_fact",
                "ordering": ["-conclusao_destino_at", "protocolo_destino"],
            },
        ),
        migrations.CreateModel(
            name="CaseFilaSampleItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("protocolo_id", models.CharField(db_index=True, max_length=128)),
                ("transaction_status", models.CharField(blank=True, default="", max_length=64)),
                ("idade_bucket", models.CharField(blank=True, db_index=True, default="", max_length=16)),
                ("created_ts", models.DateTimeField(blank=True, null=True)),
                ("workflow_origem", models.CharField(blank=True, default="", max_length=255)),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sample_items",
                        to="produtividade_case.casefilasnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "case_manager_fila_sample_item",
                "ordering": ["created_ts", "protocolo_id"],
            },
        ),
        migrations.AddIndex(
            model_name="caseconsolidadofact",
            index=models.Index(fields=["periodo_mes", "workflow_origem"], name="case_manage_periodo_wf_idx"),
        ),
        migrations.AddIndex(
            model_name="caseconsolidadofact",
            index=models.Index(fields=["periodo_mes", "matricula_destino"], name="case_manage_periodo_md_idx"),
        ),
        migrations.AddIndex(
            model_name="caseconsolidadofact",
            index=models.Index(fields=["periodo_mes", "conclusao_destino_at"], name="case_manage_periodo_cd_idx"),
        ),
        migrations.AddIndex(
            model_name="casefilasampleitem",
            index=models.Index(fields=["snapshot", "idade_bucket"], name="case_manage_snap_idade_idx"),
        ),
    ]
