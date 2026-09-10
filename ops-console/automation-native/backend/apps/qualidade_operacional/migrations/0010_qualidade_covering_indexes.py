# Performance: índices de cobertura; não altera linhas nem regras de negócio.
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("qualidade_operacional", "0009_import_telemetry"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="qualidadeauditado",
            index=models.Index(
                fields=["data", "id_cliente"],
                include=["id"],
                name="qo_aud_dt_cli_cov",
            ),
        ),
        AddIndexConcurrently(
            model_name="qualidadeauditado",
            index=models.Index(
                fields=["data_analise", "id_cliente"],
                include=["id"],
                name="qo_aud_an_cli_cov",
            ),
        ),
        AddIndexConcurrently(
            model_name="qualidadeauditado",
            index=models.Index(
                fields=["data", "matricula"],
                include=["id", "tipo_conclusao", "protocolo"],
                name="qo_aud_dt_mat_cov",
            ),
        ),
        AddIndexConcurrently(
            model_name="qualidadeauditado",
            index=models.Index(
                fields=["data_analise", "matricula"],
                include=["id", "tipo_conclusao", "protocolo"],
                name="qo_aud_an_mat_cov",
            ),
        ),
    ]
