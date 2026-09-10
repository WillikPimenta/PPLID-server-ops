from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="agenthistory",
            name="job_activity",
            field=models.CharField(blank=True, max_length=255, verbose_name="atividade"),
        ),
        migrations.AddField(
            model_name="agenthistory",
            name="team_sector",
            field=models.CharField(blank=True, max_length=255, verbose_name="setor do time"),
        ),
        migrations.AddField(
            model_name="agenthistory",
            name="job_title_sector",
            field=models.CharField(blank=True, max_length=255, verbose_name="setor do cargo"),
        ),
        migrations.AddField(
            model_name="agenthistory",
            name="job_title_activity",
            field=models.CharField(
                blank=True, max_length=128, verbose_name="atividade do cargo"
            ),
        ),
        migrations.AddField(
            model_name="agenthistory",
            name="journey_shift",
            field=models.CharField(blank=True, max_length=64, verbose_name="turno"),
        ),
        migrations.AlterField(
            model_name="agenthistory",
            name="jira",
            field=models.CharField(blank=True, max_length=512, verbose_name="Jira"),
        ),
        migrations.AlterField(
            model_name="agenthistory",
            name="formalization",
            field=models.CharField(
                blank=True, max_length=255, verbose_name="formalização"
            ),
        ),
    ]
