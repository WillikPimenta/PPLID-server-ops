from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from apps.access.permissions import HasPortalPermission
from apps.access.registry import ADM_FALHAS_IMPORT

User = get_user_model()


class PortalPermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("perm_user", password="test")

    @override_settings(ACCESS_ENFORCEMENT=False)
    def test_shadow_mode_allows_without_permission(self):
        request = self.factory.get("/test/")
        request.user = self.user
        perm = HasPortalPermission()
        perm.permission_code = ADM_FALHAS_IMPORT
        self.assertTrue(perm.has_permission(request, object()))

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_enforcement_denies_without_permission(self):
        request = self.factory.get("/test/")
        request.user = self.user
        perm = HasPortalPermission()
        perm.permission_code = ADM_FALHAS_IMPORT
        self.assertFalse(perm.has_permission(request, object()))

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_enforcement_allows_superuser(self):
        request = self.factory.get("/test/")
        request.user = User.objects.create_superuser(
            "su_perm", email="su_perm@test.local", password="test"
        )
        perm = HasPortalPermission()
        perm.permission_code = ADM_FALHAS_IMPORT
        self.assertTrue(perm.has_permission(request, object()))
