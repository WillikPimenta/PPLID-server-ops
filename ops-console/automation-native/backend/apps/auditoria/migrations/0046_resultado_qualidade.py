import unicodedata

from django.db import migrations, models


RESULTADO_COM_FALHA = "com_falha"
RESULTADO_SEM_FALHA = "sem_falha"
RESULTADO_NAO_CLASSIFICADO = "nao_classificado"


def normalizar(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(text.encode("ascii", "ignore").decode("ascii").lower().split())


def inferir_resultado(
    *,
    status,
    tipo_falha,
    origem="",
    tipo_registro="",
    status_falha="",
    brflow_parsed=None,
):
    situacao_payload = (
        brflow_parsed.get("situacao")
        if isinstance(brflow_parsed, dict)
        else ""
    )
    status_normalizado = normalizar(status or situacao_payload)
    tipo_normalizado = normalizar(tipo_falha)
    fluxo = normalizar(origem or tipo_registro)
    estado_falha = normalizar(status_falha)

    if estado_falha == "retirada":
        return RESULTADO_SEM_FALHA
    if estado_falha == "mantida":
        return RESULTADO_COM_FALHA

    if fluxo == "reinspecao":
        if status_normalizado == "procedente":
            return RESULTADO_SEM_FALHA
        if status_normalizado == "improcedente":
            return RESULTADO_COM_FALHA

    if status_normalizado in {"procedente", "nao conforme"}:
        return RESULTADO_COM_FALHA
    if status_normalizado in {"improcedente", "conforme"}:
        return RESULTADO_SEM_FALHA
    if tipo_normalizado == "sem falha":
        return RESULTADO_SEM_FALHA
    if tipo_normalizado and tipo_normalizado not in {
        "auditoria",
        "contestacao",
        "reinspecao",
    }:
        return RESULTADO_COM_FALHA
    return RESULTADO_NAO_CLASSIFICADO


def backfill_resultado_qualidade(apps, schema_editor):
    Tratado = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    alias = schema_editor.connection.alias
    lote = []

    for tratado in Tratado.objects.using(alias).all().iterator(chunk_size=500):
        tratado.resultado_qualidade = inferir_resultado(
            status=tratado.status,
            tipo_falha=tratado.tipo_falha,
            origem=tratado.origem,
            tipo_registro=tratado.tipo_registro,
            status_falha=tratado.status_falha,
            brflow_parsed=tratado.brflow_parsed,
        )
        lote.append(tratado)
        if len(lote) == 500:
            Tratado.objects.using(alias).bulk_update(lote, ["resultado_qualidade"])
            lote = []

    if lote:
        Tratado.objects.using(alias).bulk_update(lote, ["resultado_qualidade"])


class Migration(migrations.Migration):

    replaces = [
        ("auditoria", "0045_resultado_qualidade"),
    ]

    dependencies = [
        ("auditoria", "0045_qualidade_analise_origem"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="resultado_qualidade",
            field=models.CharField(
                choices=[
                    ("com_falha", "Com falha"),
                    ("sem_falha", "Sem falha"),
                    ("nao_classificado", "Não classificado"),
                ],
                db_index=True,
                default="nao_classificado",
                help_text="Resultado consolidado e automático da análise finalizada.",
                max_length=32,
            ),
        ),
        migrations.RunPython(
            backfill_resultado_qualidade,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="auditoriafalhacadastro",
            name="auditoria_tratado_deve_estar_concluido",
        ),
        migrations.RemoveField(
            model_name="auditoriafalhacadastro",
            name="analise_status",
        ),
    ]
