# -*- coding: utf-8 -*-
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="CyberRiskOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("risk_id", models.CharField(max_length=80, unique=True)),
                ("status", models.CharField(
                    choices=[("open", "Aberto"), ("accepted", "Aceito"), ("resolved", "Resolvido")],
                    default="open",
                    max_length=20,
                )),
                ("note", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="cyber_risk_overrides",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["risk_id"]},
        ),
    ]
