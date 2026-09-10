from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("escala_flex", "0005_statusevent_approval_notes"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="statusevent",
            name="ef_status_e_leader__0a4dc8_idx",
        ),
        migrations.RemoveField(
            model_name="statusevent",
            name="display",
        ),
        migrations.RemoveField(
            model_name="statusevent",
            name="leader_lan_id",
        ),
        migrations.RemoveField(
            model_name="statusevent",
            name="sharepoint_id",
        ),
        migrations.AddIndex(
            model_name="statusevent",
            index=models.Index(fields=["leader"], name="ef_status_e_leader_id_idx"),
        ),
    ]
