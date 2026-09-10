"""APIs de configuração RBAC."""

from __future__ import annotations

from django.db import transaction
from django.utils.text import slugify
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.constants import LEGACY_ROLES, role_group_name
from apps.access.models import PortalMenuOption, PortalRoleDefinition, PortalRoutePolicy
from apps.access.permissions import HasPortalPermission
from apps.access.portal_route_registry import SYSTEM_ROUTE_NAMES
from apps.access.registry import ADM_CONFIG_HUB, PORTAL_SECAO_VIEW
from apps.access.serializers import (
    PortalRoleDefinitionCreateSerializer,
    PortalRoleDefinitionUpdateSerializer,
    PortalMenuOptionWriteSerializer,
    PortalRoutePolicyWriteSerializer,
)
from apps.access.services.rbac_admin import (
    ensure_role_group,
    is_builtin_role,
    is_protected_role,
    is_system_route,
    rename_role_definition,
    suggest_route_name,
    users_with_role_count,
    validate_area,
)
from apps.access.services.role_definitions import (
    build_permissions_catalog as build_role_def_permissions_catalog,
    build_routes_catalog,
    serialize_role_definition,
)
from apps.access.services.route_policies import (
    build_permissions_catalog,
    build_role_permissions_catalog,
    build_roles_catalog,
    can_toggle_route_active,
    serialize_route_policy,
)
from apps.access.services.menu_options import serialize_menu_option


