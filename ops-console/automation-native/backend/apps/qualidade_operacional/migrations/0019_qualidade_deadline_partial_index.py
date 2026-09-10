# Performance: índice parcial para auditorias >= 120 dias; não altera linhas nem regras.
from datetime import timedelta

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("qualidade_operacional", "0018_merge_explicit_dates_dim_alias"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="qualidadeauditado",
            index=models.Index(
                fields=["protocolo", "id"],
                include=["data", "source_file", "tipo_analise"],
                name="qo_aud_deadline_protocol_cov",
                condition=Q(
                    data__isnull=False,
                    data_analise__isnull=False,
                    data__gte=F("data_analise") + timedelta(days=120),
                ),
            ),
        ),
    ]
