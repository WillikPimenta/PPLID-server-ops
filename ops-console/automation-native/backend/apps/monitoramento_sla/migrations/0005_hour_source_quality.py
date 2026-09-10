from django.db import migrations, models


def mark_date_only_sync_hours_unavailable(apps, schema_editor):
    SyncRun = apps.get_model("monitoramento_sla", "SlaUtilSyncRun")
    Detalhe = apps.get_model("monitoramento_sla", "SlaUtilDetalhe")
    Consolidado = apps.get_model("monitoramento_sla", "SlaUtilConsolidado")

    # RotinaDetalhadoBrutoRecord possui somente data. O 00:00 persistido por
    # essas cargas é marcador técnico, não evidência de recebimento à meia-noite.
    date_only_run_ids = list(
        SyncRun.objects.filter(rows_detalhe__gt=0).values_list("pk", flat=True)
    )
    if not date_only_run_ids:
        return
    Detalhe.objects.filter(sync_run_id__in=date_only_run_ids).update(
        hora_cadastro=None,
        hora_cadastro_fonte="unavailable",
    )
    Consolidado.objects.filter(sync_run_id__in=date_only_run_ids).update(
        hora_cadastro=None,
        hora_cadastro_fonte="unavailable",
    )


class Migration(migrations.Migration):
    dependencies = [("monitoramento_sla", "0004_remove_derivacao_etapa_state")]

    operations = [
        migrations.AddField(
            model_name="slautildetalhe",
            name="hora_cadastro_fonte",
            field=models.CharField(
                choices=[
                    ("real", "Horário real da origem"),
                    ("unavailable", "Horário indisponível"),
                ],
                db_index=True,
                default="real",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="slautilconsolidado",
            name="hora_cadastro_fonte",
            field=models.CharField(
                choices=[
                    ("real", "Horário real da origem"),
                    ("unavailable", "Horário indisponível"),
                ],
                db_index=True,
                default="real",
                max_length=16,
            ),
        ),
        migrations.RunPython(
            mark_date_only_sync_hours_unavailable,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
