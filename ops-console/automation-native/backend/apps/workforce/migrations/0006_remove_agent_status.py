from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0005_agent_hire_date_drop_not_null"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="agent",
            name="agent_active_2265d0_idx",
        ),
        migrations.RemoveField(
            model_name="agent",
            name="status",
        ),
        migrations.AddIndex(
            model_name="agent",
            index=models.Index(fields=["active"], name="agent_active_idx"),
        ),
    ]
