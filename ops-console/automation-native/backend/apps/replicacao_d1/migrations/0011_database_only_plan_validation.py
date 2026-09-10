import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def mark_existing_runs_as_reviewed(apps, schema_editor):
    Run = apps.get_model("replicacao_d1", "ReplicacaoD1Run")
    Run.objects.exclude(status_canonical="planned").update(validation_status="approved")


def reverse_existing_review_status(apps, schema_editor):
    Run = apps.get_model("replicacao_d1", "ReplicacaoD1Run")
    Run.objects.update(validation_status="pending")


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0010_ingestion_attempt_identity"),
        ("replicacao_d1", "0010_run_workflow_operational_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReplicacaoD1SchedulerState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(default="replicacao_d1", max_length=64, unique=True)),
                ("state", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "replicacao_d1_scheduler_state"},
        ),
        migrations.AddField(
            model_name="replicacaod1protocolo",
            name="matricula_tipo",
            field=models.CharField(
                choices=[("manual", "Manual"), ("automatico", "Automático"), ("desconhecido", "Desconhecido")],
                db_index=True,
                default="desconhecido",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1protocolo",
            name="selection_reason",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="plan_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="plan_revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="plan_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="plan_warnings",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="review_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replicacao_d1_planos_revisados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="validation_status",
            field=models.CharField(
                choices=[
                    ("pending", "Aguardando validação"),
                    ("approved", "Aprovado"),
                    ("rejected", "Rejeitado"),
                    ("superseded", "Substituído"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="excluidos_historico",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="protocolos_automaticos",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="protocolos_manuais",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="redistribuidos",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="warnings",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="ReplicacaoD1FonteLote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_date", models.DateField(db_index=True)),
                ("status", models.CharField(choices=[("loading", "Carregando"), ("ready", "Pronto"), ("failed", "Falhou")], db_index=True, default="loading", max_length=16)),
                ("schema_version", models.CharField(default="1", max_length=16)),
                ("content_hash", models.CharField(db_index=True, max_length=64)),
                ("rows_read", models.PositiveIntegerField(default=0)),
                ("rows_valid", models.PositiveIntegerField(default=0)),
                ("rows_duplicate", models.PositiveIntegerField(default=0)),
                ("rows_rejected", models.PositiveIntegerField(default=0)),
                ("error_summary", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("ingestion", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="replicacao_d1_fonte_lotes", to="common.botdataingestion")),
            ],
            options={"db_table": "replicacao_d1_fonte_lote", "ordering": ["-report_date", "-created_at"]},
        ),
        migrations.AddField(
            model_name="replicacaod1run",
            name="source_batch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="runs", to="replicacao_d1.replicacaod1fontelote"),
        ),
        migrations.CreateModel(
            name="ReplicacaoD1FonteRegistro",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_row_number", models.PositiveIntegerField()),
                ("protocolo", models.CharField(db_index=True, max_length=64)),
                ("protocolo_normalizado", models.CharField(db_index=True, max_length=64)),
                ("workflow", models.CharField(db_index=True, max_length=255)),
                ("data_analise", models.DateTimeField()),
                ("hora", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("matricula_tipo", models.CharField(choices=[("manual", "Manual"), ("automatico", "Automático"), ("desconhecido", "Desconhecido")], db_index=True, default="desconhecido", max_length=16)),
                ("matricula", models.CharField(blank=True, default="", max_length=64)),
                ("extra", models.JSONField(blank=True, default=dict)),
                ("lote", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="registros", to="replicacao_d1.replicacaod1fontelote")),
            ],
            options={"db_table": "replicacao_d1_fonte_registro", "ordering": ["lote_id", "source_row_number"]},
        ),
        migrations.AddField(
            model_name="replicacaod1protocolo",
            name="source_record",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="protocolos_planejados", to="replicacao_d1.replicacaod1fonteregistro"),
        ),
        migrations.CreateModel(
            name="ReplicacaoD1PlanReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("approve", "Aprovar"), ("reject", "Rejeitar")], db_index=True, max_length=16)),
                ("reason", models.CharField(blank=True, default="", max_length=500)),
                ("plan_hash", models.CharField(max_length=64)),
                ("plan_revision", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reviews", to="replicacao_d1.replicacaod1run")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="replicacao_d1_plan_reviews", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "replicacao_d1_plan_review", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ReplicacaoD1ExecutionEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phase", models.CharField(db_index=True, max_length=32)),
                ("status", models.CharField(db_index=True, max_length=32)),
                ("message", models.CharField(blank=True, default="", max_length=500)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="replicacao_d1.replicacaod1run")),
                ("workflow", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="events", to="replicacao_d1.replicacaod1workflowdia")),
            ],
            options={"db_table": "replicacao_d1_execution_event", "ordering": ["created_at", "id"], "indexes": [models.Index(fields=["run", "phase", "status"], name="rep_d1_event_run_phase_st")]},
        ),
        migrations.AddIndex(model_name="replicacaod1fontelote", index=models.Index(fields=["report_date", "status"], name="rep_d1_fonte_data_st")),
        migrations.AddConstraint(model_name="replicacaod1fontelote", constraint=models.UniqueConstraint(fields=("report_date", "content_hash"), name="replicacao_d1_fonte_data_hash_uniq")),
        migrations.AddIndex(model_name="replicacaod1fonteregistro", index=models.Index(fields=["lote", "workflow"], name="rep_d1_fonte_lote_wf")),
        migrations.AddIndex(model_name="replicacaod1fonteregistro", index=models.Index(fields=["lote", "protocolo_normalizado"], name="rep_d1_fonte_lote_prot")),
        migrations.AddConstraint(model_name="replicacaod1fonteregistro", constraint=models.UniqueConstraint(fields=("lote", "source_row_number"), name="rep_d1_fonte_lote_linha_uniq")),
        migrations.RunPython(mark_existing_runs_as_reviewed, reverse_existing_review_status),
    ]
