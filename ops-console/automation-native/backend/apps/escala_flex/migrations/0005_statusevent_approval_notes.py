from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("escala_flex", "0004_statusevent_approval_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="statusevent",
            name="approval_notes",
            field=models.TextField(
                blank=True,
                help_text="Observações do líder na aprovação/rejeição.",
            ),
        ),
    ]
