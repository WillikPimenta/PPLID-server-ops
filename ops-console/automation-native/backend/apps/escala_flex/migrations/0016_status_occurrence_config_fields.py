import unicodedata

from django.db import migrations, models


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().strip().split())


def _names_match_occurrence_status(occurrence_name: str, status_name: str) -> bool:
    occ = _normalize_name(occurrence_name)
    status = _normalize_name(status_name)
    if not occ or not status:
        return False
    if occ == status:
        return True
    if occ in status or status in occ:
        return True
    keyword_pairs = (
        (("portais", "acesso"), ("portais", "elevate")),
        (("indisponivel", "sistema"), ("problemas sistemicos",)),
        (("suporte tecnico",), ("problemas sistemicos",)),
    )
    for occ_keys, status_keys in keyword_pairs:
        if any(key in occ for key in occ_keys) and any(key in status for key in status_keys):
            return True
    return False


def seed_occurrence_status_links(apps, schema_editor):
    OccurrenceType = apps.get_model("escala_flex", "OccurrenceType")
    StatusType = apps.get_model("escala_flex", "StatusType")
    statuses = list(StatusType.objects.all())
    for occurrence in OccurrenceType.objects.all():
        matches = [
            status
            for status in statuses
            if _names_match_occurrence_status(occurrence.name, status.name)
        ]
        if matches:
            occurrence.status_types.set(matches)


class Migration(migrations.Migration):
    dependencies = [
        ("escala_flex", "0015_status_remove_icon_occurrence_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="statustype",
            name="observation",
            field=models.TextField(blank=True, verbose_name="observação"),
        ),
        migrations.AddField(
            model_name="statustype",
            name="deducts_logged_time",
            field=models.BooleanField(default=False, verbose_name="abate tempo logado"),
        ),
        migrations.AddField(
            model_name="statustype",
            name="default_time_seconds",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="tempo padrão (segundos)",
            ),
        ),
        migrations.AddField(
            model_name="statustype",
            name="deducts_production",
            field=models.BooleanField(default=False, verbose_name="abate produção"),
        ),
        migrations.AddField(
            model_name="occurrencetype",
            name="observation",
            field=models.TextField(blank=True, verbose_name="observação"),
        ),
        migrations.AddField(
            model_name="occurrencetype",
            name="status_types",
            field=models.ManyToManyField(
                blank=True,
                related_name="occurrence_types",
                to="escala_flex.statustype",
                verbose_name="status vinculados",
            ),
        ),
        migrations.RunPython(seed_occurrence_status_links, migrations.RunPython.noop),
    ]
