from django.db import migrations


ROLE_PERMISSIONS = {
    "qual_capacitacao": {
        "qual.capacitacao.revisao_falhas.view",
        "qual.capacitacao.revisao_falhas.decide",
        "qual.capacitacao.revisao_falhas.change",
    },
    "qual_gerencia": {
        "qual.capacitacao.revisao_falhas.view",
        "qual.capacitacao.revisao_falhas.decide",
        "qual.capacitacao.revisao_falhas.change",
    },
}

ROUTE_NAME = "qualidade-capacitacao-revisao-falhas"


def add_revisao_falhas_permissions(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLE_PERMISSIONS):
        permissions = list(row.permissions or [])
        missing_permissions = sorted(ROLE_PERMISSIONS[row.role].difference(permissions))
        update_fields = []
        if missing_permissions:
            row.permissions = [*permissions, *missing_permissions]
            update_fields.append("permissions")

        # None representa acesso irrestrito. Em allowlists explicitas, inclua
        # somente a nova rota e preserve rotas personalizadas e sua ordem.
        if row.granted_routes is not None and ROUTE_NAME not in row.granted_routes:
            row.granted_routes = [*row.granted_routes, ROUTE_NAME]
            update_fields.append("granted_routes")

        if update_fields:
            update_fields.append("updated_at")
            row.save(update_fields=update_fields)


def remove_revisao_falhas_permissions(apps, schema_editor):
    # A definicao de perfil e customizavel. Um rollback nao consegue distinguir
    # com seguranca acessos preexistentes daqueles inseridos por esta migration.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0018_operational_support_notices_manage_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_revisao_falhas_permissions,
            remove_revisao_falhas_permissions,
        ),
    ]
