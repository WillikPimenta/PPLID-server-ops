# Generated manually for consolidating production data into produtividade_record.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0021_scheduletoday_observation"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ProductionRecord",
        ),
    ]
