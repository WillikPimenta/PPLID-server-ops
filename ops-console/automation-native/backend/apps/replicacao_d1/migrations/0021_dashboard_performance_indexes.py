from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("replicacao_d1", "0020_merge_20260818_1706"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="replicacaod1run",
            index=models.Index(fields=["data_execucao"], name="rep_d1_run_execucao"),
        ),
        AddIndexConcurrently(
            model_name="replicacaod1replicado",
            index=models.Index(
                fields=["report_date", "protocolo_origem_normalizado"],
                name="rep_d1_rep_data_norm",
            ),
        ),
        AddIndexConcurrently(
            model_name="replicacaod1reconciliacao",
            index=models.Index(
                fields=["regra_version", "status", "protocolo"],
                name="rep_d1_rec_rule_st_prot",
            ),
        ),
        AddIndexConcurrently(
            model_name="replicacaod1reconciliacao",
            index=models.Index(
                fields=["regra_version", "status", "replicado"],
                name="rep_d1_rec_rule_st_rep",
            ),
        ),
    ]
