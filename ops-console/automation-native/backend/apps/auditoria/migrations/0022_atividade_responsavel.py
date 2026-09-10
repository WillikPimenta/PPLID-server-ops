# Generated manually for responsavel on AuditoriaAtividade

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auditoria", "0021_alter_auditoriamotivofalha_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividade",
            name="responsavel",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auditoria_atividades_responsavel",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
