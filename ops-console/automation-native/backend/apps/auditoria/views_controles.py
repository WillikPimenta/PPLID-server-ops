from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permissions import HasPortalPermission
from apps.access.registry import QUAL_AUDITORIA_CREATE, QUAL_AUDITORIA_VIEW
from apps.auditoria.models import AuditoriaControleRegistro
from apps.auditoria.services.controles import (
    VALID_TIPOS,
    create_controle,
    delete_controle,
    meta_for,
    serialize_controle,
    update_controle,
    validate_controle_payload,
)


def _payload_from_request(request) -> dict:
    data = request.data
    if hasattr(data, "dict"):
        return data.dict()
    if isinstance(data, dict):
        return dict(data)
    return {}


def _selfie_from_request(request):
    return request.FILES.get("selfie") or request.FILES.get("selfie_higienizada")


def _truthy_flag(value) -> bool:
    if value is True:
        return True
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class AuditoriaControleMetaView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request, tipo: str):
        if tipo not in VALID_TIPOS:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(meta_for(tipo))


class AuditoriaControleListView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method == "POST":
            self.portal_permission = QUAL_AUDITORIA_CREATE
        else:
            self.portal_permission = QUAL_AUDITORIA_VIEW
        super().initial(request, *args, **kwargs)

    def get(self, request, tipo: str):
        if tipo not in VALID_TIPOS:
            return Response(status=status.HTTP_404_NOT_FOUND)
        qs = (
            AuditoriaControleRegistro.objects.select_related("created_by")
            .filter(tipo=tipo)
            .order_by("-created_at", "-id")
        )
        situacao = (request.query_params.get("situacao") or "").strip()
        if situacao:
            qs = qs.filter(situacao__iexact=situacao)
        return Response(
            {
                **meta_for(tipo),
                "results": [serialize_controle(item) for item in qs],
            }
        )

    def post(self, request, tipo: str):
        if tipo not in VALID_TIPOS:
            return Response(status=status.HTTP_404_NOT_FOUND)
        payload = _payload_from_request(request)
        selfie = _selfie_from_request(request)
        errors = validate_controle_payload(tipo, payload, has_selfie=bool(selfie))
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        item = create_controle(tipo, payload, request.user, selfie=selfie)
        return Response(serialize_controle(item), status=status.HTTP_201_CREATED)


class AuditoriaControleDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method in ("PATCH", "DELETE"):
            self.portal_permission = QUAL_AUDITORIA_CREATE
        else:
            self.portal_permission = QUAL_AUDITORIA_VIEW
        super().initial(request, *args, **kwargs)

    def _get(self, tipo: str, pk: int) -> AuditoriaControleRegistro | None:
        if tipo not in VALID_TIPOS:
            return None
        return (
            AuditoriaControleRegistro.objects.select_related("created_by")
            .filter(tipo=tipo, pk=pk)
            .first()
        )

    def get(self, request, tipo: str, pk: int):
        item = self._get(tipo, pk)
        if not item:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_controle(item))

    def patch(self, request, tipo: str, pk: int):
        item = self._get(tipo, pk)
        if not item:
            return Response(status=status.HTTP_404_NOT_FOUND)
        payload = _payload_from_request(request)
        selfie = _selfie_from_request(request)
        remove_selfie = _truthy_flag(payload.get("remove_selfie"))
        has_selfie = bool(selfie) or (bool(item.selfie) and not remove_selfie)
        errors = validate_controle_payload(tipo, payload, has_selfie=has_selfie)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        item = update_controle(item, payload, selfie=selfie, remove_selfie=remove_selfie)
        return Response(serialize_controle(item))

    def delete(self, request, tipo: str, pk: int):
        item = self._get(tipo, pk)
        if not item:
            return Response(status=status.HTTP_404_NOT_FOUND)
        delete_controle(item)
        return Response(status=status.HTTP_204_NO_CONTENT)
