from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0008_agent_jira_api_token"),
    ]

    operations = [
        migrations.CreateModel(
            name="HeadcountCatalogItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("catalog", models.CharField(choices=[("team", "Time"), ("job_title", "Cargo"), ("job_activity", "Atividade"), ("location", "Local"), ("team_sector", "Setor do time"), ("job_title_sector", "Setor do cargo"), ("job_title_activity", "Atividade do cargo"), ("band", "Band")], db_index=True, max_length=32)),
                ("value", models.CharField(max_length=255)),
                ("label", models.CharField(blank=True, default="", max_length=255)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "headcount_catalog_item",
                "ordering": ["catalog", "sort_order", "value"],
            },
        ),
        migrations.AddConstraint(
            model_name="headcountcatalogitem",
            constraint=models.UniqueConstraint(fields=("catalog", "value"), name="uniq_headcount_catalog_value"),
        ),
    ]
