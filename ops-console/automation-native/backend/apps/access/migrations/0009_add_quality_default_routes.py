from django.db import migrations


ROLE_ROUTE_ADDITIONS = {
    "qual_auditoria_compliance": {
        "qualidade-compliance-auditoria-fila",
        "qualidade-compliance-auditoria-consulta",
    },
}


def add_quality_default_routes(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")

    for role, additions in ROLE_ROUTE_ADDITIONS.items():
        definition = PortalRoleDefinition.objects.filter(role=role).first()
        if definition is None:
            continue
        granted_routes = set(definition.granted_routes or [])
        granted_routes.update(additions)
        definition.granted_routes = sorted(granted_routes)
        definition.save(update_fields=["granted_routes", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0008_seed_customized_quality_roles"),
    ]

    operations = [
        migrations.RunPython(
            add_quality_default_routes,
            migrations.RunPython.noop,
        ),
    ]
