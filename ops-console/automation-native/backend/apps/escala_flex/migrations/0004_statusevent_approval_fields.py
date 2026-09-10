from django.db import migrations, models
import django.db.models.deletion


def backfill_total_duration(apps, schema_editor):
    StatusEvent = apps.get_model("escala_flex", "StatusEvent")
    for ev in StatusEvent.objects.filter(
        final_date__isnull=False, total_duration__isnull=True
    ).iterator():
        delta = ev.final_date - ev.start_date
        ev.total_duration = max(0, int(delta.total_seconds()))
        ev.save(update_fields=["total_duration"])


class Migration(migrations.Migration):
    dependencies = [
        ("escala_flex", "0003_escalaimportbatch_status"),
        ("workforce", "0007_rename_agent_active_idx_agent_active_c8098c_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="statusevent",
            name="total_duration",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Duração total em segundos (início → fim).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="statusevent",
            name="approved",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="statusevent",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_status_events",
                to="workforce.agent",
            ),
        ),
        migrations.AddField(
            model_name="statusevent",
            name="approved_duration",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Duração aprovada em segundos.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="statusevent",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_total_duration, migrations.RunPython.noop),
    ]
