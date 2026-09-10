from django.db import migrations, models


def link_occurrence_colors(apps, schema_editor):
    StatusType = apps.get_model("escala_flex", "StatusType")
    OccurrenceType = apps.get_model("escala_flex", "OccurrenceType")

    def normalize(value: str) -> str:
        return " ".join((value or "").lower().strip().split())

    def matches(occurrence_name: str, status_name: str) -> bool:
        occ = normalize(occurrence_name)
        status = normalize(status_name)
        if not occ or not status:
            return False
        if occ == status or occ in status or status in occ:
            return True
        if "portais" in occ and ("portais" in status or "elevate" in status):
            return True
        if "indispon" in occ and "problemas" in status:
            return True
        if "suporte" in occ and "problemas" in status:
            return True
        return False

    statuses = list(StatusType.objects.all())
    for occurrence in OccurrenceType.objects.all():
        for status in statuses:
            if matches(occurrence.name, status.name) and status.color:
                occurrence.color = status.color
                occurrence.save(update_fields=["color"])
                break


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0014_operational_occurrence_scheduled_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="occurrencetype",
            name="color",
            field=models.CharField(blank=True, max_length=32, verbose_name="cor hex"),
        ),
        migrations.RunPython(link_occurrence_colors, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="statustype",
            name="icon",
        ),
    ]
