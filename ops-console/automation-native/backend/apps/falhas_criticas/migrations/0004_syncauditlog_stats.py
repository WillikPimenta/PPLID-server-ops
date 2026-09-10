# Generated manually for Fase 3.2
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('falhas_criticas', '0003_portal_tickets'),
    ]

    operations = [
        migrations.AddField(
            model_name='syncauditlog',
            name='stats',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
