# Performance: leitura do indicador de erros por auditor sem consultar tabelas operacionais.
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("qualidade_operacional", "0024_falha_case_key"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="qualidadeauditado",
            index=models.Index(
                fields=["data", "matricula_auditor"],
                name="qo_aud_ret_dt_aud_idx",
                condition=Q(status="retirada"),
            ),
        ),
    ]
