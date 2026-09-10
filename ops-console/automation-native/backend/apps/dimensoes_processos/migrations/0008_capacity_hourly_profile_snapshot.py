from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dimensoes_processos", "0007_capacity_daily_snapshot")]

    operations = [
        migrations.CreateModel(
            name="CapacityHourlyProfileSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quarter_from", models.DateField()),
                ("quarter_to", models.DateField()),
                ("weekday", models.PositiveSmallIntegerField(help_text="Segunda=0 ... domingo=6")),
                ("scope", models.CharField(choices=[("workflow", "Cliente + workflow"), ("client", "Cliente")], max_length=16)),
                ("id_cliente", models.PositiveIntegerField()),
                ("id_workflow", models.PositiveIntegerField(default=0)),
                ("hourly_volumes", models.JSONField(default=list)),
                ("trusted_volume", models.BigIntegerField(default=0)),
                ("generated_at", models.DateTimeField()),
            ],
            options={"db_table": "capacity_hourly_profile_snapshot"},
        ),
        migrations.AddConstraint(
            model_name="capacityhourlyprofilesnapshot",
            constraint=models.UniqueConstraint(
                fields=("quarter_from", "quarter_to", "weekday", "scope", "id_cliente", "id_workflow"),
                name="uniq_capacity_hourly_profile",
            ),
        ),
        migrations.AddIndex(
            model_name="capacityhourlyprofilesnapshot",
            index=models.Index(
                fields=["quarter_to", "weekday", "id_cliente", "id_workflow"],
                name="cap_hour_profile_lookup_idx",
            ),
        ),
    ]
