import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("suporte_operacional", "0009_operationalsupportrequest_answer_option"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalSupportNotice",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=160)),
                ("message", models.TextField(max_length=3000)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="operational_support_notices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "operational_support_notice",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["active", "-created_at"], name="ops_notice_active_idx"),
                ],
            },
        ),
    ]
