# Generated manually for natureza interno/externo on chamados.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0007_titulo_protocolos"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclarochamadoexterno",
            name="natureza",
            field=models.CharField(
                choices=[("interno", "Interno"), ("externo", "Externo")],
                db_index=True,
                default="externo",
                max_length=16,
            ),
        ),
    ]
