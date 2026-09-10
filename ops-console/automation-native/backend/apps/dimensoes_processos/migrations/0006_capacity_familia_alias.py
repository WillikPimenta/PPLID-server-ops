from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dimensoes_processos", "0005_dim_nome_alias_classificacao"),
    ]

    operations = [
        migrations.CreateModel(
            name="CapacityFamiliaAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("alias_key", models.CharField(max_length=512, unique=True)),
                ("alias_origem", models.CharField(max_length=512)),
                ("familia_canonica", models.CharField(max_length=512)),
                ("ativo", models.BooleanField(default=True)),
                ("notas", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="capacity_familia_aliases_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "capacity_familia_alias",
                "ordering": ["familia_canonica", "alias_origem"],
            },
        ),
    ]
