from django.db import migrations

from apps.access.constants import (
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_AUDITORIA_FRAUD,
    ROLE_QUAL_CAPACITACAO,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_FRAUD,
    role_group_name,
)

NEW_QUAL_ROLES = (
    ROLE_QUAL_AUDITORIA_FRAUD,
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_FRAUD,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    ROLE_QUAL_CAPACITACAO,
)


def seed_new_qual_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for role in NEW_QUAL_ROLES:
        Group.objects.get_or_create(name=role_group_name(role))


def unseed_new_qual_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    names = [role_group_name(role) for role in NEW_QUAL_ROLES]
    Group.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0004_portal_role_definition"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(seed_new_qual_role_groups, unseed_new_qual_role_groups),
    ]
