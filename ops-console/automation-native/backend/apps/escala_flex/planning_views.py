from io import BytesIO



from django.conf import settings

from rest_framework import status

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from config.api_exceptions import secure_error_payload

from apps.escala_flex.rbac import ESCALAS_IMPORT, ESCALAS_VIEW



from .escala_views import build_escala_queryset, paginate_escala_response

from .models import EscalaImportBatch

from .planning_serializers import (

    EscalaImportBatchSerializer,

    EscalaImportResultSerializer,

    EscalaImportStartedSerializer,

)

from .services.escala_column_filters import build_escala_column_filter_options

from .services.escala_excel_importer import (

    can_access_planning,

    import_escala_excel,

    import_in_progress,

    schedule_import_job,

)

from .services.permissions import build_operational_profile





def _deny_if_no_planning_access(request):

    if not can_access_planning(request.user):

        return Response(

            {"detail": "Sem permissão para importar escalas."},

            status=status.HTTP_403_FORBIDDEN,

        )

    return None





def _serialize_import_result(result) -> dict:

    serializer = EscalaImportResultSerializer(

        {

            "batch_id": result.batch_id,

            "sheets_processed": result.sheets_processed,

            "rows_upserted": result.rows_upserted,

            "schedules_synced": result.schedules_synced,

            "dates_rebuilt": result.dates_rebuilt,

            "errors": result.errors,

            "warnings": result.warnings,

        }

    )

    return serializer.data





@api_view(["POST"])

@permission_classes(ESCALAS_IMPORT)

def escala_import_view(request):

    denied = _deny_if_no_planning_access(request)

    if denied:

        return denied



    upload = request.FILES.get("file")

    if not upload:

        return Response(

            {"detail": "Arquivo Excel (.xlsx) é obrigatório."},

            status=status.HTTP_400_BAD_REQUEST,

        )



    name = upload.name or "upload.xlsx"

    if not name.lower().endswith((".xlsx", ".xls")):

        return Response(

            {"detail": "Formato inválido. Envie um arquivo .xlsx."},

            status=status.HTTP_400_BAD_REQUEST,

        )



    if import_in_progress():

        return Response(

            {"detail": "Já existe uma importação em andamento. Aguarde a conclusão."},

            status=status.HTTP_409_CONFLICT,

        )



    content = upload.read()



    if settings.ESCALA_IMPORT_ASYNC:

        batch = EscalaImportBatch.objects.create(

            filename=name,

            uploaded_by=request.user,

            status=EscalaImportBatch.STATUS_PROCESSING,

        )

        user_id = request.user.pk if request.user.is_authenticated else None

        schedule_import_job(str(batch.id), content, name, user_id)

        serializer = EscalaImportStartedSerializer(

            {"batch_id": batch.id, "status": EscalaImportBatch.STATUS_PROCESSING}

        )

        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)



    try:

        result = import_escala_excel(BytesIO(content), filename=name, user=request.user)

    except Exception as exc:

        return Response(

            secure_error_payload(exc, operation="escala_flex.import"),

            status=status.HTTP_500_INTERNAL_SERVER_ERROR,

        )



    if not result.sheets_processed:

        return Response(

            {

                "detail": "Nenhuma aba mensal válida encontrada no arquivo.",

                "errors": result.errors,

            },

            status=status.HTTP_400_BAD_REQUEST,

        )



    return Response(_serialize_import_result(result), status=status.HTTP_201_CREATED)





@api_view(["GET"])

@permission_classes(ESCALAS_VIEW)

def escala_import_detail_view(request, batch_id):

    denied = _deny_if_no_planning_access(request)

    if denied:

        return denied



    try:

        batch = EscalaImportBatch.objects.select_related("uploaded_by").get(pk=batch_id)

    except EscalaImportBatch.DoesNotExist:

        return Response({"detail": "Importação não encontrada."}, status=status.HTTP_404_NOT_FOUND)



    serializer = EscalaImportBatchSerializer(batch)

    return Response(serializer.data)





@api_view(["GET"])

@permission_classes(ESCALAS_VIEW)

def escala_imports_list_view(request):

    denied = _deny_if_no_planning_access(request)

    if denied:

        return denied



    qs = EscalaImportBatch.objects.select_related("uploaded_by").order_by("-created_at")[:20]

    serializer = EscalaImportBatchSerializer(qs, many=True)

    return Response(serializer.data)





@api_view(["GET"])

@permission_classes(ESCALAS_VIEW)

def escala_list_view(request):

    """Alias legado para Planejamento — sem escopo restritivo de perfil."""

    denied = _deny_if_no_planning_access(request)

    if denied:

        return denied



    profile = build_operational_profile(request.user)

    try:

        qs = build_escala_queryset(

            request,

            profile,

            apply_profile_scope=False,

        )

    except ValueError as exc:

        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)



    return paginate_escala_response(request, qs)


@api_view(["GET"])
@permission_classes(ESCALAS_VIEW)
def escala_planning_filter_options_view(request):
    """Opções de coluna para /planejamento/escalas/consulta (sem escopo de perfil)."""
    denied = _deny_if_no_planning_access(request)
    if denied:
        return denied

    from .escala_views import _apply_period_filters, _base_escala_queryset

    profile = build_operational_profile(request.user)
    try:
        base_qs = _base_escala_queryset(
            profile,
            apply_profile_scope=False,
            user=request.user,
        )
        base_qs = _apply_period_filters(base_qs, request)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    columns = build_escala_column_filter_options(base_qs, include_horario=False)
    return Response({"columns": columns})

