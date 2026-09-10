from django.db import migrations


ROLE_PERMISSIONS = {
    "qual_contestacao_fraud": {
        "qual.contestacao_interna.fraud.view",
        "qual.contestacao_interna.fraud.analyze",
    },
    "qual_contestacao_compliance": {
        "qual.contestacao_interna.compliance.view",
        "qual.contestacao_interna.compliance.analyze",
    },
}

ROLE_ROUTES = {
    "qual_contestacao_fraud": {"qualidade-fraud-contestacao-interna"},
}


def add_contestacao_interna_permissions(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.filter(role__in=ROLE_PERMISSIONS):
        permissions = list(row.permissions or [])
        missing = sorted(ROLE_PERMISSIONS[row.role].difference(permissions))
        update_fields = []
        if missing:
            row.permissions = [*permissions, *missing]
            update_fields.append("permissions")

        # None significa acesso irrestrito por rota e deve continuar assim.
        # Para allowlists explícitas, inclua apenas a nova rota necessária,
        # preservando a ordem e quaisquer customizações existentes.
        if row.granted_routes is not None:
            granted_routes = list(row.granted_routes)
            missing_routes = sorted(
                ROLE_ROUTES.get(row.role, set()).difference(granted_routes)
            )
            if missing_routes:
                row.granted_routes = [*granted_routes, *missing_routes]
                update_fields.append("granted_routes")

        if update_fields:
            row.save(update_fields=update_fields)


def remove_contestacao_interna_permissions(apps, schema_editor):
    # Alteração aditiva sobre registros customizáveis: não há como distinguir
    # com segurança valores preexistentes dos inseridos pela migration.
    # O rollback preserva permissões e rotas para não revogar acessos legítimos.
    pass


class Migration(migrations.Migration):

    dependencies = [("access", "0016_split_auditoria_compliance_permissions")]

    operations = [
        migrations.RunPython(
            add_contestacao_interna_permissions,
            remove_contestacao_interna_permissions,
        )
    ]
