from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0043_alter_auditoriacatalogitem_catalog"),
    ]

    # A tabela, o vínculo e o backfill pertencem ao ramo principal em
    # 0045_qualidade_analise_origem. Este nó paralelo apenas reconcilia o grafo.
    operations = []
