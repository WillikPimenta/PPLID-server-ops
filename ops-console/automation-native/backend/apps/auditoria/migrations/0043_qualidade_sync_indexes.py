# Compatibility anchor for databases that already recorded this migration name.
# The effective indexes are created idempotently by 0047_qualidade_sync_indexes,
# after the resultado_qualidade schema transition.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0042_fila_contexto_auditoria_compliance"),
    ]

    operations = []
