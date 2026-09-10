import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0009_add_quality_default_routes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PortalMenuOption",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "menu_key",
                    models.CharField(
                        db_index=True,
                        max_length=128,
                        unique=True,
                    ),
                ),
                ("label", models.CharField(max_length=128)),
                (
                    "description",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("section", models.CharField(max_length=64)),
                ("route_names", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(default=True)),
                ("is_builtin", models.BooleanField(default=False)),
                ("is_editable", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="menu_option_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Opção de menu RBAC",
                "verbose_name_plural": "Opções de menu RBAC",
                "ordering": ["section", "label", "menu_key"],
            },
        ),
    ]
