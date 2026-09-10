from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0015_auditoriacontroleregistro"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditoriacatalogitem",
            name="catalog",
            field=models.CharField(
                choices=[
                    ("modulo", "Módulo"),
                    ("tipo_falha", "Tipo de falha"),
                    ("novo_resultado", "Novo resultado"),
                    ("sinalizacao", "Sinalização"),
                    ("etapa_falha", "Etapa da falha"),
                    ("nivel_dificuldade", "Nível de dificuldade"),
                    ("tipo_documento", "Tipo de documento"),
                    ("uf_documento", "UF do documento"),
                    ("cruzamento_bases", "Cruzamento de bases"),
                    ("qualidade_imagem", "Qualidade da imagem"),
                    ("tipo_acao_controle", "Tipo de ação (controles)"),
                    ("motivo_base_negativa", "Motivo (base negativa)"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
