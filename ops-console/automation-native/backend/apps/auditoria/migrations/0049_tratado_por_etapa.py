import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0048_separate_origin_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="protocolo_origem",
            field=models.ForeignKey(
                blank=True,
                help_text="Protocolo operacional que originou esta etapa tratada.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tratados_etapas",
                to="auditoria.auditoriaatividadeprotocolo",
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="etapa_origem",
            field=models.OneToOneField(
                blank=True,
                help_text="Etapa operacional promovida para a base central.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tratado",
                to="auditoria.auditoriaatividadeprotocoloetapa",
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="etapa_chave",
            field=models.CharField(
                blank=True,
                help_text="Chave idempotente e imutável da etapa tratada.",
                max_length=160,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="ordem_etapa",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="etapa_criada_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="etapa_atualizada_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="tempo_analise",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="cruzamento_bases",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddIndex(
            model_name="auditoriafalhacadastro",
            index=models.Index(
                fields=["origem", "protocolo"],
                name="aud_fal_ori_prot_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditoriafalhacadastro",
            index=models.Index(
                fields=["origem", "usuario", "analise_concluida_em"],
                name="aud_fal_ori_usr_dt_idx",
            ),
        ),
    ]
