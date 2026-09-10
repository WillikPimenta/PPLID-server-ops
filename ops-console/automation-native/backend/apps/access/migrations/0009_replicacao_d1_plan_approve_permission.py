from django.db import migrations


PERMISSION = "planejamento.automacao.approve"
ROLES = ("plan_gerencia", "adm_portal")


def grant_plan_approval(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLES):
        permissions = list(row.permissions or [])
        if PERMISSION not in permissions:
            permissions.append(PERMISSION)
            row.permissions = sorted(set(permissions))
            row.save(update_fields=["permissions", "updated_at"])


def revoke_plan_approval(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLES):
        permissions = [code for code in (row.permissions or []) if code != PERMISSION]
        row.permissions = permissions
        row.save(update_fields=["permissions", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("access", "0008_seed_customized_quality_roles")]
    operations = [migrations.RunPython(grant_plan_approval, revoke_plan_approval)]
