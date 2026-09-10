from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dimensoes_processos", "0006_capacity_familia_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="CapacityDailySnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("calculation_date", models.DateField(unique=True)),
                ("payload", models.JSONField(default=dict)),
                ("generated_at", models.DateTimeField()),
                ("valid_until", models.DateTimeField(db_index=True)),
                ("generation_id", models.UUIDField(db_index=True)),
            ],
            options={
                "db_table": "capacity_daily_snapshot",
                "ordering": ["calculation_date"],
            },
        ),
    ]
