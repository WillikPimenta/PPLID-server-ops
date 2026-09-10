# -*- coding: utf-8 -*-
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.falhas_criticas.models import PortalTicket, PortalTicketComment
from apps.falhas_criticas.permissions import IsAuthenticatedPortal
from apps.falhas_criticas.services.jira_ticket import (
    apply_jira_transition,
    create_jira_issue,
    jira_configured,
    jira_issue_url,
    sync_jira_issue,
)
from apps.falhas_criticas.services.user_display import resolve_user_display_name

STATUS_LABELS = dict(PortalTicket.STATUS_CHOICES)


def _serialize_comment(comment: PortalTicketComment) -> dict:
    author_name = resolve_user_display_name(comment.author) if comment.author else "Usuário"
    return {
        "id": comment.id,
        "body": comment.body,
        "author": author_name,
        "author_username": comment.author.username if comment.author else "",
        "created_at": comment.created_at.isoformat(),
    }


def _serialize_ticket(ticket: PortalTicket, detailed: bool = False) -> dict:
    comments_qs = ticket.comments.select_related("author").all()
    payload = {
        "id": ticket.id,
        "title": ticket.title,
        "description": ticket.description,
        "status": ticket.status,
        "status_label": STATUS_LABELS.get(ticket.status, ticket.status),
        "localidade": ticket.localidade,
        "module": ticket.module,
        "jira_key": ticket.jira_key or None,
        "jira_url": jira_issue_url(ticket.jira_key) if ticket.jira_key else None,
        "jira_status_name": ticket.jira_status_name or None,
        "jira_configured": jira_configured(),
        "comments_count": comments_qs.count(),
        "created_by": resolve_user_display_name(ticket.created_by) if ticket.created_by else "",
        "created_by_username": ticket.created_by.username if ticket.created_by else "",
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }
    if detailed:
        payload["comments"] = [_serialize_comment(c) for c in comments_qs]
        payload["jira_transitions"] = []
        if ticket.jira_key and jira_configured():
            sync_data = sync_jira_issue(ticket.jira_key) or {}
            payload["jira_transitions"] = [
                {
                    "id": str(t.get("id", "")),
                    "name": t.get("name", ""),
                    "to_status": (t.get("to") or {}).get("name", ""),
                }
                for t in sync_data.get("transitions", [])
            ]
    return payload


class TicketListCreateView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def get(self, request):
        qs = PortalTicket.objects.filter(created_by=request.user).prefetch_related("comments")
        items = [_serialize_ticket(t) for t in qs]
        return Response({
            "items": items,
            "total": len(items),
            "jira_configured": jira_configured(),
        })

    def post(self, request):
        title = (request.data.get("title") or "").strip()
        if not title:
            return Response({"detail": "Título obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        description = (request.data.get("description") or "").strip()
        localidade = (request.data.get("localidade") or "").strip()
        module = (request.data.get("module") or "").strip()
        ticket = PortalTicket.objects.create(
            title=title,
            description=description,
            localidade=localidade,
            module=module,
            created_by=request.user,
        )
        jira_data = create_jira_issue(title, description, localidade)
        if jira_data:
            ticket.jira_key = jira_data.get("key", "")
            ticket.jira_status_name = (jira_data.get("status") or {}).get("name", "")
            ticket.save(update_fields=["jira_key", "jira_status_name", "updated_at"])
        return Response({
            "message": "Chamado criado com sucesso.",
            "ticket": _serialize_ticket(ticket, detailed=True),
        }, status=status.HTTP_201_CREATED)


class TicketDetailView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def _get_ticket(self, request, ticket_id: int) -> PortalTicket | None:
        return PortalTicket.objects.filter(pk=ticket_id, created_by=request.user).first()

    def get(self, request, ticket_id: int):
        ticket = self._get_ticket(request, ticket_id)
        if not ticket:
            return Response({"detail": "Chamado não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_serialize_ticket(ticket, detailed=True))

    def patch(self, request, ticket_id: int):
        ticket = self._get_ticket(request, ticket_id)
        if not ticket:
            return Response({"detail": "Chamado não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        new_status = request.data.get("status")
        if new_status and new_status in dict(PortalTicket.STATUS_CHOICES):
            ticket.status = new_status
            ticket.save(update_fields=["status", "updated_at"])
        return Response(_serialize_ticket(ticket, detailed=True))


class TicketCommentView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def post(self, request, ticket_id: int):
        ticket = PortalTicket.objects.filter(pk=ticket_id, created_by=request.user).first()
        if not ticket:
            return Response({"detail": "Chamado não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        body = (request.data.get("body") or "").strip()
        if not body:
            return Response({"detail": "Comentário vazio."}, status=status.HTTP_400_BAD_REQUEST)
        PortalTicketComment.objects.create(ticket=ticket, author=request.user, body=body)
        return Response(_serialize_ticket(ticket, detailed=True), status=status.HTTP_201_CREATED)


class TicketSyncView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def post(self, request, ticket_id: int):
        ticket = PortalTicket.objects.filter(pk=ticket_id, created_by=request.user).first()
        if not ticket:
            return Response({"detail": "Chamado não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        if not ticket.jira_key:
            return Response({"detail": "Chamado sem vínculo Jira."}, status=status.HTTP_400_BAD_REQUEST)
        sync_data = sync_jira_issue(ticket.jira_key) or {}
        ticket.jira_status_name = (sync_data.get("status") or {}).get("name", ticket.jira_status_name)
        ticket.save(update_fields=["jira_status_name", "updated_at"])
        payload = _serialize_ticket(ticket, detailed=True)
        payload["message"] = "Status sincronizado com o Jira."
        return Response(payload)


class TicketTransitionView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def post(self, request, ticket_id: int):
        ticket = PortalTicket.objects.filter(pk=ticket_id, created_by=request.user).first()
        if not ticket:
            return Response({"detail": "Chamado não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        transition_id = str(request.data.get("transition_id") or "").strip()
        if not transition_id:
            return Response({"detail": "transition_id obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        if not ticket.jira_key:
            return Response({"detail": "Chamado sem vínculo Jira."}, status=status.HTTP_400_BAD_REQUEST)
        result = apply_jira_transition(ticket.jira_key, transition_id) or {}
        ticket.jira_status_name = (result.get("status") or {}).get("name", ticket.jira_status_name)
        if ticket.jira_status_name and "resolv" in ticket.jira_status_name.lower():
            ticket.status = PortalTicket.STATUS_RESOLVED
        ticket.save(update_fields=["jira_status_name", "status", "updated_at"])
        payload = _serialize_ticket(ticket, detailed=True)
        payload["message"] = result.get("message") or "Transição aplicada."
        return Response(payload)
