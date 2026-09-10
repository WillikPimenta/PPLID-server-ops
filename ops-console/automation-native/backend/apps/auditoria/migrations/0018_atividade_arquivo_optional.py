from django.db import migrations, models
import apps.auditoria.models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0017_controle_selfie"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditoriaatividade",
            name="arquivo_original",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=apps.auditoria.models.atividade_arquivo_upload_to,
            ),
        ),
        migrations.AlterField(
            model_name="auditoriaatividade",
            name="nome_arquivo_original",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
