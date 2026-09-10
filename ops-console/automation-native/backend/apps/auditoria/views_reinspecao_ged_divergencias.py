from __future__ import annotations

from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permissions import HasAnyPortalPermission, HasPortalPermission
from apps.access.registry import QUAL_AUDITORIA_ASSIGN, QUAL_AUDITORIA_CREATE, QUAL_AUDITORIA_VIEW
from apps.auditoria.models import ReinspecaoGedDivergencia
from apps.auditoria.services.reinspecao_ged_divergencias import (
    listar_divergencias,
    reconhecer_divergencia,
    resumo_divergencias,
    serialize_divergencia,
)


class ReinspecaoGedDivergenciaResumoView(APIView):
    permission_classes = [IsAuthenticated, HasAnyPortalPermission]
    portal_permissions = (QUAL_AUDITORIA_VIEW, QUAL_AUDITORIA_CREATE, QUAL_AUDITORIA_ASSIGN)

    def get(self, request):
        return Response(resumo_divergencias(contexto="reinspecao"))


class ReinspecaoGedDivergenciaListView(APIView):
    permission_classes = [IsAuthenticated, HasAnyPortalPermission]
    portal_permissions = (QUAL_AUDITORIA_VIEW, QUAL_AUDITORIA_CREATE, QUAL_AUDITORIA_ASSIGN)

    def get(self, request):
        try:
            page = int(request.GET.get("page") or 1)
            page_size = int(request.GET.get("page_size") or 50)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Paginacao invalida."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw_from = (request.GET.get("date_from") or "").strip()
        raw_to = (request.GET.get("date_to") or "").strip()
        date_from = parse_date(raw_from) if raw_from else None
        date_to = parse_date(raw_to) if raw_to else None
        if (raw_from and date_from is None) or (raw_to and date_to is None):
            return Response(
                {"detail": "Periodo invalido. Use AAAA-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            listar_divergencias(
                contexto="reinspecao",
                status=(request.GET.get("status") or "").strip().lower(),
                protocolo=request.GET.get("protocolo") or "",
                date_from=date_from,
                date_to=date_to,
                page=page,
                page_size=page_size,
            )
        )


class ReinspecaoGedDivergenciaReconhecerView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_ASSIGN

    def post(self, request, pk: int):
        observacao = str((request.data or {}).get("observacao") or "").strip()
        if len(observacao) > 2000:
            return Response(
                {"detail": "A observacao deve ter no maximo 2000 caracteres."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            divergencia, changed = reconhecer_divergencia(
                divergencia_id=pk,
                user=request.user,
                observacao=observacao,
            )
        except ReinspecaoGedDivergencia.DoesNotExist:
            return Response(
                {"detail": "Divergencia nao encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"changed": changed, "divergencia": serialize_divergencia(divergencia)})
