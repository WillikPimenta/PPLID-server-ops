from django.db import migrations


PERMISSION = "operacao.suporte_operacional.notices.manage"
ROLES = (
    "op_gerencia",
    "op_lider",
    "qual_capacitacao",
    "qual_gerencia",
)


def add_permission(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLES):
        permissions = set(row.permissions or [])
        if PERMISSION in permissions:
            continue
        permissions.add(PERMISSION)
        row.permissions = sorted(permissions)
        row.save(update_fields=["permissions", "updated_at"])


def remove_permission(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLES):
        permissions = set(row.permissions or [])
        if PERMISSION not in permissions:
            continue
        permissions.remove(PERMISSION)
        row.permissions = sorted(permissions)
        row.save(update_fields=["permissions", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0017_contestacao_interna_permissions"),
    ]

    operations = [
        migrations.RunPython(add_permission, remove_permission),
    ]
