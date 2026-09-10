from django.db import migrations, models


def seed_absence_types(apps, schema_editor):
    AbsenceType = apps.get_model("escala_flex", "AbsenceType")
    defaults = [
        (1, "FOLGA", "Folga", "#0EA5E9"),
        (2, "FERIAS", "Férias", "#8B5CF6"),
        (3, "BH", "Banco de horas", "#F59E0B"),
        (4, "AFASTADO", "Afastado", "#6B7280"),
    ]
    for pk, code, name, color in defaults:
        AbsenceType.objects.update_or_create(
            pk=pk,
            defaults={"code": code, "name": name, "color": color, "active": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("escala_flex", "0016_status_occurrence_config_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="AbsenceType",
            fields=[
                ("id", models.PositiveSmallIntegerField(primary_key=True, serialize=False)),
                ("code", models.CharField(help_text="Valor gravado em dia_escala (ex.: FOLGA).", max_length=32, unique=True, verbose_name="código na escala")),
                ("name", models.CharField(max_length=128, verbose_name="nome")),
                ("color", models.CharField(blank=True, max_length=32, verbose_name="cor hex")),
                ("active", models.BooleanField(default=True)),
                ("observation", models.TextField(blank=True, verbose_name="observação")),
            ],
            options={
                "verbose_name": "tipo de ausência",
                "verbose_name_plural": "tipos de ausência",
                "db_table": "ef_absence_type",
                "ordering": ["name"],
            },
        ),
        migrations.RunPython(seed_absence_types, migrations.RunPython.noop),
    ]
