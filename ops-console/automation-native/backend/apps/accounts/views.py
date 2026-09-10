from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.access.permissions import HasPortalPermission
from apps.access.registry import ADM_USERS_MANAGE

from .models import User
from .serializers import UserCreateSerializer, UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("username")
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = ADM_USERS_MANAGE

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        return UserSerializer
