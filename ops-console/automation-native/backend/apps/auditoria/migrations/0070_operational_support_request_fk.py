from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0013_operationalsupportrequest_operation_origin"),
        ("auditoria", "0069_contestacao_revisao_resultado"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="operational_support_request",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="Solicitação de suporte operacional respondida vinculada a este tratado.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auditoria_falhas_vinculadas",
                to="suporte_operacional.operationalsupportrequest",
            ),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="operational_support_request",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="Solicitação de suporte operacional respondida vinculada a este protocolo.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auditoria_protocolos_vinculados",
                to="suporte_operacional.operationalsupportrequest",
            ),
        ),
        migrations.AddField(
            model_name="contestacaooperacional",
            name="operational_support_request",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="Solicitação de suporte operacional respondida vinculada a esta contestação.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contestacoes_operacionais_vinculadas",
                to="suporte_operacional.operationalsupportrequest",
            ),
        ),
        migrations.AddField(
            model_name="qualidadependentereinspecao",
            name="operational_support_request",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="Solicitação de suporte operacional respondida vinculada a este pendente.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reinspecao_pendentes_vinculados",
                to="suporte_operacional.operationalsupportrequest",
            ),
        ),
        migrations.AddField(
            model_name="qualidadependenteauditoriacompliance",
            name="operational_support_request",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="Solicitação de suporte operacional respondida vinculada a este pendente.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auditoria_compliance_pendentes_vinculados",
                to="suporte_operacional.operationalsupportrequest",
            ),
        ),
    ]
