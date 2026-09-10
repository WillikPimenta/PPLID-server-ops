# Generated manually for Team/Category fields on FalhasAgent

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('falhas_criticas', '0004_syncauditlog_stats'),
    ]

    operations = [
        migrations.AddField(
            model_name='falhasagent',
            name='team',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='falhasagent',
            name='team_categoria',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
