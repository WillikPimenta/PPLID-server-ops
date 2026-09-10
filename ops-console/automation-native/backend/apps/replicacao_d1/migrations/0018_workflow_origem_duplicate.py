# Generated manually — destinos duplicados por fila/regra

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0017_bio_redoc_filas"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1workflow",
            name="workflow_origem",
            field=models.ForeignKey(
                blank=True,
                help_text="Workflow base quando este cadastro é uma cópia para outra fila/destino BRFlow.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="destinos_replicacao",
                to="replicacao_d1.replicacaod1workflow",
            ),
        ),
    ]
