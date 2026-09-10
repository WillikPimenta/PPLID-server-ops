from django.db import migrations, models


def _backfill_outcomes(apps, schema_editor):
    Workflow = apps.get_model("replicacao_d1", "ReplicacaoD1WorkflowDia")
    mappings = {
        "SALVO_OK": ("salvo", "info", "recebido"),
        "UPLOAD_OK": ("salvo", "info", "recebido"),
        "SEM_ALTERACAO": ("sem_alteracao", "aviso", "recebido"),
        "PROCESSANDO": ("processando", "info", "pendente"),
        "PULADO": ("pulado", "aviso", "ignorado"),
        "INATIVO": ("inativo", "info", "ignorado"),
        "NAO_SALVO": ("nao_salvo", "erro", "falhou"),
        "ERRO": ("falhou", "erro", "falhou"),
        "FALHOU": ("falhou", "erro", "falhou"),
        "FALHA": ("falhou", "erro", "falhou"),
        "TIMEOUT": ("falhou", "erro", "falhou"),
        "CANCELADO": ("cancelado", "aviso", "ignorado"),
    }
    recognized = models.Q(pk__in=[])
    resultado_whens = []
    severidade_whens = []
    status_whens = []
    motivo_codigo_whens = []
    motivo_resumo_whens = []
    for raw, (resultado, severidade, status_operacional) in mappings.items():
        condition = models.Q(status_brflow__iexact=raw)
        recognized |= condition
        resultado_whens.append(models.When(condition, then=models.Value(resultado)))
        severidade_whens.append(models.When(condition, then=models.Value(severidade)))
        status_whens.append(
            models.When(condition, then=models.Value(status_operacional))
        )
        if resultado in {"falhou", "nao_salvo"}:
            motivo_codigo_whens.extend(
                [
                    models.When(
                        condition & models.Q(erro_codigo=""),
                        then=models.Value(f"BRFLOW_{raw}"),
                    ),
                    models.When(condition, then=models.F("erro_codigo")),
                ]
            )
            motivo_resumo_whens.append(
                models.When(condition, then=models.F("erro_resumo"))
            )

    # Uma única varredura substitui as 12 atualizações sequenciais anteriores.
    # Isso reduz substancialmente locks e tempo de transação em produção.
    Workflow.objects.filter(recognized).update(
        resultado=models.Case(
            *resultado_whens,
            default=models.F("resultado"),
            output_field=models.CharField(),
        ),
        severidade=models.Case(
            *severidade_whens,
            default=models.F("severidade"),
            output_field=models.CharField(),
        ),
        status_operacional=models.Case(
            *status_whens,
            default=models.F("status_operacional"),
            output_field=models.CharField(),
        ),
        motivo_codigo=models.Case(
            *motivo_codigo_whens,
            default=models.F("motivo_codigo"),
            output_field=models.CharField(),
        ),
        motivo_resumo=models.Case(
            *motivo_resumo_whens,
            default=models.F("motivo_resumo"),
            output_field=models.CharField(),
        ),
    )
    Workflow.objects.filter(modo_replicacao="qtd").update(
        quantidade_alvo=models.F("protocolos_planejados")
    )
    Workflow.objects.filter(
        modo_replicacao="qtd",
        status_brflow__iexact="SEM_ALTERACAO",
    ).update(quantidade_encontrada=models.F("protocolos_planejados"))


class Migration(migrations.Migration):
    # PostgreSQL nao permite criar os indices abaixo enquanto o backfill ainda
    # possui eventos de trigger pendentes na mesma transacao. Cada operacao
    # precisa ser confirmada antes de a proxima alteracao de schema comecar.
    atomic = False

    dependencies = [("replicacao_d1", "0024_restore_segmento_categoria_hierarchy")]

    operations = [
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="resultado",
            field=models.CharField(
                choices=[
                    ("pendente", "Pendente"),
                    ("processando", "Processando"),
                    ("salvo", "Salvo"),
                    ("sem_alteracao", "Sem alteração"),
                    ("pulado", "Pulado"),
                    ("inativo", "Inativo"),
                    ("nao_salvo", "Não salvo"),
                    ("falhou", "Falhou"),
                    ("cancelado", "Cancelado"),
                ],
                default="pendente",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="severidade",
            field=models.CharField(
                choices=[("info", "Informação"), ("aviso", "Aviso"), ("erro", "Erro")],
                default="info",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="motivo_codigo",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="motivo_resumo",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="fase_execucao",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="quantidade_alvo",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="replicacaod1workflowdia",
            name="quantidade_encontrada",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(_backfill_outcomes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="replicacaod1workflowdia",
            name="resultado",
            field=models.CharField(
                choices=[
                    ("pendente", "Pendente"),
                    ("processando", "Processando"),
                    ("salvo", "Salvo"),
                    ("sem_alteracao", "Sem alteração"),
                    ("pulado", "Pulado"),
                    ("inativo", "Inativo"),
                    ("nao_salvo", "Não salvo"),
                    ("falhou", "Falhou"),
                    ("cancelado", "Cancelado"),
                ],
                db_index=True,
                default="pendente",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="replicacaod1workflowdia",
            name="severidade",
            field=models.CharField(
                choices=[("info", "Informação"), ("aviso", "Aviso"), ("erro", "Erro")],
                db_index=True,
                default="info",
                max_length=12,
            ),
        ),
    ]
