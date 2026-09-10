# -*- coding: utf-8 -*-
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dimensoes_processos", "0003_derivacao_etapa_megazord"),
        ("monitoramento_sla", "0003_derivacao_etapa"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="DerivacaoEtapaDiaria"),
                migrations.DeleteModel(name="DerivacaoEtapaImportRun"),
            ],
            database_operations=[],
        ),
    ]
