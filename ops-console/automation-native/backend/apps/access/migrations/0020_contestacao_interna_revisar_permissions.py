from django.db import migrations


ROLE_PERMISSIONS = {
    "qual_contestacao_fraud": {
        "qual.contestacao_interna.fraud.revisar",
    },
    "qual_contestacao_compliance": {
        "qual.contestacao_interna.compliance.revisar",
    },
    "qual_gerencia": {
        "qual.contestacao_interna.fraud.revisar",
        "qual.contestacao_interna.compliance.revisar",
    },
}


def add_contestacao_revisar_permissions(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLE_PERMISSIONS):
        permissions = list(row.permissions or [])
        missing = sorted(ROLE_PERMISSIONS[row.role].difference(permissions))
        if missing:
            row.permissions = [*permissions, *missing]
            row.save(update_fields=["permissions"])


def remove_contestacao_revisar_permissions(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [("access", "0019_revisao_falhas_permissions")]

    operations = [
        migrations.RunPython(
            add_contestacao_revisar_permissions,
            remove_contestacao_revisar_permissions,
        )
    ]
