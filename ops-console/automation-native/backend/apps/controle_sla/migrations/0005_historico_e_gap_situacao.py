# Generated manually for historico + etapa gap situacao

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def backfill_gap_timestamps(apps, schema_editor):
    EtapaGap = apps.get_model("controle_sla", "EtapaGap")
    now = timezone.now()
    for row in EtapaGap.objects.all().iterator():
        updates = []
        if getattr(row, "first_seen_at", None) is None:
            row.first_seen_at = now
            updates.append("first_seen_at")
        if getattr(row, "last_seen_at", None) is None:
            row.last_seen_at = now
            updates.append("last_seen_at")
        if updates:
            row.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("controle_sla", "0004_slabreach_tipo_analise"),
    ]

    operations = [
        migrations.CreateModel(
            name="SlaBreachHistorico",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cod_cliente", models.PositiveIntegerField()),
                ("nom_cliente", models.CharField(max_length=512)),
                ("nom_workflow", models.CharField(blank=True, default="", max_length=512)),
                ("id_workflow", models.PositiveIntegerField(blank=True, null=True)),
                ("cod_nivel_hierarquico", models.PositiveIntegerField(blank=True, null=True)),
                ("nom_fluxo", models.CharField(max_length=512)),
                ("tipo_analise", models.CharField(blank=True, default="", max_length=16)),
                ("qtd_registro", models.PositiveIntegerField(blank=True, null=True)),
                ("qtd_fila", models.PositiveIntegerField(blank=True, null=True)),
                ("dat_registro_antigo", models.DateTimeField()),
                ("idade_segundos", models.PositiveIntegerField()),
                ("sla_limite_segundos", models.PositiveIntegerField()),
                ("excedente_segundos", models.PositiveIntegerField(default=0)),
                ("pct_sla", models.FloatField(default=0)),
                (
                    "criticidade",
                    models.CharField(
                        choices=[
                            ("baixo", "Baixo"),
                            ("medio", "Médio"),
                            ("alto", "Alto"),
                            ("critico", "Crítico"),
                        ],
                        db_index=True,
                        default="baixo",
                        max_length=16,
                    ),
                ),
                ("first_detected_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField()),
            ],
            options={
                "db_table": "controle_sla_breach_historico",
                "ordering": ["-last_seen_at", "-pct_sla"],
            },
        ),
        migrations.AddField(
            model_name="etapagap",
            name="id_workflow",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="etapagap",
            name="id_cliente_megazord",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="etapagap",
            name="situacao",
            field=models.CharField(
                choices=[
                    ("pendente", "Pendente"),
                    ("ignorada", "Ignorada"),
                    ("cadastrada", "Cadastrada"),
                ],
                db_index=True,
                default="pendente",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="etapagap",
            name="ocorrencias",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="etapagap",
            name="tratado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="controle_sla_gaps_tratados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="etapagap",
            name="tratado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="etapagap",
            name="projecao_sla_id",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="ID da primeira regra Projeção SLA vinculada após cadastrar.",
                null=True,
            ),
        ),
        # Replace auto_now fields with explicit timestamps (keep values).
        migrations.AlterField(
            model_name="etapagap",
            name="first_seen_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AlterField(
            model_name="etapagap",
            name="last_seen_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(backfill_gap_timestamps, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="etapagap",
            name="first_seen_at",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="etapagap",
            name="last_seen_at",
            field=models.DateTimeField(),
        ),
        migrations.AddIndex(
            model_name="slabreach",
            index=models.Index(fields=["dat_registro_antigo"], name="controle_sl_dat_reg_9a1b2c_idx"),
        ),
        migrations.AddIndex(
            model_name="etapagap",
            index=models.Index(fields=["situacao", "last_seen_at"], name="controle_sl_situaca_7c3d4e_idx"),
        ),
        migrations.AddConstraint(
            model_name="slabreachhistorico",
            constraint=models.UniqueConstraint(
                fields=("cod_cliente", "id_workflow", "dat_registro_antigo"),
                name="uq_controle_sla_breach_hist_key",
            ),
        ),
        migrations.AddIndex(
            model_name="slabreachhistorico",
            index=models.Index(fields=["cod_cliente", "id_workflow"], name="controle_sl_cod_cli_hist1_idx"),
        ),
        migrations.AddIndex(
            model_name="slabreachhistorico",
            index=models.Index(fields=["dat_registro_antigo"], name="controle_sl_dat_reg_hist2_idx"),
        ),
        migrations.AddIndex(
            model_name="slabreachhistorico",
            index=models.Index(fields=["first_detected_at"], name="controle_sl_first_d_hist3_idx"),
        ),
        migrations.AddIndex(
            model_name="slabreachhistorico",
            index=models.Index(fields=["last_seen_at"], name="controle_sl_last_se_hist4_idx"),
        ),
    ]
