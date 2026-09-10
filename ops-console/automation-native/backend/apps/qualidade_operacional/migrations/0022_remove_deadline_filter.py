# Remove filtro de prazo 120d: tabelas de projeção/rollups e índice parcial.
from django.contrib.postgres.operations import RemoveIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("qualidade_operacional", "0021_deadline_protocol_scope_and_rollups"),
    ]

    operations = [
        RemoveIndexConcurrently(
            model_name="qualidadeauditado",
            name="qo_aud_deadline_protocol_cov",
        ),
        migrations.DeleteModel(name="QualidadeEoMonthlyRollup"),
        migrations.DeleteModel(name="QualidadeDeadlineProtocoloScope"),
        migrations.DeleteModel(name="QualidadeDeadlineFalhaScope"),
        migrations.DeleteModel(name="QualidadeDeadlineAuditadoScope"),
        migrations.DeleteModel(name="QualidadeDeadlineGeneration"),
    ]
