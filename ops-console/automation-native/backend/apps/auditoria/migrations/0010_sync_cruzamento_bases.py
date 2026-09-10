from django.db import migrations

from apps.auditoria.constants import CRUZAMENTO_BASES
from apps.auditoria.models import AuditoriaCatalogItem
from apps.auditoria.services.text_format import format_auditoria_label


def sync_cruzamento_bases(apps, schema_editor):
    AuditoriaCatalogItemModel = apps.get_model("auditoria", "AuditoriaCatalogItem")
    catalog = AuditoriaCatalogItem.CATALOG_CRUZAMENTO_BASES
    AuditoriaCatalogItemModel.objects.filter(catalog=catalog).delete()
    items = [
        AuditoriaCatalogItemModel(
            catalog=catalog,
            value=format_auditoria_label(value),
            label=format_auditoria_label(value),
            sort_order=index,
            active=True,
        )
        for index, value in enumerate(CRUZAMENTO_BASES)
    ]
    AuditoriaCatalogItemModel.objects.bulk_create(items)


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0009_auditoriaatividadeprotocoloetapa"),
    ]

    operations = [
        migrations.RunPython(sync_cruzamento_bases, migrations.RunPython.noop),
    ]
