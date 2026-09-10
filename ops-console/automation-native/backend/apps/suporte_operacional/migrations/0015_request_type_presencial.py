from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0014_ops_req_protocol_origin_status_idx"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="presencial_support_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="operational_support_presencial_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="operationalsupportrequest",
            name="request_type",
            field=models.CharField(
                choices=[
                    ("online", "Online"),
                    ("offline", "Offline"),
                    ("presencial", "Presencial"),
                ],
                db_index=True,
                default="online",
                max_length=16,
            ),
        ),
    ]
