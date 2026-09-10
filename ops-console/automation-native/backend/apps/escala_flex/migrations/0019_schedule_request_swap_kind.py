from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0018_operational_occurrence_extension"),
    ]

    operations = [
        migrations.AddField(
            model_name="schedulerequest",
            name="swap_kind",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
