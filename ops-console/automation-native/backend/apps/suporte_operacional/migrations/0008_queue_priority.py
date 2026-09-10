from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_operacional", "0007_alter_operationalsupportevent_event_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="queue_priority_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="operationalsupportevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("created", "Criação"),
                    ("auto_approved", "Aprovação automática"),
                    ("approved", "Aprovação"),
                    ("rejected", "Recusa"),
                    ("cancelled", "Cancelamento"),
                    ("assigned", "Atribuição"),
                    ("queue_assigned", "Fila automática"),
                    ("queue_directed", "Direcionamento"),
                    ("queue_prioritized", "Priorização da fila"),
                    ("queue_timeout", "Timeout da fila"),
                    ("queue_released", "Liberação da fila"),
                    ("presence_changed", "Status de presença"),
                    ("answered", "Resposta"),
                ],
                max_length=32,
            ),
        ),
    ]
