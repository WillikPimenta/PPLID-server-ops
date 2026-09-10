from django.db import migrations, models


def ensure_status_column(apps, schema_editor):
    """Add status only if missing (0001_initial may already include it)."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'agent' AND column_name = 'status'"
        )
        if cursor.fetchone():
            return

    Agent = apps.get_model("workforce", "Agent")
    field = models.CharField(
        choices=[
            ("ACTIVE", "Ativo"),
            ("ON_LEAVE", "Afastado"),
            ("TERMINATED", "Desligado"),
        ],
        default="ACTIVE",
        max_length=32,
        verbose_name="status",
    )
    field.set_attributes_from_name("status")
    schema_editor.add_field(Agent, field)


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0002_agenthistory_excel_fields"),
    ]

    operations = [
        migrations.RunPython(ensure_status_column, migrations.RunPython.noop),
    ]
