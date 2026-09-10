from __future__ import annotations

import json

from datetime import datetime, time
from uuid import UUID

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permissions import HasPortalPermission
from apps.access.resolve import user_has_permission
from apps.access.registry import QUAL_AUDITORIA_ASSIGN, QUAL_AUDITORIA_CREATE, QUAL_AUDITORIA_VIEW
from apps.auditoria.models import QualidadePendenteReinspecao
from apps.auditoria.services.reinspecao_atividade_import import list_reinspecao_falhas
from apps.auditoria.services.reinspecao_fila import (
    build_controle_operacoes,
    claim_next_for_auditor,
    concluir_analise,
    direcionar_protocolo,
    distribuir_protocolos,
    get_or_create_presence,
    iniciar_analise,
    list_auditor_fila,
    list_auditores_controle,
    list_historico,
    list_minha_fila,
    redistribuir_fila_auditor,
    remover_protocolo_direcionado,
    remover_protocolo_fila_controle,
    reinspecao_falhas_qs,
    reset_fila_contexto,
    serialize_fila_item,
    serialize_pendente_reinspecao,
    serialize_presence,
    set_auditor_status_and_maybe_distribute,
    set_fila_contexto,
)
from apps.auditoria.services.serialization import serialize_falha_cadastro

User = get_user_model()


