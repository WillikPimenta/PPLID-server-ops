# Generated manually for tratado_por on chamados externos.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0013_categoria_incidente_vinculos"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclarochamadoexterno",
            name="tratado_por",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
