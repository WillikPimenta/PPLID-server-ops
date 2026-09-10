from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0004_alter_agent_hire_date_nullable"),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE agent ALTER COLUMN hire_date DROP NOT NULL;',
            reverse_sql='ALTER TABLE agent ALTER COLUMN hire_date SET NOT NULL;',
        ),
    ]
