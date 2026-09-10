from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dimensoes_processos", "0004_derivacao_etapa_preflight"),
    ]

    operations = [
        migrations.AddField(
            model_name="dimnomealias",
            name="classificacao",
            field=models.CharField(
                choices=[
                    ("producao", "Produção"),
                    ("etapa_automatica", "Etapa automática"),
                    ("poc_teste", "POC / teste"),
                ],
                default="producao",
                max_length=24,
            ),
        ),
    ]
