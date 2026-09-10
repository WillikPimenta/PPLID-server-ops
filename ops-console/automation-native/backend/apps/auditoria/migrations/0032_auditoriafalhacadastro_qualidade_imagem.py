from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0031_auditoriafalhacadastro_public_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="qualidade_imagem",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
