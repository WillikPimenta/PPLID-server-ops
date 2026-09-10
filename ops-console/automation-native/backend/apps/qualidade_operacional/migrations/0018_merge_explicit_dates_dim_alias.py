from django.db import migrations


class Migration(migrations.Migration):
    """Reconcilia os ramos independentes de datas e aliases de dimensão."""

    dependencies = [
        ("qualidade_operacional", "0016_merge_tratados_dim_alias"),
        ("qualidade_operacional", "0017_explicit_quality_dates"),
    ]

    operations = []
