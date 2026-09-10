import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0006_remove_statusevent_legacy_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="currentactivity",
            name="hierarchical_level",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="current_activity_records",
                to="escala_flex.hierarchicallevel",
            ),
        ),
        migrations.AddField(
            model_name="scheduletoday",
            name="hierarchical_level",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schedule_today_entries",
                to="escala_flex.hierarchicallevel",
            ),
        ),
    ]
