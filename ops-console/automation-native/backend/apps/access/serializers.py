"""Serializers para políticas RBAC de rotas e perfis."""

from __future__ import annotations

from rest_framework import serializers

from apps.access.models import PortalRoutePolicy
from apps.access.portal_route_registry import SYSTEM_ROUTE_NAMES
from apps.access.registry import ALL_PERMISSIONS, validate_permissions
from apps.access.services.menu_options import _would_create_menu_cycle
from apps.access.services.rbac_admin import (
    VALID_AREAS,
    normalize_route_path,
    suggest_route_name,
    validate_area,
    validate_role_slug,
)


class PortalRoutePolicySerializer(serializers.ModelSerializer):
    code_defaults = serializers.DictField(read_only=True)
    is_customized = serializers.BooleanField(read_only=True)
    roles_with_access = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_system = serializers.SerializerMethodField()

    class Meta:
        model = PortalRoutePolicy
        fields = [
            "route_name",
            "path",
            "label",
            "section",
            "permissions_any",
            "permissions_all",
            "requires_auth",
            "is_active",
            "is_editable",
            "is_system",
            "code_defaults",
            "is_customized",
            "roles_with_access",
            "updated_at",
        ]
        read_only_fields = [
            "route_name",
            "path",
            "label",
            "section",
            "requires_auth",
            "is_editable",
            "updated_at",
        ]

    def get_is_system(self, obj) -> bool:
        from apps.access.portal_route_registry import SYSTEM_ROUTE_NAMES

        return obj.route_name in SYSTEM_ROUTE_NAMES


class PortalRoutePolicyWriteSerializer(serializers.Serializer):
    path = serializers.CharField(max_length=255, required=False)
    label = serializers.CharField(max_length=255, required=False, allow_blank=False)
    section = serializers.CharField(max_length=64, required=False, allow_blank=True)
    route_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    permissions_any = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
    )
    permissions_all = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
    )
    requires_auth = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)

    def validate_path(self, value):
        return normalize_route_path(value)

    def validate_route_name(self, value):
        if not value:
            return value
        return suggest_route_name("/", explicit=value)

    def validate(self, attrs):
        creating = self.context.get("creating", False)
        if creating and not attrs.get("path"):
            raise serializers.ValidationError({"path": "Informe o caminho da rota."})
        if creating and not attrs.get("label"):
            raise serializers.ValidationError({"label": "Informe o nome/label da rota."})

        any_codes = attrs.get("permissions_any")
        all_codes = attrs.get("permissions_all")
        combined = set(any_codes or []) | set(all_codes or [])
        unknown = combined - ALL_PERMISSIONS
        if unknown:
            raise serializers.ValidationError(
                {"permissions_any": f"Permissões desconhecidas: {sorted(unknown)}"}
            )
        if combined:
            validate_permissions(combined)

        if "path" in attrs and "route_name" not in attrs and creating:
            attrs["route_name"] = suggest_route_name(attrs["path"])
        elif "path" in attrs and attrs.get("route_name"):
            attrs["route_name"] = suggest_route_name(attrs["path"], explicit=attrs["route_name"])
        elif creating and attrs.get("route_name") and "path" in attrs:
            attrs["route_name"] = suggest_route_name(attrs["path"], explicit=attrs.get("route_name"))

        return attrs


# Mantém alias usado pelos testes/views legados
PortalRoutePolicyUpdateSerializer = PortalRoutePolicyWriteSerializer


MENU_SECTIONS = {
    "indicadores",
    "operacao",
    "planejamento",
    "processos",
    "qualidade",
}


