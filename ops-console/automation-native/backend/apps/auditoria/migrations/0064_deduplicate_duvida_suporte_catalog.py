from django.db import migrations


CATALOG = "duvida_suporte_operacional"
QUESTIONS = [
    "DOCUMENTO ADULTERADO",
    "DOCUMENTO AUSENTE",
    "DOCUMENTO DE CELEBRIDADE/POLÍTICO",
    "DOCUMENTO FOTO DA FOTO",
    "DOCUMENTO FOTOCOPIADO",
    "DOCUMENTO ILEGÍVEL",
    "DOCUMENTO INCOMPLETO",
    "DOCUMENTO POSSUI RASURAS",
    "DOCUMENTO DETERIORADO",
    "FACE ENCONTRADA NA BASE",
    "FACES INCOMPATÍVEIS",
    "FORMATAÇÃO/FONTE E DESALINHAMENTO",
    "FOTO DO CLIENTE DIVERGENTE",
    "FOTO DO CLIENTE IGUAL",
    "FOTO DO CLIENTE NÃO PASSÍVEL",
    "FOTO DO DOCUMENTO ILEGÍVEL",
    "NÚMERO DO CPF AUSENTE",
    "NÚMERO DO CPF DIVERGENTE",
    "SELFIE APRESENTA PARTES ÍNTIMAS",
    "SELFIE COM MÁSCARA DE DISFARCE",
    "SELFIE COM MÁSCARA DE PROTEÇÃO",
    "SELFIE FOTO DA FOTO",
    "SELFIE ILEGÍVEL",
    "SELFIE INVÁLIDA",
    "SELFIE MANIPULADA",
    "SOBREPOSIÇÃO NA FOTO",
]


def deduplicate_defaults(apps, schema_editor):
    CatalogItem = apps.get_model("auditoria", "AuditoriaCatalogItem")
    rows = list(CatalogItem.objects.filter(catalog=CATALOG).order_by("id"))
    defaults = {value.casefold(): (index, value) for index, value in enumerate(QUESTIONS)}
    grouped = {}
    for row in rows:
        key = row.value.casefold()
        if key in defaults:
            grouped.setdefault(key, []).append(row)

    for key, duplicates in grouped.items():
        index, canonical = defaults[key]
        keeper = next((row for row in duplicates if row.value == canonical), duplicates[0])
        CatalogItem.objects.filter(pk__in=[row.pk for row in duplicates if row.pk != keeper.pk]).delete()
        keeper.value = canonical
        keeper.label = canonical
        keeper.sort_order = index
        keeper.active = True
        keeper.save(update_fields=["value", "label", "sort_order", "active"])


class Migration(migrations.Migration):
    dependencies = [("auditoria", "0063_contestacaooperacional_cenario_original_and_more")]
    operations = [migrations.RunPython(deduplicate_defaults, migrations.RunPython.noop)]
