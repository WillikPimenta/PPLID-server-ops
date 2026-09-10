from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0012_protocolo_status_em_andamento"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividadeprotocoloetapa",
            name="tempo_analise",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
