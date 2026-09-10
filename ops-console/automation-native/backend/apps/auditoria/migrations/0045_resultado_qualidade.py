from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0044_qualidade_analise_origem"),
    ]

    # Schema/backfill de resultado_qualidade e remoção de analise_status ficam em
    # 0046_resultado_qualidade (ramo paralelo). Esta migration permanece só para o merge.
    operations = []
