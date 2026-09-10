# Generated manually for auditoria catalog update

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditoriafalhacadastro",
            name="tipo_falha",
            field=models.CharField(max_length=32),
        ),
    ]
