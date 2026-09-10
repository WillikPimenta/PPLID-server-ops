from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.permissions import HasPortalPermission
from apps.access.registry import ADM_USERS_MANAGE
from apps.accounts.serializers import (
    PortalUserBulkActiveSerializer,
    PortalUserBulkReplicateRbacSerializer,
    PortalUserBulkResetSerializer,
    PortalUserBulkRolesSerializer,
    PortalUserCreateSerializer,
    PortalUserListSerializer,
    PortalUserUpdateSerializer,
    UserChangeHistoricoSerializer,
)
from apps.accounts.services.portal_user_admin import (
    bulk_replicate_rbac,
    bulk_reset_password,
    bulk_set_active,
    bulk_set_roles,
    create_external_user,
    get_user_historico,
    list_portal_users,
    reset_password,
    update_user,
)

User = get_user_model()


class PortalUserViewSet(viewsets.ViewSet):
    """Gestão administrativa de contas do portal."""

    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_USERS_MANAGE

    def list(self, request):
        q = request.query_params.get("q", "")
        is_active_param = request.query_params.get("is_active")
        role = request.query_params.get("role", "")
        has_agent_param = request.query_params.get("has_agent")

        is_active = None
        if is_active_param in {"true", "false"}:
            is_active = is_active_param == "true"

        has_agent = None
        if has_agent_param in {"true", "false"}:
            has_agent = has_agent_param == "true"

        users = list_portal_users(q=q, is_active=is_active, role=role, has_agent=has_agent)
        serializer = PortalUserListSerializer(users, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        user = self._get_user(pk)
        from apps.accounts.services.portal_user_admin import serialize_portal_user

        data = serialize_portal_user(user)
        return Response(PortalUserListSerializer(data).data)

    def create(self, request):
        serializer = PortalUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = create_external_user(actor=request.user, **serializer.validated_data)
        return Response(PortalUserListSerializer(data).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        user = self._get_user(pk)
        serializer = PortalUserUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = update_user(actor=request.user, user=user, **serializer.validated_data)
        return Response(PortalUserListSerializer(data).data)

    @action(detail=False, methods=["post"], url_path="bulk-active")
    def bulk_active(self, request):
        serializer = PortalUserBulkActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = bulk_set_active(
            actor=request.user,
            user_ids=serializer.validated_data["user_ids"],
            is_active=serializer.validated_data["is_active"],
        )
        return Response(result)

    @action(detail=False, methods=["post"], url_path="bulk-reset-password")
    def bulk_reset_password(self, request):
        serializer = PortalUserBulkResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = bulk_reset_password(
            actor=request.user,
            user_ids=serializer.validated_data["user_ids"],
        )
        return Response(result)

    @action(detail=False, methods=["post"], url_path="bulk-replicate-rbac")
    def bulk_replicate_rbac(self, request):
        serializer = PortalUserBulkReplicateRbacSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = bulk_replicate_rbac(
            actor=request.user,
            source_user_id=serializer.validated_data["source_user_id"],
            target_user_ids=serializer.validated_data["target_user_ids"],
        )
        return Response(result)

    @action(detail=False, methods=["post"], url_path="bulk-roles")
    def bulk_roles(self, request):
        serializer = PortalUserBulkRolesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = bulk_set_roles(
            actor=request.user,
            user_ids=serializer.validated_data["user_ids"],
            operation=serializer.validated_data["operation"],
            roles=serializer.validated_data["roles"],
        )
        return Response(result)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password_action(self, request, pk=None):
        user = self._get_user(pk)
        data = reset_password(user, actor=request.user)
        return Response(PortalUserListSerializer(data).data)

    @action(detail=True, methods=["get"])
    def historico(self, request, pk=None):
        user = self._get_user(pk)
        entries = get_user_historico(user)
        return Response(UserChangeHistoricoSerializer(entries, many=True).data)

    def _get_user(self, pk):
        return User.objects.get(pk=pk)
