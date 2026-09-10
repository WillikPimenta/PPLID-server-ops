"""API da geração automática de escala mensal."""

from __future__ import annotations

from datetime import date

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.escala_flex.models import (
    EscalaGenerationConflict,
    EscalaGenerationEntry,
    EscalaGenerationRun,
)
from apps.escala_flex.rbac import ESCALAS_GENERATE, ESCALAS_PUBLISH, ESCALAS_VIEW
from apps.escala_flex.services.absence_types import get_active_absence_codes
from apps.escala_flex.services.escala_generation import (
    TARGET_JOB_TITLE,
    default_configuration,
    generate_preview,
    publish_run,
)
from apps.escala_flex.services.escala_generation.eligibility import (
    build_eligible_agents_summary,
    collect_vigent_eligible_activities,
    load_eligible_agent_days,
    normalize_job_title,
)
from apps.workforce.models import HeadcountCatalogItem
from apps.workforce.services.catalog import active_options_by_catalog
from apps.escala_flex.services.escala_generation.publisher import (
    PublishBlockedError,
    revalidate_run,
)
from apps.escala_flex.services.escala_generation.rules import (
    load_holidays_for_month,
    merge_configuration,
)

from .generation_serializers import (
    EscalaGenerationConflictResolveSerializer,
    EscalaGenerationConflictSerializer,
    EscalaGenerationEntryPatchSerializer,
    EscalaGenerationEntrySerializer,
    EscalaGenerationPreviewSerializer,
    EscalaGenerationPublishResultSerializer,
    EscalaGenerationRunDetailSerializer,
    EscalaGenerationRunSerializer,
)


def _available_job_titles() -> list[str]:
    catalog = active_options_by_catalog().get(
        HeadcountCatalogItem.CATALOG_JOB_TITLE, []
    )
    titles = [item["value"] for item in catalog if item.get("value")]
    if TARGET_JOB_TITLE not in titles:
        titles.insert(0, TARGET_JOB_TITLE)
    return titles


def _resolve_target_job_title(raw: str | None) -> str:
    text = (raw or "").strip() or TARGET_JOB_TITLE
    for candidate in _available_job_titles():
        if normalize_job_title(candidate) == normalize_job_title(text):
            return candidate
    return text


def _get_run(run_id) -> EscalaGenerationRun | None:
    return (
        EscalaGenerationRun.objects.filter(pk=run_id)
        .prefetch_related("entries__agent", "entries__leader", "conflicts")
        .first()
    )


@api_view(["GET"])
@permission_classes(ESCALAS_GENERATE)
def generation_config_options_view(request):
    """Opções e defaults para o wizard de geração."""
    year = int(request.query_params.get("year") or timezone.localdate().year)
    month = int(request.query_params.get("month") or timezone.localdate().month)
    target_job_title = _resolve_target_job_title(
        request.query_params.get("target_job_title")
    )
    reference = date(year, month, 1)
    eligible, _warnings = load_eligible_agent_days(
        reference_month=reference,
        target_job_title=target_job_title,
    )
    eligible_agents = build_eligible_agents_summary(
        eligible,
        target_job_title=target_job_title,
    )
    holidays = [
        {
            "date": holiday.date.isoformat(),
            "name": holiday.name,
            "location": holiday.location,
            "holiday_type": holiday.holiday_type,
        }
        for holiday in load_holidays_for_month(reference)
    ]

    activities = collect_vigent_eligible_activities(
        eligible,
        target_job_title=target_job_title,
    )
    return Response(
        {
            "target_job_title": target_job_title,
            "job_titles": _available_job_titles(),
            "default_configuration": default_configuration(),
            "eligible_agents": eligible_agents,
            "eligible_agent_count": len(eligible_agents),
            "holidays": holidays,
            "activities": activities,
            "absence_codes": sorted(get_active_absence_codes()),
            "reference_month": reference.isoformat(),
        }
    )


