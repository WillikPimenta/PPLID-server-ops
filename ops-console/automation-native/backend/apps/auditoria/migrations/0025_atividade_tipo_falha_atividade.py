from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0024_responsavel_headcount_agent"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividade",
            name="tipo",
            field=models.CharField(
                choices=[("contestacao", "Contestação"), ("auditoria", "Auditoria")],
                db_index=True,
                default="contestacao",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="auditoriaatividade",
            name="brflow_raw",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="auditoriaatividade",
            name="brflow_parsed",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="atividade",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="falhas",
                to="auditoria.auditoriaatividade",
            ),
        ),
    ]
