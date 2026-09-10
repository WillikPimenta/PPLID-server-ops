# Performance: recorte temporal de contestação sem indexar linhas sem recepção.
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("qualidade_operacional", "0025_auditor_withdrawn_failure_index"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # CREATE INDEX CONCURRENTLY não é transacional. Se um deploy for
            # interrompido depois de criar (ou deixar inválido) o índice e
            # antes de gravar django_migrations, uma repetição comum falha com
            # "relation already exists". Remover/recriar torna o retry seguro.
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        'DROP INDEX CONCURRENTLY IF EXISTS "qo_aud_cont_rec_idx";',
                        'CREATE INDEX CONCURRENTLY "qo_aud_cont_rec_idx" '
                        'ON "qualidade_auditado" ("data_recepcao_contestacao") '
                        'WHERE "data_recepcao_contestacao" IS NOT NULL;',
                    ],
                    reverse_sql=(
                        'DROP INDEX CONCURRENTLY IF EXISTS "qo_aud_cont_rec_idx";'
                    ),
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="qualidadeauditado",
                    index=models.Index(
                        fields=["data_recepcao_contestacao"],
                        name="qo_aud_cont_rec_idx",
                        condition=Q(data_recepcao_contestacao__isnull=False),
                    ),
                ),
            ],
        ),
    ]
