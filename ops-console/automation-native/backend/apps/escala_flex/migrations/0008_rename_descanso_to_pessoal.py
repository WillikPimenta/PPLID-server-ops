from django.db import migrations


def rename_descanso_to_pessoal(apps, schema_editor):
    StatusType = apps.get_model("escala_flex", "StatusType")
    StatusType.objects.filter(pk=10, name="Descanso").update(name="Pessoal")


def rename_pessoal_to_descanso(apps, schema_editor):
    StatusType = apps.get_model("escala_flex", "StatusType")
    StatusType.objects.filter(pk=10, name="Pessoal").update(name="Descanso")


class Migration(migrations.Migration):
    dependencies = [
        ("escala_flex", "0007_schedule_today_hierarchical_level"),
    ]

    operations = [
        migrations.RunPython(rename_descanso_to_pessoal, rename_pessoal_to_descanso),
    ]
