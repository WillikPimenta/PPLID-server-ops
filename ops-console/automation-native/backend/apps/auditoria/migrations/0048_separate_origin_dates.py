from django.db import migrations, models


AUDITORIA_COMPLIANCE = "auditoria_compliance"


def _separate_origin_dates(apps, schema_editor):
    AuditoriaFalhaCadastro = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    QualidadePendenteReinspecao = apps.get_model(
        "auditoria",
        "QualidadePendenteReinspecao",
    )

    QualidadePendenteReinspecao.objects.filter(
        contexto=AUDITORIA_COMPLIANCE,
        data_analise__isnull=True,
    ).exclude(data_contestacao__isnull=True).update(
        data_analise=models.F("data_contestacao"),
        data_contestacao=None,
    )

    AuditoriaFalhaCadastro.objects.filter(
        brflow_parsed__fila_contexto=AUDITORIA_COMPLIANCE,
        data_analise__isnull=True,
    ).exclude(data_contestacao__isnull=True).update(
        data_analise=models.F("data_contestacao"),
        data_contestacao=None,
    )


def _restore_shared_date(apps, schema_editor):
    AuditoriaFalhaCadastro = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    QualidadePendenteReinspecao = apps.get_model(
        "auditoria",
        "QualidadePendenteReinspecao",
    )

    QualidadePendenteReinspecao.objects.filter(
        contexto=AUDITORIA_COMPLIANCE,
        data_contestacao__isnull=True,
    ).exclude(data_analise__isnull=True).update(
        data_contestacao=models.F("data_analise"),
    )

    AuditoriaFalhaCadastro.objects.filter(
        brflow_parsed__fila_contexto=AUDITORIA_COMPLIANCE,
        data_contestacao__isnull=True,
    ).exclude(data_analise__isnull=True).update(
        data_contestacao=models.F("data_analise"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0043_qualidade_sync_indexes"),
        ("auditoria", "0047_qualidade_sync_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="data_analise",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="qualidadependentereinspecao",
            name="data_analise",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(_separate_origin_dates, _restore_shared_date),
    ]
