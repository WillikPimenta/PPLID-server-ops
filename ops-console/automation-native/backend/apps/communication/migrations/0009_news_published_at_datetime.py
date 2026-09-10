# Generated manually for published_at DateField -> DateTimeField

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("communication", "0008_newslike_reaction_heart"),
    ]

    operations = [
        migrations.AlterField(
            model_name="news",
            name="published_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                verbose_name="publicação",
            ),
        ),
    ]
