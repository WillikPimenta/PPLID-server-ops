from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0004_immutable_event_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="request_type",
            field=models.CharField(
                choices=[("online", "Online"), ("offline", "Offline")],
                db_index=True,
                default="online",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="operationalsupportrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending_leader", "Aguardando líder"),
                    ("pending_support", "Aguardando suporte"),
                    ("pending_offline", "Aguardando suporte offline"),
                    ("in_analysis", "Em análise"),
                    ("answered", "Respondida"),
                    ("rejected_leader", "Recusada pelo líder"),
                    ("cancelled", "Cancelada"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="operationalsupportrequest",
            index=models.Index(
                fields=["request_type", "status", "created_at"],
                name="ops_req_type_status_idx",
            ),
        ),
    ]
