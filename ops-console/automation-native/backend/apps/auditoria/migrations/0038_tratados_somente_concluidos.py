import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def preparar_base_central(apps, schema_editor):
    Atividade = apps.get_model("auditoria", "AuditoriaAtividade")
    Protocolo = apps.get_model("auditoria", "AuditoriaAtividadeProtocolo")
    Tratado = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    Rascunho = apps.get_model("auditoria", "QualidadePendenteAuditoriaFalha")
    PendenteReinspecao = apps.get_model("auditoria", "QualidadePendenteReinspecao")
    Historico = apps.get_model("auditoria", "ReinspecaoFilaHistorico")

    # Auditorias ainda abertas deixam a base central e voltam a ser rascunhos.
    auditoria_ids = Atividade.objects.filter(tipo="auditoria").values_list("id", flat=True)
    auditoria_aberta = Tratado.objects.filter(
        atividade_id__in=auditoria_ids,
    ).exclude(analise_status="concluido")
    for item in auditoria_aberta.iterator():
        Rascunho.objects.create(
            atividade_id=item.atividade_id,
            protocolo=item.protocolo,
            brflow_raw=item.brflow_raw,
            brflow_parsed=item.brflow_parsed,
            modulo=item.modulo,
            demanda_url=item.demanda_url,
            tipo_falha=item.tipo_falha,
            usuario=item.usuario,
            resultado_cliente=item.resultado_cliente,
            novo_resultado=item.novo_resultado,
            sinalizacao=item.sinalizacao,
            motivo_falha=item.motivo_falha,
            etapa_falha=item.etapa_falha,
            nivel_dificuldade=item.nivel_dificuldade,
            tipo_documento=item.tipo_documento,
            uf_documento=item.uf_documento,
            qualidade_imagem=item.qualidade_imagem,
            tipo_registro="auditoria",
            created_by_id=item.created_by_id,
        )
        item.delete()

    # Registros antigos de fila de reinspeção também não podem ficar na central.
    reinspecao_aberta = Tratado.objects.filter(tipo_registro="reinspecao").exclude(
        analise_status="concluido"
    )
    for item in reinspecao_aberta.iterator():
        pendente = PendenteReinspecao.objects.create(
            protocolo=item.protocolo,
            usuario=item.usuario,
            descricao_irregularidades=item.descricao_irregularidades,
            data_contestacao=item.data_contestacao,
            cliente=item.cliente,
            modulo=item.modulo,
            status=item.status,
            observacao=item.observacao,
            auditor=item.auditor,
            tipo_falha=item.tipo_falha,
            etapa_falha=item.etapa_falha,
            motivo_falha=item.motivo_falha,
            nivel_dificuldade=item.nivel_dificuldade,
            tipo_documento=item.tipo_documento,
            uf_documento=item.uf_documento,
            novo_resultado=item.novo_resultado,
            brflow_parsed=item.brflow_parsed,
            data_resposta=item.data_resposta,
            analise_concluida_em=item.analise_concluida_em,
            analise_status=item.analise_status,
            responsavel_id=item.responsavel_id,
            atribuido_em=item.atribuido_em,
            analise_iniciada_em=item.analise_iniciada_em,
            fila_origem=item.fila_origem,
            created_by_id=item.created_by_id,
        )
        Historico.objects.filter(falha_id=item.id).update(falha_id=None, pendente_id=pendente.id)
        item.delete()

    # Vincula tratados de contestação já existentes ao protocolo histórico.
    for tratado in Tratado.objects.filter(tipo_registro="contestacao").iterator():
        parsed = tratado.brflow_parsed if isinstance(tratado.brflow_parsed, dict) else {}
        protocolo_id = parsed.get("protocolo_origem_id")
        if protocolo_id:
            Protocolo.objects.filter(pk=protocolo_id, tratado_id__isnull=True).update(
                tratado_id=tratado.id
            )

    # Os registros restantes eram tratados legados criados antes do status obrigatório.
    Tratado.objects.exclude(analise_status="concluido").update(analise_status="concluido")


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0037_remove_public_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadePendenteAuditoriaFalha",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("protocolo", models.CharField(db_index=True, max_length=100)),
                ("brflow_raw", models.TextField(blank=True, default="")),
                ("brflow_parsed", models.JSONField(blank=True, default=dict)),
                ("modulo", models.CharField(blank=True, default="", max_length=255)),
                ("demanda_url", models.URLField(blank=True, default="", max_length=500)),
                ("tipo_falha", models.CharField(max_length=32)),
                ("usuario", models.CharField(max_length=255)),
                ("resultado_cliente", models.TextField(blank=True, default="")),
                ("novo_resultado", models.TextField(blank=True, default="")),
                ("sinalizacao", models.CharField(blank=True, default="", max_length=255)),
                ("motivo_falha", models.CharField(blank=True, default="", max_length=255)),
                ("etapa_falha", models.CharField(blank=True, default="", max_length=255)),
                ("nivel_dificuldade", models.CharField(blank=True, default="", max_length=100)),
                ("tipo_documento", models.CharField(blank=True, default="", max_length=255)),
                ("uf_documento", models.CharField(blank=True, default="", max_length=50)),
                ("qualidade_imagem", models.CharField(blank=True, default="", max_length=255)),
                ("tipo_registro", models.CharField(choices=[("auditoria", "Auditoria"), ("contestacao", "Contestação"), ("reinspecao", "Reinspeção")], db_index=True, default="auditoria", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("atividade", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="falhas", to="auditoria.auditoriaatividade")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="auditoria_falhas_pendentes_criadas", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "qualidade_pendente_auditoria_falha",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="tratado",
            field=models.OneToOneField(blank=True, help_text="Cópia consolidada deste protocolo na base única de tratados.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="contestacao_protocolo_origem", to="auditoria.auditoriafalhacadastro"),
        ),
        migrations.AlterField(
            model_name="auditoriafalhacadastro",
            name="atividade",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="tratados", to="auditoria.auditoriaatividade"),
        ),
        # Constraint/DDL em auditoria_falha_cadastro ficam na 0039: no PostgreSQL,
        # ALTER TABLE na mesma transação após DELETE/UPDATE do RunPython falha com
        # "cannot ALTER TABLE ... because it has pending trigger events".
        migrations.RunPython(preparar_base_central, migrations.RunPython.noop),
    ]
