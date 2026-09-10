from django.db import migrations


def reformat_catalog_labels(apps, schema_editor):
    AuditoriaCatalogItem = apps.get_model("auditoria", "AuditoriaCatalogItem")
    from apps.auditoria.services.text_format import format_auditoria_label

    for item in AuditoriaCatalogItem.objects.all().order_by("catalog", "id"):
        formatted = format_auditoria_label(item.value)
        duplicate = (
            AuditoriaCatalogItem.objects.filter(catalog=item.catalog, value=formatted)
            .exclude(pk=item.pk)
            .exists()
        )
        if duplicate:
            item.delete()
            continue
        item.value = formatted
        item.label = formatted
        item.save(update_fields=["value", "label"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0003_auditoriacatalogitem"),
    ]

    operations = [
        migrations.RunPython(reformat_catalog_labels, migrations.RunPython.noop),
    ]
