from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dimensoes_processos", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="dimetapa",
            name="manual",
            field=models.BooleanField(default=True),
        ),
    ]
