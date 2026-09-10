from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from apps.access.services.impersonation import (
    build_access_payload,
    get_session_impersonate_lan_id,
    resolve_effective_user,
)

from .serializers import AuthUserSerializer, ChangePasswordSerializer, LoginSerializer


class AuthLoginRateThrottle(SimpleRateThrottle):
    scope = "auth_login"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


INVALID_CREDENTIALS_MESSAGE = "Usuário ou senha inválidos."


def _auth_user_payload(user) -> dict:
    return AuthUserSerializer(user).data


@api_view(["GET"])
@ensure_csrf_cookie
@permission_classes([AllowAny])
def csrf_token_view(request):
    token = get_token(request)
    return Response({"csrfToken": token})


@api_view(["GET"])
@permission_classes([AllowAny])
def me_view(request):
    if not request.user.is_authenticated:
        return Response({"authenticated": False, "user": None})

    real = request.user
    effective = resolve_effective_user(request)
    impersonating = bool(get_session_impersonate_lan_id(request)) and effective.pk != real.pk
    return Response(
        {
            "authenticated": True,
            "user": _auth_user_payload(effective),
            "access": build_access_payload(effective, inject_impersonate=impersonating),
            "impersonating": impersonating,
            "real_user": _auth_user_payload(real) if impersonating else None,
            "impersonate_lan_id": get_session_impersonate_lan_id(request) if impersonating else None,
        }
    )


@csrf_protect
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthLoginRateThrottle])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    username = serializer.validated_data["username"].strip().lower()
    password = serializer.validated_data["password"]

    user = authenticate(
        request,
        username=username,
        password=password,
    )
    if user is None:
        return Response(
            {"detail": INVALID_CREDENTIALS_MESSAGE},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not user.is_active:
        return Response(
            {"detail": INVALID_CREDENTIALS_MESSAGE},
            status=status.HTTP_400_BAD_REQUEST,
        )

    login(request, user)
    return Response(
        {
            **_auth_user_payload(user),
            "csrfToken": get_token(request),
            "access": build_access_payload(user),
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    serializer = ChangePasswordSerializer(
        data=request.data,
        context={"user": request.user},
    )
    serializer.is_valid(raise_exception=True)

    user = request.user
    new_password = serializer.validated_data["new_password"]

    if user.must_change_password:
        pass
    else:
        current_password = serializer.validated_data.get("current_password") or ""
        if not user.check_password(current_password):
            return Response(
                {"detail": "Senha atual incorreta."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    update_session_auth_hash(request, user)

    return Response(_auth_user_payload(user))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    logout(request)
    return Response({"detail": "ok"})
