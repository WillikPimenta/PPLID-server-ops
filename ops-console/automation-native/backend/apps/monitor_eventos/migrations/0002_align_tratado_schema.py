from django.db import migrations, models
import datetime


def truncate_monitor_records(apps, schema_editor):
    apps.get_model("monitor_eventos", "MonitorEventoRecord").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("monitor_eventos", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(truncate_monitor_records, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="monitoreventorecord",
            name="monitor_eve_team_8e4a00_idx",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="agent",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="agent_name",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="id_sessao",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="journey_shift",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="leader_name",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="location",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="objeto",
        ),
        migrations.RemoveField(
            model_name="monitoreventorecord",
            name="team",
        ),
        migrations.AddField(
            model_name="monitoreventorecord",
            name="data",
            field=models.DateField(db_index=True, default=datetime.date(2026, 1, 1)),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="monitoreventorecord",
            name="hora",
            field=models.PositiveSmallIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="monitoreventorecord",
            index=models.Index(fields=["data", "hora"], name="monitor_eve_data_hora_idx"),
        ),
    ]