class PortalRoutePolicyViewSet(viewsets.ViewSet):
    """Políticas de acesso por rota do frontend."""

    portal_permission = ADM_CONFIG_HUB

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasPortalPermission()]

    def list(self, request):
        policies = PortalRoutePolicy.objects.all().order_by("section", "path")
        data = [serialize_route_policy(policy) for policy in policies]
        return Response(data)

    def retrieve(self, request, pk=None):
        policy = self._get_policy(pk)
        return Response(serialize_route_policy(policy))

    def create(self, request):
        serializer = PortalRoutePolicyWriteSerializer(
            data=request.data,
            context={"creating": True},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        route_name = data.get("route_name") or suggest_route_name(data["path"])
        if PortalRoutePolicy.objects.filter(route_name=route_name).exists():
            return Response(
                {"route_name": f"Já existe política para '{route_name}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_system_route(route_name):
            return Response(
                {"detail": "Rotas de sistema não podem ser criadas pela UI."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        policy = PortalRoutePolicy.objects.create(
            route_name=route_name,
            path=data["path"],
            label=data["label"],
            section=(data.get("section") or "").strip(),
            permissions_any=list(data.get("permissions_any") or []),
            permissions_all=list(data.get("permissions_all") or []),
            requires_auth=bool(data.get("requires_auth", True)),
            is_active=bool(data.get("is_active", True)),
            is_editable=True,
            updated_by=request.user,
        )
        return Response(serialize_route_policy(policy), status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        policy = self._get_policy(pk)
        if is_system_route(policy.route_name) or not policy.is_editable:
            return Response(
                {"detail": "Esta rota não pode ser editada pela UI."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = PortalRoutePolicyWriteSerializer(
            data=request.data,
            partial=True,
            context={"creating": False},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "is_active" in data and not can_toggle_route_active(policy.route_name):
            return Response(
                {"detail": "Esta rota precisa permanecer ativa para administrar o portal."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        update_fields = ["updated_at", "updated_by"]
        if "path" in data:
            policy.path = data["path"]
            update_fields.append("path")
        if "label" in data:
            policy.label = data["label"]
            update_fields.append("label")
        if "section" in data:
            policy.section = (data["section"] or "").strip()
            update_fields.append("section")
        if "permissions_any" in data:
            policy.permissions_any = list(data["permissions_any"] or [])
            update_fields.append("permissions_any")
        if "permissions_all" in data:
            policy.permissions_all = list(data["permissions_all"] or [])
            update_fields.append("permissions_all")
        if "requires_auth" in data:
            policy.requires_auth = bool(data["requires_auth"])
            update_fields.append("requires_auth")
        if "is_active" in data:
            policy.is_active = bool(data["is_active"])
            update_fields.append("is_active")
        policy.updated_by = request.user
        policy.save(update_fields=update_fields)
        return Response(serialize_route_policy(policy))

    def destroy(self, request, pk=None):
        policy = self._get_policy(pk)
        if is_system_route(policy.route_name) or not policy.is_editable:
            return Response(
                {"detail": "Esta rota não pode ser excluída pela UI."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        route_name = policy.route_name
        linked_menus = [
            option.label
            for option in PortalMenuOption.objects.all().only("label", "route_names")
            if route_name in (option.route_names or [])
        ]
        if linked_menus:
            return Response(
                {
                    "detail": (
                        "Remova a rota dos menus antes de excluí-la: "
                        + ", ".join(linked_menus)
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        policy.delete()
        return Response({"detail": f"Rota '{route_name}' excluída."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="catalog")
    def catalog(self, request):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        from apps.access.resolve import user_has_permission

        if not user_has_permission(request.user, ADM_CONFIG_HUB):
            return Response(status=status.HTTP_403_FORBIDDEN)
        return Response(
            {
                "permissions": build_permissions_catalog(),
                "roles": build_roles_catalog(),
                "role_permissions": build_role_permissions_catalog(),
            }
        )

    def _get_policy(self, route_name: str) -> PortalRoutePolicy:
        try:
            return PortalRoutePolicy.objects.get(route_name=route_name)
        except PortalRoutePolicy.DoesNotExist as exc:
            raise NotFound(f"Rota '{route_name}' não encontrada.") from exc


class PortalMenuOptionViewSet(viewsets.ViewSet):
    """Opções exibidas nos menus e cards, vinculadas às rotas RBAC."""

    portal_permission = ADM_CONFIG_HUB

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasPortalPermission()]

    def list(self, request):
        rows = PortalMenuOption.objects.all().order_by("section", "label", "menu_key")
        return Response([serialize_menu_option(row) for row in rows])

    def retrieve(self, request, pk=None):
        return Response(serialize_menu_option(self._get_option(pk)))

    def create(self, request):
        serializer = PortalMenuOptionWriteSerializer(
            data=request.data,
            context={"creating": True},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        menu_key = data.get("menu_key") or slugify(data["label"])
        if not menu_key:
            return Response(
                {"menu_key": "Não foi possível gerar o código do menu."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if PortalMenuOption.objects.filter(menu_key=menu_key).exists():
            return Response(
                {"menu_key": f"Já existe a opção de menu '{menu_key}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        row = PortalMenuOption.objects.create(
            menu_key=menu_key,
            label=data["label"].strip(),
            description=(data.get("description") or "").strip(),
            section=data["section"],
            parent_menu_key=(data.get("parent_menu_key") or "").strip(),
            sort_order=int(data.get("sort_order") or 0),
            route_names=list(data.get("route_names") or []),
            is_active=bool(data.get("is_active", True)),
            is_builtin=bool(data.get("is_builtin", False)),
            is_editable=True,
            updated_by=request.user,
        )
        return Response(serialize_menu_option(row), status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        row = self._get_option(pk)
        if not row.is_editable:
            return Response(
                {"detail": "Esta opção de menu não pode ser editada."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = PortalMenuOptionWriteSerializer(
            data=request.data,
            partial=True,
            context={"creating": False, "instance": row},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        update_fields = ["updated_at", "updated_by"]
        for field in (
            "label",
            "description",
            "section",
            "parent_menu_key",
            "sort_order",
            "route_names",
            "is_active",
        ):
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, str):
                value = value.strip()
            setattr(row, field, value)
            update_fields.append(field)
        row.updated_by = request.user
        row.save(update_fields=update_fields)
        return Response(serialize_menu_option(row))

    def destroy(self, request, pk=None):
        row = self._get_option(pk)
        if row.is_builtin or not row.is_editable:
            return Response(
                {"detail": "Menus padrão não podem ser excluídos; apenas desativados."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        menu_key = row.menu_key
        row.delete()
        return Response(
            {"detail": f"Menu '{menu_key}' excluído."},
            status=status.HTTP_200_OK,
        )

    def _get_option(self, menu_key: str) -> PortalMenuOption:
        try:
            return PortalMenuOption.objects.get(menu_key=menu_key)
        except PortalMenuOption.DoesNotExist as exc:
            raise NotFound(f"Menu '{menu_key}' não encontrado.") from exc


class PortalRoleDefinitionViewSet(viewsets.ViewSet):
    """Definições de perfil RBAC (permissões + escopo)."""

    portal_permission = ADM_CONFIG_HUB

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasPortalPermission()]

    def list(self, request):
        rows = (
            PortalRoleDefinition.objects.exclude(role__in=LEGACY_ROLES)
            .order_by("area", "label", "role")
        )
        return Response([serialize_role_definition(row) for row in rows])

    def retrieve(self, request, pk=None):
        row = self._get_role(pk)
        return Response(serialize_role_definition(row))

    def create(self, request):
        serializer = PortalRoleDefinitionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = data["role"]
        if PortalRoleDefinition.objects.filter(role=role).exists():
            return Response(
                {"role": f"Já existe perfil '{role}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_protected_role(role):
            return Response(
                {"role": "Este código de perfil é reservado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        permissions = list(data.get("permissions") or [])
        if not permissions:
            permissions = [PORTAL_SECAO_VIEW]

        with transaction.atomic():
            row = PortalRoleDefinition.objects.create(
                role=role,
                label=data["label"].strip(),
                area=validate_area(data.get("area")),
                permissions=permissions,
                default_scope=data.get("default_scope") or "global",
                granted_routes=data.get("granted_routes"),
                is_editable=True,
                updated_by=request.user,
            )
            ensure_role_group(role)

        return Response(serialize_role_definition(row), status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        row = self._get_role(pk)
        if not row.is_editable or is_protected_role(row.role):
            return Response(
                {"detail": "Este perfil não pode ser editado pela UI."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = PortalRoleDefinitionUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "role" in data and data["role"] != row.role:
            try:
                row = rename_role_definition(row.role, data["role"], actor=request.user)
            except ValueError as exc:
                return Response({"role": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except PortalRoleDefinition.DoesNotExist:
                raise NotFound(f"Perfil '{pk}' não encontrado.") from None

        update_fields = ["updated_at", "updated_by"]
        if "label" in data:
            row.label = data["label"].strip()
            update_fields.append("label")
        if "area" in data:
            row.area = validate_area(data["area"])
            update_fields.append("area")
        if "permissions" in data:
            row.permissions = data["permissions"]
            update_fields.append("permissions")
        if "default_scope" in data:
            row.default_scope = data["default_scope"]
            update_fields.append("default_scope")
        if "granted_routes" in data:
            row.granted_routes = data["granted_routes"]
            update_fields.append("granted_routes")
        row.updated_by = request.user
        row.save(update_fields=update_fields)
        return Response(serialize_role_definition(row))

    def destroy(self, request, pk=None):
        row = self._get_role(pk)
        if is_builtin_role(row.role) or is_protected_role(row.role):
            return Response(
                {
                    "detail": (
                        "Perfis do catálogo padrão não podem ser excluídos. "
                        "Remova permissões ou desative atribuições nos usuários."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        assigned = users_with_role_count(row.role)
        if assigned:
            return Response(
                {
                    "detail": (
                        f"Não é possível excluir: {assigned} usuário(s) ainda usam este perfil. "
                        "Remova o perfil dos usuários antes."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        role = row.role
        with transaction.atomic():
            row.delete()
            from django.contrib.auth.models import Group

            Group.objects.filter(name=role_group_name(role)).delete()

        return Response({"detail": f"Perfil '{role}' excluído."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="catalog")
    def catalog(self, request):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        from apps.access.resolve import user_has_permission

        if not user_has_permission(request.user, ADM_CONFIG_HUB):
            return Response(status=status.HTTP_403_FORBIDDEN)
        return Response(
            {
                "permissions": build_role_def_permissions_catalog(),
                "routes": build_routes_catalog(),
                "areas": [
                    {"id": "planejamento", "label": "Planejamento"},
                    {"id": "operacao", "label": "Operação"},
                    {"id": "processos", "label": "Processos"},
                    {"id": "qualidade", "label": "Qualidade"},
                    {"id": "administracao", "label": "Administração"},
                    {"id": "outros", "label": "Outros"},
                ],
            }
        )

    def _get_role(self, role: str) -> PortalRoleDefinition:
        try:
            return PortalRoleDefinition.objects.get(role=role)
        except PortalRoleDefinition.DoesNotExist as exc:
            raise NotFound(f"Perfil '{role}' não encontrado.") from exc
