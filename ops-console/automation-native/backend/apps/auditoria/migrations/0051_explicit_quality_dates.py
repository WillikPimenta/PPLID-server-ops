from django.db import migrations, models


class Migration(migrations.Migration):
    """Adiciona datas sem inferir nem alterar registros existentes."""

    dependencies = [
        ("auditoria", "0050_normalizar_tratados_por_etapa"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeanaliseorigem",
            name="protocolo_criado_em",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Data de criação do protocolo no sistema de origem.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="qualidadeanaliseorigem",
            name="protocolo_analisado_em",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Data de análise do protocolo no sistema de origem.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="qualidadeanaliseorigem",
            name="protocolo_concluido_em",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Data de conclusão do protocolo no sistema de origem.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="data_analise_intranet",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Momento em que o caso foi analisado ou concluído na Intranet.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="data_recepcao_contestacao",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Momento em que a contestação entrou no fluxo de auditoria.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="data_encerramento_atividade_intranet",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Momento em que o lote ou atividade foi encerrado na Intranet.",
                null=True,
            ),
        ),
    ]