class PortalMenuOptionWriteSerializer(serializers.Serializer):
    menu_key = serializers.RegexField(
        regex=r"^[a-z0-9][a-z0-9_-]*$",
        max_length=128,
        required=False,
    )
    label = serializers.CharField(max_length=128, required=False, allow_blank=False)
    description = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )
    section = serializers.ChoiceField(choices=sorted(MENU_SECTIONS), required=False)
    parent_menu_key = serializers.CharField(
        max_length=128,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    sort_order = serializers.IntegerField(required=False, min_value=0)
    route_names = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
    )
    is_active = serializers.BooleanField(required=False)
    is_builtin = serializers.BooleanField(required=False)

    def validate(self, attrs):
        creating = self.context.get("creating", False)
        instance = self.context.get("instance")
        if creating and not attrs.get("label"):
            raise serializers.ValidationError({"label": "Informe o nome do menu."})
        if creating and not attrs.get("section"):
            raise serializers.ValidationError({"section": "Informe a seção do menu."})

        if "parent_menu_key" in attrs:
            attrs["parent_menu_key"] = (attrs.get("parent_menu_key") or "").strip()
        elif instance is not None:
            attrs["parent_menu_key"] = (instance.parent_menu_key or "").strip()

        menu_key = (attrs.get("menu_key") or "").strip()
        if not menu_key and instance is not None:
            menu_key = instance.menu_key
        parent_key = attrs.get("parent_menu_key", "")
        if instance is not None and "parent_menu_key" not in attrs:
            parent_key = (instance.parent_menu_key or "").strip()
        if parent_key:
            if menu_key and parent_key == menu_key:
                raise serializers.ValidationError(
                    {"parent_menu_key": "Um menu não pode ser pai de si mesmo."}
                )
            if menu_key and _would_create_menu_cycle(menu_key, parent_key):
                raise serializers.ValidationError(
                    {"parent_menu_key": "Referência circular entre menus."}
                )

        if "route_names" in attrs:
            route_names = list(dict.fromkeys(attrs["route_names"]))
            if route_names:
                policies = {
                    policy.route_name: policy
                    for policy in PortalRoutePolicy.objects.filter(route_name__in=route_names)
                }
                existing = set(policies)
                missing = [name for name in route_names if name not in existing]
                if missing:
                    raise serializers.ValidationError(
                        {"route_names": f"Rotas não cadastradas: {missing}"}
                    )
                invalid = [
                    name
                    for name in route_names
                    if name in SYSTEM_ROUTE_NAMES
                    or any(
                        segment.startswith(":") and not segment.endswith("?")
                        for segment in policies[name].path.split("/")
                    )
                ]
                if invalid:
                    raise serializers.ValidationError(
                        {
                            "route_names": (
                                "Rotas de sistema ou com parâmetros não podem ser usadas no menu: "
                                f"{invalid}"
                            )
                        }
                    )
            attrs["route_names"] = route_names
        elif creating:
            attrs["route_names"] = []
        return attrs


class PortalRoleDefinitionUpdateSerializer(serializers.Serializer):
    role = serializers.CharField(max_length=64, required=False)
    label = serializers.CharField(max_length=128, required=False, allow_blank=False)
    area = serializers.ChoiceField(choices=sorted(VALID_AREAS), required=False)
    permissions = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
    )
    granted_routes = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
        allow_null=True,
    )
    default_scope = serializers.ChoiceField(
        choices=["own", "team", "global"],
        required=False,
    )

    def validate_role(self, value):
        try:
            return validate_role_slug(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_area(self, value):
        return validate_area(value)

    def validate(self, attrs):
        codes = set(attrs.get("permissions") or [])
        unknown = codes - ALL_PERMISSIONS
        if unknown:
            raise serializers.ValidationError(
                {"permissions": f"Permissões desconhecidas: {sorted(unknown)}"}
            )
        if codes:
            validate_permissions(codes)
        if "granted_routes" in attrs and attrs["granted_routes"] is not None:
            attrs["granted_routes"] = sorted(
                {str(name).strip() for name in attrs["granted_routes"] if str(name).strip()}
            )
        return attrs


class PortalRoleDefinitionCreateSerializer(PortalRoleDefinitionUpdateSerializer):
    role = serializers.CharField(max_length=64)
    label = serializers.CharField(max_length=128)
    area = serializers.ChoiceField(choices=sorted(VALID_AREAS), required=False, default="outros")
    permissions = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
        default=list,
    )
    default_scope = serializers.ChoiceField(
        choices=["own", "team", "global"],
        required=False,
        default="global",
    )
    granted_routes = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=None,
    )

    def validate_role(self, value):
        return validate_role_slug(value)
