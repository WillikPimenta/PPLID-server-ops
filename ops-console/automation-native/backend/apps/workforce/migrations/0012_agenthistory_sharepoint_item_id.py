# Generated manually for AgentHistory SharePoint source id

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0011_rename_cycle_change_audit_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="agenthistory",
            name="sharepoint_item_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Identificador de origem (lista tbHeadcount) para reimportações.",
                max_length=64,
                verbose_name="ID item SharePoint",
            ),
        ),
    ]
