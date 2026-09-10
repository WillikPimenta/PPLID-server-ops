from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0068_merge_20260831_1524"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="contestacaooperacional",
            name="analista_decisao",
            field=models.ForeignKey(
                blank=True,
                help_text="Analista que registrou a decisão original (preservado em revisões).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contestacoes_operacionais_decididas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="contestacaooperacional",
            name="falhas_manter_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="contestacaooperacional",
            name="parecer_revisao",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="contestacaooperacionalhistorico",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="contestacaooperacionalhistorico",
            name="evento",
            field=models.CharField(
                choices=[
                    ("criada", "Criada"),
                    ("analise_iniciada", "Análise iniciada"),
                    ("decidida", "Decidida"),
                    ("devolvida_fila", "Devolvida à fila"),
                    ("reclassificada", "Reclassificada"),
                    ("resultado_alterado", "Resultado alterado"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
