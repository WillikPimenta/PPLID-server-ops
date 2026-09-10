# Generated manually for controle_sla

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SlaBreach",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cod_cliente", models.PositiveIntegerField()),
                ("nom_cliente", models.CharField(max_length=512)),
                ("nom_workflow", models.CharField(blank=True, default="", max_length=512)),
                ("id_workflow", models.PositiveIntegerField(blank=True, null=True)),
                ("cod_nivel_hierarquico", models.PositiveIntegerField(blank=True, null=True)),
                ("nom_fluxo", models.CharField(max_length=512)),
                ("qtd_registro", models.PositiveIntegerField(blank=True, null=True)),
                ("qtd_fila", models.PositiveIntegerField(blank=True, null=True)),
                ("dat_registro_antigo", models.DateTimeField()),
                ("idade_segundos", models.PositiveIntegerField()),
                ("sla_limite_segundos", models.PositiveIntegerField()),
                ("excedente_segundos", models.PositiveIntegerField(default=0)),
                ("first_detected_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "controle_sla_breach",
                "ordering": ["-excedente_segundos", "nom_cliente", "nom_fluxo"],
            },
        ),
        migrations.CreateModel(
            name="EtapaGap",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cod_cliente", models.PositiveIntegerField()),
                ("nom_cliente", models.CharField(max_length=512)),
                ("nom_workflow", models.CharField(blank=True, default="", max_length=512)),
                ("cod_nivel_hierarquico", models.PositiveIntegerField(blank=True, null=True)),
                ("nom_fluxo", models.CharField(max_length=512)),
                ("qtd_fila", models.PositiveIntegerField(blank=True, null=True)),
                ("dat_registro_antigo", models.DateTimeField(blank=True, null=True)),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "controle_sla_etapa_gap",
                "ordering": ["nom_cliente", "nom_fluxo"],
            },
        ),
        migrations.AddIndex(
            model_name="etapagap",
            index=models.Index(fields=["cod_cliente"], name="controle_sl_cod_cli_7a1b2c_idx"),
        ),
        migrations.AddIndex(
            model_name="etapagap",
            index=models.Index(fields=["last_seen_at"], name="controle_sl_last_se_8d3e4f_idx"),
        ),
        migrations.AddIndex(
            model_name="slabreach",
            index=models.Index(fields=["cod_cliente", "nom_fluxo"], name="controle_sl_cod_cli_1a2b3c_idx"),
        ),
        migrations.AddIndex(
            model_name="slabreach",
            index=models.Index(fields=["last_seen_at"], name="controle_sl_last_se_4d5e6f_idx"),
        ),
        migrations.AddConstraint(
            model_name="etapagap",
            constraint=models.UniqueConstraint(fields=("cod_cliente", "nom_fluxo"), name="uq_controle_sla_etapa_gap_key"),
        ),
        migrations.AddConstraint(
            model_name="slabreach",
            constraint=models.UniqueConstraint(
                fields=("cod_cliente", "nom_fluxo", "cod_nivel_hierarquico"),
                name="uq_controle_sla_breach_key",
            ),
        ),
    ]
