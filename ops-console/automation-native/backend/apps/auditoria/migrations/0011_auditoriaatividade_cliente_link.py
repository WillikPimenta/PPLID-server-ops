from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0010_sync_cruzamento_bases"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividade",
            name="cliente",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriaatividade",
            name="link_demanda",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
    ]
