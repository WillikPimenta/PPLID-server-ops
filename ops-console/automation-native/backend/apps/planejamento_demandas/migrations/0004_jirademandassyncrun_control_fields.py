from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("planejamento_demandas", "0003_jirademanda_status_kind"),
    ]

    operations = [
        migrations.AlterField(
            model_name="jirademandasyncrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Na fila"),
                    ("running", "Em execução"),
                    ("success", "Sucesso"),
                    ("failed", "Falha"),
                    ("cancelled", "Cancelada"),
                    ("interrupted", "Interrompida"),
                ],
                default="queued",
                max_length=16,
            ),
        ),
        migrations.AddField(model_name="jirademandasyncrun", name="mode", field=models.CharField(choices=[("full", "Completa"), ("incremental", "Incremental")], default="full", max_length=16)),
        migrations.AddField(model_name="jirademandasyncrun", name="duplicate_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="jirademandasyncrun", name="pages_processed", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="jirademandasyncrun", name="phase", field=models.CharField(blank=True, default="", max_length=32)),
        migrations.AddField(model_name="jirademandasyncrun", name="phase_index", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="jirademandasyncrun", name="phase_count", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="jirademandasyncrun", name="phase_fetched", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="jirademandasyncrun", name="phase_total", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="jirademandasyncrun", name="progress_percent", field=models.FloatField(default=0)),
        migrations.AddField(model_name="jirademandasyncrun", name="error_code", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="jirademandasyncrun", name="heartbeat_at", field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name="jirademandasyncrun", name="checkpoint_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="jirademandasyncrun", name="cancel_requested_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name="jirademandasyncrun",
            name="retry_of",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="retries", to="planejamento_demandas.jirademandasyncrun"),
        ),
    ]
