from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0010_operational_occurrence"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationaloccurrence",
            name="cancelled",
            field=models.BooleanField(default=False),
        ),
    ]
