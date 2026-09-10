from django.db import migrations, models


FACT_MODELS = ("qualidadeauditado", "qualidadefalha")
DATE_FIELDS = (
    "data_analise_intranet",
    "data_analise_origem",
    "data_criacao_origem",
    "data_conclusao_origem",
    "data_recepcao_contestacao",
    "data_encerramento_atividade_intranet",
)


class Migration(migrations.Migration):
    """Campos de projeção explícitos; não contém backfill de dados."""

    dependencies = [
        ("auditoria", "0051_explicit_quality_dates"),
        ("qualidade_operacional", "0016_reprojetar_apos_g_auditoria"),
    ]

    operations = [
        migrations.AddField(
            model_name=model_name,
            name=field_name,
            field=models.DateField(blank=True, null=True),
        )
        for model_name in FACT_MODELS
        for field_name in DATE_FIELDS
    ]
