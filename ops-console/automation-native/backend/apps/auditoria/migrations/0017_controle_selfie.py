from django.db import migrations, models
import apps.auditoria.models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0016_catalog_tipo_acao_motivo_base_negativa"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriacontroleregistro",
            name="selfie",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.auditoria.models.controle_selfie_upload_to,
            ),
        ),
    ]
