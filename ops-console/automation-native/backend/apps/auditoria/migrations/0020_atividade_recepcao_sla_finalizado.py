from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0019_protocolo_reanalisado"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividade",
            name="data_recepcao",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriaatividade",
            name="encerrado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="finalizado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
