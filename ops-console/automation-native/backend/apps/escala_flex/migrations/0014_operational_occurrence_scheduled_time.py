from django.db import migrations, models
from django.utils import timezone


def backfill_scheduled_time(apps, schema_editor):
    OperationalOccurrence = apps.get_model("escala_flex", "OperationalOccurrence")
    for occurrence in OperationalOccurrence.objects.exclude(created_at__isnull=True):
        local_created = timezone.localtime(occurrence.created_at)
        occurrence.scheduled_time = local_created.time().replace(second=0, microsecond=0)
        occurrence.save(update_fields=["scheduled_time"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "escala_flex",
            "0013_rename_ef_operatio_date_0a8f0d_idx_ef_operatio_date_c0e58f_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="operationaloccurrence",
            name="scheduled_time",
            field=models.TimeField(
                blank=True,
                help_text="Horário em que o agente será retirado da operação.",
                null=True,
                verbose_name="horário planejado",
            ),
        ),
        migrations.RunPython(backfill_scheduled_time, migrations.RunPython.noop),
    ]
