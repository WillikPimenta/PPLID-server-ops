from django.db import migrations


PERMISSION_MAP = {
    "qual.auditoria.view": "qual.auditoria_compliance.view",
    "qual.auditoria.create": "qual.auditoria_compliance.create",
    "qual.auditoria.assign": "qual.auditoria_compliance.assign",
    "qual.auditoria.change": "qual.auditoria_compliance.change",
}
COMPLIANCE_ROLE = "qual_auditoria_compliance"


def copy_permissions(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.all():
        permissions = list(row.permissions or [])
        expected = {
            new_permission
            for old_permission, new_permission in PERMISSION_MAP.items()
            if old_permission in permissions
        }
        if row.role == COMPLIANCE_ROLE:
            expected.update(PERMISSION_MAP.values())
        missing = sorted(expected.difference(permissions))
        if missing:
            row.permissions = [*permissions, *missing]
            row.save(update_fields=["permissions"])


def remove_permissions(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    split_permissions = set(PERMISSION_MAP.values())
    for row in PortalRoleDefinition.objects.all():
        permissions = [
            permission
            for permission in (row.permissions or [])
            if permission not in split_permissions
        ]
        row.permissions = permissions
        row.save(update_fields=["permissions"])


class Migration(migrations.Migration):

    dependencies = [("access", "0015_portalroutepolicy_is_active")]

    operations = [migrations.RunPython(copy_permissions, remove_permissions)]
