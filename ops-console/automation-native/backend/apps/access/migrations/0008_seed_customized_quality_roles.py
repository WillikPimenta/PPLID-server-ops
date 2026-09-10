from django.db import migrations


# Perfis configurados em Configurações -> RBAC no ambiente local.
# A migration mantém a configuração reproduzível em bancos novos e existentes,
# sem transportar usuários ou atribuições de grupos entre ambientes.
ROLE_DEFINITIONS = (
    {
        "role": "qual_auditoria_compliance",
        "label": "Qual. Auditoria Compliance",
        "area": "qualidade",
        "permissions": [
            "portal.secao.view",
            "qual.auditoria.view",
        ],
        "granted_routes": [
            "qualidade-compliance-auditoria",
            "qualidade-compliance-auditoria-fila",
            "qualidade-compliance-auditoria-consulta",
            "section",
        ],
        "default_scope": "global",
        "is_editable": True,
    },
    {
        "role": "qual_auditoria_fraud",
        "label": "Qual. Auditoria Fraud",
        "area": "qualidade",
        "permissions": [
            "portal.secao.view",
            "qual.auditoria.view",
            "qual.auditoria.create",
        ],
        "granted_routes": [
            "qualidade-auditoria-atividade-detalhe",
            "qualidade-auditoria-cadastro",
            "qualidade-auditoria-consulta",
            "qualidade-auditoria-protocolo-analise",
            "section",
        ],
        "default_scope": "global",
        "is_editable": True,
    },
    {
        "role": "qual_contestacao_compliance",
        "label": "Qual. Reinspeção",
        "area": "qualidade",
        "permissions": [
            "portal.secao.view",
            "qual.auditoria.view",
        ],
        "granted_routes": [
            "qualidade-compliance-auditoria",
            "qualidade-compliance-contestacao",
            "qualidade-compliance-contestacao-interna",
            "qualidade-compliance-contestacao-reinspecao-fila",
            "section",
        ],
        "default_scope": "own",
        "is_editable": True,
    },
    {
        "role": "qual_contestacao_fraud",
        "label": "Qual. Contestação Fraud",
        "area": "qualidade",
        "permissions": [
            "portal.secao.view",
            "qual.auditoria.view",
            "qual.auditoria.create",
        ],
        "granted_routes": [
            "qualidade-contestacao-atividade-detalhe",
            "qualidade-contestacao-cadastro",
            "qualidade-contestacao-consulta",
            "qualidade-contestacao-protocolo-analise",
            "qualidade-contestacao-remocao-base-negativa",
            "qualidade-contestacao-remocao-base-positiva",
            "qualidade-contestacao-solicitacoes-idas-bio",
            "section",
        ],
        "default_scope": "global",
        "is_editable": True,
    },
)


def seed_customized_quality_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")

    for definition in ROLE_DEFINITIONS:
        role = definition["role"]
        defaults = {
            key: value for key, value in definition.items() if key != "role"
        }
        PortalRoleDefinition.objects.update_or_create(role=role, defaults=defaults)
        Group.objects.get_or_create(name=f"role:{role}")


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0007_portalroledefinition_area"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(
            seed_customized_quality_roles,
            migrations.RunPython.noop,
        ),
    ]
