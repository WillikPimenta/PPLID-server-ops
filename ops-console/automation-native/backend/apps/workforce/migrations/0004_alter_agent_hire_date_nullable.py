from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0003_agent_status_column"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agent",
            name="hire_date",
            field=models.DateField(
                blank=True, null=True, verbose_name="data de admissão"
            ),
        ),
    ]
