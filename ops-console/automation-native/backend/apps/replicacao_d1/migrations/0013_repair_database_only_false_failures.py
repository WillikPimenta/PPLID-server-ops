from django.db import migrations
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def _event_result(event):
    payload = event.payload if event and isinstance(event.payload, dict) else {}
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _aware(value):
    parsed = parse_datetime(str(value or ""))
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def repair_false_failures(apps, schema_editor):
    Run = apps.get_model("replicacao_d1", "ReplicacaoD1Run")
    Event = apps.get_model("replicacao_d1", "ReplicacaoD1ExecutionEvent")

    affected = Run.objects.filter(
        status_canonical="failed",
        erro_codigo="",
        erro_resumo="",
        started_at__isnull=True,
        finished_at__isnull=True,
    )
    for run in affected.iterator():
        close_event = (
            Event.objects.filter(run_id=run.pk, phase="close")
            .order_by("-created_at", "-id")
            .first()
        )
        close_payload = (
            close_event.payload
            if close_event and isinstance(close_event.payload, dict)
            else {}
        )
        if close_event is None or int(close_payload.get("workflows_failed") or 0) != 0:
            continue

        manifest = (
            Event.objects.filter(run_id=run.pk, phase="manifest")
            .order_by("-created_at", "-id")
            .first()
        )
        result = _event_result(manifest)
        started_at = _aware(result.get("started_at"))
        finished_at = _aware(result.get("finished_at")) or close_event.created_at

        run.status_canonical = "partial"
        run.started_at = started_at
        run.finished_at = finished_at
        if started_at and finished_at:
            run.duration_seconds = round((finished_at - started_at).total_seconds(), 2)
        run.save(
            update_fields=[
                "status_canonical",
                "started_at",
                "finished_at",
                "duration_seconds",
                "synced_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("replicacao_d1", "0012_replicacaod1plandeletion")]

    operations = [migrations.RunPython(repair_false_failures, migrations.RunPython.noop)]
