"""API HTTP para importação de agent_history via XLSX SharePoint."""

from __future__ import annotations

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from config.api_exceptions import secure_error_payload

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from apps.workforce.services.agent_history_xlsx import (
    AgentHistoryImportError,
    confirm_session,
    restore_from_backup_path,
    save_upload_and_preflight,
)

ManagePerm = portal_perm(R.PLANEJAMENTO_HEADCOUNT_MANAGE)


class AgentHistoryImportThrottle(UserRateThrottle):
    """Limita uploads/confirmções pesadas por usuário."""

    scope = "agent_history_import"


class AgentHistoryImportPreflightView(APIView):
    permission_classes = [ManagePerm]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [AgentHistoryImportThrottle]

    def post(self, request):
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response(
                {"ok": False, "message": "Selecione um arquivo .xlsx."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            payload = save_upload_and_preflight(
                uploaded,
                original_name=str(uploaded.name or "agent_history.xlsx"),
            )
        except AgentHistoryImportError as exc:
            return Response(
                {"ok": False, "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # noqa: BLE001
            return Response(
                secure_error_payload(
                    exc,
                    operation="workforce.import_preflight",
                    message_field="message",
                    extra={"ok": False},
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(payload, status=status.HTTP_200_OK)


class AgentHistoryImportConfirmView(APIView):
    permission_classes = [ManagePerm]
    throttle_classes = [AgentHistoryImportThrottle]

    def post(self, request):
        import_id = str(request.data.get("import_id") or "").strip()
        if not import_id:
            return Response(
                {"ok": False, "message": "import_id é obrigatório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        allow_partial = str(request.data.get("allow_partial") or "").lower() in {
            "1",
            "true",
            "yes",
            "sim",
        }
        # Default ligado na UI; se a chave não vier, trata como True.
        align_raw = request.data.get("align_agent_active", True)
        align_agent_active = str(align_raw).lower() not in {
            "0",
            "false",
            "no",
            "nao",
            "não",
        }
        try:
            payload = confirm_session(
                import_id,
                allow_partial=allow_partial,
                align_agent_active=align_agent_active,
                performed_by=request.user,
            )
        except AgentHistoryImportError as exc:
            return Response(
                {"ok": False, "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # noqa: BLE001
            return Response(
                secure_error_payload(
                    exc,
                    operation="workforce.import_confirm",
                    message_field="message",
                    extra={"ok": False},
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(payload, status=status.HTTP_200_OK)


class AgentHistoryImportRestoreView(APIView):
    permission_classes = [ManagePerm]
    throttle_classes = [AgentHistoryImportThrottle]

    def post(self, request):
        backup_path = str(request.data.get("backup_path") or "").strip()
        if not backup_path:
            return Response(
                {"ok": False, "message": "backup_path é obrigatório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            payload = restore_from_backup_path(backup_path)
        except AgentHistoryImportError as exc:
            return Response(
                {"ok": False, "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # noqa: BLE001
            return Response(
                secure_error_payload(
                    exc,
                    operation="workforce.import_restore",
                    message_field="message",
                    extra={"ok": False},
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(payload, status=status.HTTP_200_OK)
