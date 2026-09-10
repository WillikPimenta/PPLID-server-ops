from django.db import migrations


PERMISSION = "qual.auditoria.change"
ROLES = ("qual_gerencia",)


def add_permission(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLES):
        permissions = list(row.permissions or [])
        if PERMISSION not in permissions:
            permissions.append(PERMISSION)
            row.permissions = permissions
            row.save(update_fields=["permissions"])


def remove_permission(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLES):
        permissions = [value for value in (row.permissions or []) if value != PERMISSION]
        row.permissions = permissions
        row.save(update_fields=["permissions"])


class Migration(migrations.Migration):

    dependencies = [("access", "0013_merge_20260814_1009")]

    operations = [migrations.RunPython(add_permission, remove_permission)]
