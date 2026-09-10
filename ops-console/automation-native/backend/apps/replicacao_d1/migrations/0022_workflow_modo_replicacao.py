from django.db import migrations, models


def _ensure_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            ALTER TABLE replicacao_d1_workflow
            ADD COLUMN IF NOT EXISTS usar_arquivo_csv boolean NOT NULL DEFAULT true
            """
        )
        cursor.execute(
            """
            ALTER TABLE replicacao_d1_workflow_dia
            ADD COLUMN IF NOT EXISTS modo_replicacao varchar(16) NOT NULL DEFAULT 'protocolos'
            """
        )


def backfill_modo_replicacao(apps, schema_editor):
    """Backfill determinístico e diretamente testável da regra por fila."""
    Workflow = apps.get_model("replicacao_d1", "ReplicacaoD1Workflow")
    WorkflowDia = apps.get_model("replicacao_d1", "ReplicacaoD1WorkflowDia")
    for row in Workflow.objects.all().iterator():
        row.usar_arquivo_csv = (row.fila or "").strip().casefold() not in {"bio", "redoc"}
        row.save(update_fields=["usar_arquivo_csv"])
    for row in WorkflowDia.objects.all().iterator():
        row.modo_replicacao = (
            "qtd" if (row.fila or "").strip().casefold() in {"bio", "redoc"} else "protocolos"
        )
        row.save(update_fields=["modo_replicacao"])


class Migration(migrations.Migration):
    dependencies = [
        ("replicacao_d1", "0021_dashboard_performance_indexes"),
    ]
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="replicacaod1workflow",
                    name="usar_arquivo_csv",
                    field=models.BooleanField(default=True),
                ),
                migrations.AddField(
                    model_name="replicacaod1workflowdia",
                    name="modo_replicacao",
                    field=models.CharField(
                        choices=[
                            ("protocolos", "Protocolos (arquivo CSV)"),
                            ("qtd", "Quantidade"),
                        ],
                        default="protocolos",
                        max_length=16,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(_ensure_columns, migrations.RunPython.noop),
                migrations.RunPython(backfill_modo_replicacao, migrations.RunPython.noop),
            ],
        ),
    ]
