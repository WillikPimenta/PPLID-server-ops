from django.db import migrations, models


def migrate_ausente_to_presencial(apps, schema_editor):
    Presence = apps.get_model("suporte_operacional", "OperationalSupportAgentPresence")
    Presence.objects.filter(status="ausente").update(status="presencial")


def migrate_presencial_to_ausente(apps, schema_editor):
    Presence = apps.get_model("suporte_operacional", "OperationalSupportAgentPresence")
    Presence.objects.filter(status="presencial").update(status="ausente")


class Migration(migrations.Migration):
    dependencies = [("suporte_operacional", "0011_operationalsupportnotice_inactive_at")]

    operations = [
        migrations.RunPython(migrate_ausente_to_presencial, migrate_presencial_to_ausente),
        migrations.AlterField(
            model_name="operationalsupportagentpresence",
            name="status",
            field=models.CharField(
                choices=[
                    ("online", "Online"),
                    ("offline", "Offline"),
                    ("presencial", "Presencial"),
                ],
                db_index=True,
                default="offline",
                max_length=16,
            ),
        ),
    ]
