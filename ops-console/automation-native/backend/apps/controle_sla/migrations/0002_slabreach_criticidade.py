# Generated manually for Controle SLA criticidade ≥80%

from django.db import migrations, models


def backfill_criticidade(apps, schema_editor):
    SlaBreach = apps.get_model("controle_sla", "SlaBreach")
    for row in SlaBreach.objects.all().iterator():
        limit = row.sla_limite_segundos or 1
        pct = (float(row.idade_segundos) / float(limit)) * 100.0
        if pct >= 100:
            crit = "critico"
        elif pct >= 95:
            crit = "alto"
        elif pct >= 90:
            crit = "medio"
        else:
            crit = "baixo"
        row.pct_sla = pct
        row.criticidade = crit
        row.save(update_fields=["pct_sla", "criticidade"])


class Migration(migrations.Migration):

    dependencies = [
        ("controle_sla", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="slabreach",
            name="pct_sla",
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name="slabreach",
            name="criticidade",
            field=models.CharField(
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
        migrations.AlterModelOptions(
            name="slabreach",
            options={"ordering": ["-pct_sla", "nom_cliente", "nom_fluxo"]},
        ),
        migrations.AddIndex(
            model_name="slabreach",
            index=models.Index(
                fields=["criticidade", "-pct_sla"],
                name="controle_sl_crit_pct_idx",
            ),
        ),
        migrations.RunPython(backfill_criticidade, migrations.RunPython.noop),
    ]
