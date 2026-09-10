from django.db import migrations

from apps.access.constants import ALL_ROLES, role_group_name


def seed_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for role in ALL_ROLES:
        Group.objects.get_or_create(name=role_group_name(role))


def unseed_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    names = [role_group_name(role) for role in ALL_ROLES]
    Group.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(seed_role_groups, unseed_role_groups),
    ]
