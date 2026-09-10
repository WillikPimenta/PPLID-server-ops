from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.services.impersonation import (
    build_access_payload,
    build_impersonation_preview,
    can_impersonate_portal,
    clear_impersonation_session,
    get_session_impersonate_lan_id,
    list_impersonation_targets,
    resolve_effective_user,
    resolve_target_user,
    set_impersonation_session,
)
from apps.accounts.serializers import AuthUserSerializer


def _me_payload(request) -> dict:
    real = request.user
    effective = resolve_effective_user(request)
    impersonating = bool(get_session_impersonate_lan_id(request)) and effective.pk != real.pk
    return {
        "authenticated": True,
        "user": AuthUserSerializer(effective).data,
        "access": build_access_payload(effective, inject_impersonate=impersonating),
        "impersonating": impersonating,
        "real_user": AuthUserSerializer(real).data if impersonating else None,
        "impersonate_lan_id": get_session_impersonate_lan_id(request) if impersonating else None,
    }


class ImpersonationTargetsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can_impersonate_portal(request.user):
            return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)
        return Response({"results": list_impersonation_targets()})


class ImpersonationPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can_impersonate_portal(request.user):
            return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)
        lan = str(request.query_params.get("user_lan_id") or "").strip()
        if not lan:
            return Response({"detail": "Informe o usuário."}, status=status.HTTP_400_BAD_REQUEST)
        preview, error = build_impersonation_preview(lan)
        if error:
            return Response({"detail": error}, status=status.HTTP_404_NOT_FOUND)
        return Response(preview)


class ImpersonationStartView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not can_impersonate_portal(request.user):
            return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)

        lan = str(request.data.get("user_lan_id") or request.data.get("username") or "").strip()
        target, error = resolve_target_user(lan)
        if error:
            code = (
                status.HTTP_404_NOT_FOUND
                if "não encontrado" in error.lower() or "sem conta" in error.lower()
                else status.HTTP_400_BAD_REQUEST
            )
            return Response({"detail": error}, status=code)

        if target.pk == request.user.pk:
            return Response(
                {"detail": "Selecione outro usuário para impersonar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        set_impersonation_session(request, lan)
        return Response(_me_payload(request))


class ImpersonationClearView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not get_session_impersonate_lan_id(request) and not can_impersonate_portal(request.user):
            return Response({"detail": "Sem permissão."}, status=status.HTTP_403_FORBIDDEN)
        clear_impersonation_session(request)
        return Response(_me_payload(request))
