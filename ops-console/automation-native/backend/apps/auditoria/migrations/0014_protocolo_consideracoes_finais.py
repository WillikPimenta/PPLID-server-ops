from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0013_etapa_tempo_analise"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="consideracoes_finais",
            field=models.TextField(blank=True, default=""),
        ),
    ]