class ReinspecaoFilaContextMixin:
    """Isola Reinspeção e Auditoria Compliance no mesmo motor de fila."""

    def dispatch(self, request, *args, **kwargs):
        requested = (request.GET.get("contexto") or "reinspecao").strip().lower()
        if requested != "reinspecao":
            return JsonResponse(
                {"detail": "Esta rota atende somente a fila de Reinspeção."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        token = set_fila_contexto("reinspecao")
        try:
            return super().dispatch(request, *args, **kwargs)
        finally:
            reset_fila_contexto(token)


def _parse_dt(raw: str | None):
    if not raw:
        return None
    text = str(raw).strip()
    value = parse_datetime(text)
    if value is None:
        day = parse_date(text)
        if day is not None:
            value = datetime.combine(day, time.min)
    if value and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _parse_uuid(raw) -> UUID | None:
    try:
        return UUID(str(raw))
    except (TypeError, ValueError, AttributeError):
        return None


class AuditoriaReinspecaoFalhasView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        auditor_id = _parse_uuid(request.query_params.get("auditor_id"))
        page_raw = (request.query_params.get("page") or "1").strip()
        page_size_raw = (request.query_params.get("page_size") or "50").strip()
        page = int(page_raw) if page_raw.isdigit() else 1
        page_size = int(page_size_raw) if page_size_raw.isdigit() else 50
        column_filters = {}
        raw_filters = (request.query_params.get("column_filters") or "").strip()
        if raw_filters:
            try:
                parsed = json.loads(raw_filters)
                if isinstance(parsed, dict):
                    column_filters = parsed
            except json.JSONDecodeError:
                column_filters = {}
        return Response(
            list_reinspecao_falhas(
                protocolo=(request.query_params.get("protocolo") or "").strip(),
                auditor_id=auditor_id,
                atribuicao=(request.query_params.get("atribuicao") or "").strip(),
                analise_status=(request.query_params.get("analise_status") or "").strip(),
                andamento=(request.query_params.get("andamento") or "").strip(),
                page=page,
                page_size=page_size,
                column_filters=column_filters,
                sort_key=(request.query_params.get("sort_key") or "").strip(),
                sort_dir=(request.query_params.get("sort_dir") or "asc").strip(),
                filter_column=(request.query_params.get("filter_column") or "").strip(),
            )
        )


class AuditoriaReinspecaoFalhasBulkDeleteView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request):
        from apps.auditoria.services.reinspecao_atividade_import import bulk_delete_reinspecao_falhas

        payload = request.data if isinstance(request.data, dict) else {}
        raw_ids = payload.get("ids")
        if not isinstance(raw_ids, list):
            return Response(
                {"detail": "Informe a lista de IDs em 'ids'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ids: list[int] = []
        for value in raw_ids:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        if not ids:
            return Response(
                {"detail": "Nenhum ID válido para exclusão."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(ids) > 500:
            return Response(
                {"detail": "Máximo de 500 registros por exclusão."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = bulk_delete_reinspecao_falhas(ids=ids)
        return Response(result, status=status.HTTP_200_OK)


class AuditoriaReinspecaoFalhaDetailView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method in ("PATCH", "DELETE"):
            self.portal_permission = QUAL_AUDITORIA_CREATE
        else:
            self.portal_permission = QUAL_AUDITORIA_VIEW
        super().initial(request, *args, **kwargs)

    def _get_falha(self, pk: int) -> QualidadePendenteReinspecao | None:
        return (
            reinspecao_falhas_qs().filter(pk=pk)
            .select_related("responsavel", "created_by")
            .first()
        )

    def get(self, request, pk: int):
        falha = self._get_falha(pk)
        if not falha:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_fila_item(falha))

    def patch(self, request, pk: int):
        from apps.auditoria.services.reinspecao_atividade_import import (
            is_falha_realizada,
            update_reinspecao_falha,
        )

        falha = self._get_falha(pk)
        if not falha:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if is_falha_realizada(falha):
            return Response(
                {"detail": "Protocolo já respondido — alteração não permitida."},
                status=status.HTTP_409_CONFLICT,
            )
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            updated = update_reinspecao_falha(falha, payload)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(serialize_pendente_reinspecao(updated))

    def delete(self, request, pk: int):
        from apps.auditoria.services.reinspecao_atividade_import import is_falha_realizada

        falha = self._get_falha(pk)
        if not falha:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if is_falha_realizada(falha):
            return Response(
                {"detail": "Protocolo já respondido — exclusão não permitida."},
                status=status.HTTP_409_CONFLICT,
            )
        falha.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReinspecaoPresenceMeView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        presence = get_or_create_presence(request.user)
        return Response(serialize_presence(presence))

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            result = set_auditor_status_and_maybe_distribute(
                user=request.user,
                status=str(payload.get("status") or ""),
                actor=request.user,
            )
        except ValueError as exc:
            return Response({"errors": {"status": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ReinspecaoPresenceAdminView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_ASSIGN

    def post(self, request, user_id: UUID):
        target = User.objects.filter(pk=user_id, is_active=True).first()
        if not target:
            return Response({"errors": {"user_id": "Auditor inválido."}}, status=status.HTTP_404_NOT_FOUND)
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            result = set_auditor_status_and_maybe_distribute(
                user=target,
                status=str(payload.get("status") or ""),
                actor=request.user,
            )
        except ValueError as exc:
            return Response({"errors": {"status": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ReinspecaoDistribuirView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_ASSIGN

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        limit_raw = payload.get("limit")
        limit = int(limit_raw) if str(limit_raw or "").isdigit() else None
        result = distribuir_protocolos(actor=request.user, limit=limit)
        return Response(result)


class ReinspecaoDirecionarView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_ASSIGN

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        auditor_id = _parse_uuid(payload.get("auditor_id"))
        if not auditor_id:
            return Response(
                {"errors": {"auditor_id": "Informe o auditor de destino."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        falha_id = payload.get("falha_id")
        try:
            falha = direcionar_protocolo(
                falha_id=int(falha_id) if falha_id not in (None, "") else None,
                protocolo=str(payload.get("protocolo") or ""),
                auditor_id=auditor_id,
                actor=request.user,
                justificativa=str(payload.get("justificativa") or ""),
                reatribuicao=bool(payload.get("reatribuicao")),
            )
        except ValueError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_fila_item(falha))


class ReinspecaoMinhaFilaView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        # Claim automático + lista (1 protocolo). Também usado como poll periódico.
        return Response(list_minha_fila(request.user, auto_claim=True))


class ReinspecaoRemoverDirecionadoView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request, pk: int):
        try:
            result = remover_protocolo_direcionado(falha_id=pk, user=request.user)
        except ValueError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ReinspecaoClaimView(ReinspecaoFilaContextMixin, APIView):
    """Poll leve: tenta atribuir 1 protocolo da fila geral se o auditor estiver Online e livre."""

    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def post(self, request):
        falha = claim_next_for_auditor(user=request.user)
        return Response(
            {
                "claimed": bool(falha),
                "item": serialize_fila_item(falha) if falha else None,
                "fila": list_minha_fila(request.user, auto_claim=False),
            }
        )

class ReinspecaoAuditorFilaView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_ASSIGN

    def get(self, request, user_id: UUID):
        return Response(list_auditor_fila(user_id))


class ReinspecaoRemoverFilaControleView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_ASSIGN

    def post(self, request, user_id: UUID):
        payload = request.data if isinstance(request.data, dict) else {}
        falha_raw = payload.get("falha_id")
        try:
            result = remover_protocolo_fila_controle(
                auditor_id=user_id,
                falha_id=int(falha_raw) if falha_raw not in (None, "") else None,
                protocolo=str(payload.get("protocolo") or ""),
                actor=request.user,
            )
        except ValueError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ReinspecaoIniciarAnaliseView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request, pk: int):
        allow = user_has_permission(request.user, QUAL_AUDITORIA_ASSIGN)
        try:
            falha = iniciar_analise(falha_id=pk, user=request.user, allow_any=allow)
        except PermissionError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_fila_item(falha))


class ReinspecaoConcluirAnaliseView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request, pk: int):
        allow = user_has_permission(request.user, QUAL_AUDITORIA_ASSIGN)
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            falha = concluir_analise(
                falha_id=pk,
                user=request.user,
                allow_any=allow,
                cliente=str(payload.get("cliente") or ""),
                status_texto=str(payload.get("status") or ""),
                observacao=str(payload.get("observacao") or payload.get("consideracoes_finais") or ""),
                matricula_agente=str(payload.get("matricula_agente") or payload.get("usuario") or ""),
                matricula_auditor=str(payload.get("matricula_auditor") or payload.get("auditor") or ""),
                etapas=payload.get("etapas") if isinstance(payload.get("etapas"), list) else None,
            )
        except PermissionError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_fila_item(falha))


class ReinspecaoControleAuditoresView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        date_from = _parse_dt(request.query_params.get("date_from"))
        date_to = _parse_dt(request.query_params.get("date_to"))
        return Response(build_controle_operacoes(date_from=date_from, date_to=date_to))


class ReinspecaoRedistribuirFilaView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_ASSIGN

    def post(self, request, user_id: UUID):
        try:
            result = redistribuir_fila_auditor(auditor_id=user_id, actor=request.user)
        except ValueError as exc:
            return Response({"errors": {"detail": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ReinspecaoHistoricoView(ReinspecaoFilaContextMixin, APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        falha_raw = (request.query_params.get("falha_id") or "").strip()
        auditor_id = _parse_uuid(request.query_params.get("auditor_id"))
        limit_raw = (request.query_params.get("limit") or "100").strip()
        return Response(
            {
                "results": list_historico(
                    falha_id=int(falha_raw) if falha_raw.isdigit() else None,
                    auditor_id=auditor_id,
                    limit=int(limit_raw) if limit_raw.isdigit() else 100,
                )
            }
        )