@api_view(["POST"])
@permission_classes(ESCALAS_GENERATE)
def generation_preview_view(request):
    serializer = EscalaGenerationPreviewSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    month = serializer.validated_data["reference_month"].replace(day=1)
    configuration = merge_configuration(
        serializer.validated_data.get("configuration") or {}
    )
    run = generate_preview(
        reference_month=month,
        configuration=configuration,
        user=request.user,
    )
    detail = (
        EscalaGenerationRun.objects.prefetch_related(
            "entries__agent",
            "entries__leader",
            "entries__job_activity",
            "entries__location",
            "conflicts__agent",
        )
        .get(pk=run.pk)
    )
    return Response(
        EscalaGenerationRunDetailSerializer(detail).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes(ESCALAS_VIEW)
def generation_runs_list_view(request):
    qs = EscalaGenerationRun.objects.all()[:50]
    return Response(EscalaGenerationRunSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes(ESCALAS_VIEW)
def generation_run_detail_view(request, run_id):
    run = (
        EscalaGenerationRun.objects.filter(pk=run_id)
        .prefetch_related(
            "entries__agent",
            "entries__leader",
            "entries__job_activity",
            "entries__location",
            "conflicts__agent",
        )
        .first()
    )
    if not run:
        return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    return Response(EscalaGenerationRunDetailSerializer(run).data)


@api_view(["PATCH"])
@permission_classes(ESCALAS_GENERATE)
def generation_entry_patch_view(request, run_id, entry_id):
    run = EscalaGenerationRun.objects.filter(pk=run_id).first()
    if not run:
        return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    if run.status == EscalaGenerationRun.STATUS_PUBLISHED:
        return Response(
            {"detail": "Execução já publicada."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    entry = EscalaGenerationEntry.objects.filter(pk=entry_id, run=run).first()
    if not entry:
        return Response({"detail": "Item não encontrado."}, status=status.HTTP_404_NOT_FOUND)

    serializer = EscalaGenerationEntryPatchSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    update_fields = ["adjusted_manually", "adjusted_by", "updated_at"]
    for field in ("day_value", "schedule", "observation", "team", "sector"):
        if field in data:
            setattr(entry, field, data[field])
            update_fields.append(field)
    entry.adjusted_manually = True
    entry.adjusted_by = request.user
    entry.source = EscalaGenerationEntry.SOURCE_MANUAL
    update_fields.append("source")
    entry.save(update_fields=update_fields)
    return Response(EscalaGenerationEntrySerializer(entry).data)


@api_view(["POST"])
@permission_classes(ESCALAS_GENERATE)
def generation_revalidate_view(request, run_id):
    run = EscalaGenerationRun.objects.filter(pk=run_id).first()
    if not run:
        return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    run = revalidate_run(run)
    detail = (
        EscalaGenerationRun.objects.prefetch_related(
            "entries__agent",
            "entries__leader",
            "entries__job_activity",
            "entries__location",
            "conflicts__agent",
        )
        .get(pk=run.pk)
    )
    return Response(EscalaGenerationRunDetailSerializer(detail).data)


@api_view(["POST"])
@permission_classes(ESCALAS_PUBLISH)
def generation_publish_view(request, run_id):
    run = EscalaGenerationRun.objects.filter(pk=run_id).first()
    if not run:
        return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    try:
        result = publish_run(run=run, user=request.user)
    except PublishBlockedError as exc:
        return Response(
            {
                "detail": str(exc),
                "conflicts": EscalaGenerationConflictSerializer(
                    exc.conflicts, many=True
                ).data,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(EscalaGenerationPublishResultSerializer(result).data)


@api_view(["POST"])
@permission_classes(ESCALAS_GENERATE)
def generation_cancel_view(request, run_id):
    run = EscalaGenerationRun.objects.filter(pk=run_id).first()
    if not run:
        return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    if run.status == EscalaGenerationRun.STATUS_PUBLISHED:
        return Response(
            {"detail": "Não é possível cancelar uma execução publicada."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    run.status = EscalaGenerationRun.STATUS_CANCELLED
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at"])
    return Response(EscalaGenerationRunSerializer(run).data)


@api_view(["POST"])
@permission_classes(ESCALAS_GENERATE)
def generation_conflict_resolve_view(request, run_id, conflict_id):
    run = EscalaGenerationRun.objects.filter(pk=run_id).first()
    if not run:
        return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
    conflict = EscalaGenerationConflict.objects.filter(pk=conflict_id, run=run).first()
    if not conflict:
        return Response(
            {"detail": "Conflito não encontrado."}, status=status.HTTP_404_NOT_FOUND
        )
    if conflict.severity == EscalaGenerationConflict.SEVERITY_BLOCKING:
        return Response(
            {
                "detail": (
                    "Conflitos impeditivos exigem ajuste na escala; "
                    "apenas avisos podem ser aceitos com justificativa."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    serializer = EscalaGenerationConflictResolveSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    conflict.resolved = True
    conflict.resolution_note = serializer.validated_data["resolution_note"]
    conflict.resolved_by = request.user
    conflict.resolved_at = timezone.now()
    conflict.save(
        update_fields=["resolved", "resolution_note", "resolved_by", "resolved_at"]
    )
    return Response(EscalaGenerationConflictSerializer(conflict).data)
