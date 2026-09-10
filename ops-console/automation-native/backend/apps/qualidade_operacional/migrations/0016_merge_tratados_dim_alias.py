from django.db import migrations


class Migration(migrations.Migration):
    """Reconcile the independent tratado and dimension-alias migration branches."""

    dependencies = [
        ("qualidade_operacional", "0014_reprojetar_tratados_por_etapa"),
        ("qualidade_operacional", "0015_sicredi_dim_alias"),
    ]

    operations = []
