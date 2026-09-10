from django.db import migrations, models
from django.db.models import Count


def _normalize(value):
    return " ".join((value or "").strip().casefold().split())


def backfill_source_key(apps, schema_editor):
    Detail = apps.get_model("monitoramento_sla", "SlaUtilDetalhe")
    batch = []
    for row in Detail.objects.only(
        "id", "protocolo", "workflow_nome", "nh_nome"
    ).iterator(chunk_size=2000):
        row.source_key = (
            f"{row.protocolo}|{_normalize(row.workflow_nome)}|"
            f"{_normalize(row.nh_nome)}"
        )
        batch.append(row)
        if len(batch) >= 2000:
            Detail.objects.bulk_update(batch, ["source_key"], batch_size=2000)
            batch = []
    if batch:
        Detail.objects.bulk_update(batch, ["source_key"], batch_size=2000)


def dedupe_source_keys(apps, schema_editor):
    """Mantém a versão mais recente caso o legado tenha chaves nulas duplicadas."""
    Detail = apps.get_model("monitoramento_sla", "SlaUtilDetalhe")
    duplicate_keys = (
        Detail.objects.order_by()
        .values("source_key")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .values_list("source_key", flat=True)
    )
    for source_key in duplicate_keys.iterator(chunk_size=1000):
        keep_id = (
            Detail.objects.filter(source_key=source_key)
            .order_by("-updated_at", "-id")
            .values_list("id", flat=True)
            .first()
        )
        Detail.objects.filter(source_key=source_key).exclude(id=keep_id).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("monitoramento_sla", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="slautilsyncrun",
            name="metrics",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="slautildetalhe",
            name="source_report_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="slautildetalhe",
            name="source_fingerprint",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="slautildetalhe",
            name="source_key",
            field=models.CharField(blank=True, max_length=640, null=True),
        ),
        migrations.RunPython(backfill_source_key, migrations.RunPython.noop),
        migrations.RunPython(dedupe_source_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="slautildetalhe",
            name="source_key",
            field=models.CharField(max_length=640, unique=True),
        ),
        migrations.AddIndex(
            model_name="slautildetalhe",
            index=models.Index(
                fields=["data_cadastro", "protocolo", "id_workflow", "id_nh"],
                name="sla_det_cad_prot_wf_nh",
            ),
        ),
    ]
