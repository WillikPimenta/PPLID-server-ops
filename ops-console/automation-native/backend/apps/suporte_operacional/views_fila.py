from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permission_classes import portal_perm
from apps.access.registry import QUAL_CAPACITACAO_SUPORTE_ASSIGN, QUAL_CAPACITACAO_SUPORTE_VIEW

from .services.fila_online import (
    FilaError,
    assumir_protocolo,
    build_controle_operacoes,
    claim_next_for_agent,
    direcionar_solicitacao,
    get_or_create_presence,
    list_minha_fila,
    priorizar_solicitacao,
    remover_direcionado,
    serialize_presence,
    set_agent_status,
)

AssignPerm = portal_perm(QUAL_CAPACITACAO_SUPORTE_ASSIGN)
ViewPerm = portal_perm(QUAL_CAPACITACAO_SUPORTE_VIEW)


def _fila_error_response(exc: FilaError) -> Response:
    return Response({"detail": exc.message}, status=exc.status_code)


class PresenceMeView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        presence = get_or_create_presence(request.user)
        return Response(serialize_presence(presence))

    def post(self, request):
        status_value = (request.data.get("status") or "").strip().lower()
        try:
            presence = set_agent_status(user=request.user, status=status_value, actor=request.user)
            if presence.status == presence.STATUS_ONLINE:
                claim_next_for_agent(user=request.user, actor=request.user)
        except FilaError as exc:
            return _fila_error_response(exc)
        return Response(serialize_presence(presence))


class MinhaFilaView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        return Response(list_minha_fila(user=request.user))


class ClaimFilaView(APIView):
    permission_classes = [ViewPerm]

    def post(self, request):
        try:
            req = claim_next_for_agent(user=request.user, actor=request.user)
        except FilaError as exc:
            return _fila_error_response(exc)
        payload = list_minha_fila(user=request.user)
        payload["claimed_id"] = str(req.pk) if req else None
        return Response(payload)


class AssumirProtocoloView(APIView):
    permission_classes = [ViewPerm]

    def post(self, request, request_id):
        try:
            assumir_protocolo(user=request.user, request_id=request_id, actor=request.user)
        except FilaError as exc:
            return _fila_error_response(exc)
        return Response(list_minha_fila(user=request.user))


class DirecionarView(APIView):
    permission_classes = [AssignPerm]

    def post(self, request):
        agent_id = request.data.get("agent_id") or request.data.get("assignee_id")
        request_id = request.data.get("request_id")
        justificativa = request.data.get("justificativa") or request.data.get("justification") or ""
        if not agent_id:
            return Response({"detail": "Informe o agente de suporte."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            req = direcionar_solicitacao(
                actor=request.user,
                agent_id=agent_id,
                request_id=request_id,
                justificativa=justificativa,
            )
        except FilaError as exc:
            return _fila_error_response(exc)
        return Response({"id": str(req.pk), "assignee_id": req.assignee_id})


class PriorizarView(APIView):
    permission_classes = [AssignPerm]

    def post(self, request):
        request_id = request.data.get("request_id")
        if not request_id:
            return Response(
                {"detail": "Informe o protocolo da fila geral."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            req = priorizar_solicitacao(actor=request.user, request_id=request_id)
        except FilaError as exc:
            return _fila_error_response(exc)
        return Response({"id": str(req.pk), "queue_priority_at": req.queue_priority_at})


class RemoverDirecionadoView(APIView):
    permission_classes = [ViewPerm]

    def post(self, request, request_id):
        try:
            result = remover_direcionado(user=request.user, request_id=request_id)
        except FilaError as exc:
            return _fila_error_response(exc)
        return Response(result)


class ControleOperacoesView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        return Response(build_controle_operacoes())
