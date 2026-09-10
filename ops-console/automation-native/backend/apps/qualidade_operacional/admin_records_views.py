from django.core.exceptions import ObjectDoesNotExist, ValidationError
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permission_classes import portal_perm
from apps.access.registry import ADM_QUALIDADE_REGISTROS_MANAGE, ADM_QUALIDADE_REGISTROS_VIEW
from apps.access.resolve import user_has_permission
from apps.qualidade_operacional.services.record_admin import (
    build_preview,
    execute_action,
    model_for_kind,
    record_detail,
    search_records,
    serialize_history,
    summary,
)
from apps.qualidade_operacional.throttling import QualidadeOperacionalListThrottle

ManagePerm = portal_perm(ADM_QUALIDADE_REGISTROS_MANAGE)


class ViewOrManagePerm(BasePermission):
    def has_permission(self, request, view):
        return user_has_permission(
            request.user, ADM_QUALIDADE_REGISTROS_VIEW
        ) or user_has_permission(request.user, ADM_QUALIDADE_REGISTROS_MANAGE)


def _error_response(exc: ValidationError) -> Response:
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", None) or [str(exc)]
    conflict = isinstance(detail, dict) and any(
        key in detail for key in {"expected_revision", "preview_token"}
    )
    return Response(
        {"errors": detail},
        status=status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST,
    )


class QualidadeAdminRegistroListView(APIView):
    permission_classes = [ViewOrManagePerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request):
        try:
            return Response(search_records(request.query_params))
        except ValidationError as exc:
            return _error_response(exc)


class QualidadeAdminRegistroDetailView(APIView):
    permission_classes = [ViewOrManagePerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def get(self, request, kind: str, pk: int):
        try:
            instance = model_for_kind(kind).all_objects.get(pk=pk)
        except ValidationError as exc:
            return _error_response(exc)
        except ObjectDoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(
            record_detail(
                instance,
                kind,
                can_manage=user_has_permission(request.user, ADM_QUALIDADE_REGISTROS_MANAGE),
            )
        )


class QualidadeAdminRegistroPreviewView(APIView):
    permission_classes = [ManagePerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def post(self, request, kind: str, pk: int):
        try:
            instance = model_for_kind(kind).all_objects.get(pk=pk)
            return Response(build_preview(instance, kind, request.data if isinstance(request.data, dict) else {}))
        except ObjectDoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except ValidationError as exc:
            return _error_response(exc)


class QualidadeAdminRegistroActionView(APIView):
    permission_classes = [ManagePerm]
    throttle_classes = [QualidadeOperacionalListThrottle]

    def post(self, request, kind: str, pk: int):
        try:
            instance = model_for_kind(kind).all_objects.get(pk=pk)
            instance, history, created = execute_action(
                instance,
                kind,
                request.data if isinstance(request.data, dict) else {},
                request.user,
            )
        except ObjectDoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except ValidationError as exc:
            return _error_response(exc)
        return Response(
            {
                "record": summary(instance, kind),
                "history_entry": serialize_history(history),
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
