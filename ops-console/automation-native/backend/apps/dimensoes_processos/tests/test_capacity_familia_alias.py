from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import CapacityFamiliaAlias
from apps.dimensoes_processos.services.capacity_familia import (
    extract_familia_normalized,
    invalidate_familia_alias_cache,
    normalize_familia,
)


class CapacityFamiliaAliasModelTests(TestCase):
    def setUp(self):
        invalidate_familia_alias_cache()

    def tearDown(self):
        invalidate_familia_alias_cache()

    def test_db_alias_overrides_static_map(self):
        CapacityFamiliaAlias.objects.create(
            alias_key="variante custom",
            alias_origem="Variante Custom",
            familia_canonica="Família Oficial",
        )
        invalidate_familia_alias_cache()
        self.assertEqual(normalize_familia("Variante Custom"), "Família Oficial")

    def test_extract_normalized_uses_db_alias(self):
        CapacityFamiliaAlias.objects.create(
            alias_key="selfie especial",
            alias_origem="Selfie Especial",
            familia_canonica="Selfie",
        )
        invalidate_familia_alias_cache()
        self.assertEqual(
            extract_familia_normalized("Selfie Especial - Cliente X"),
            "Selfie",
        )


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class CapacityFamiliaAliasApiTests(TestCase):
    def setUp(self):
        invalidate_familia_alias_cache()
        User = get_user_model()
        self.user = User.objects.create_user(username="cap_alias_user", password="x")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(group)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = "/api/v1/dimensoes-processos/capacity/familia-aliases/"

    def tearDown(self):
        invalidate_familia_alias_cache()

    def test_create_and_list_alias(self):
        response = self.client.post(
            self.url,
            {
                "alias_origem": "Reclassificação 2",
                "familia_canonica": "Reclassificação",
                "notas": "Unificar variantes numeradas",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["created"])

        list_response = self.client.get(self.url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data["results"]), 1)
        self.assertEqual(list_response.data["results"][0]["familia_canonica"], "Reclassificação")

    def test_patch_deactivates_alias(self):
        row = CapacityFamiliaAlias.objects.create(
            alias_key="temp",
            alias_origem="Temp",
            familia_canonica="Canonical",
        )
        response = self.client.patch(self.url, {"id": row.pk, "ativo": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["result"]["ativo"])

        list_response = self.client.get(self.url)
        self.assertEqual(len(list_response.data["results"]), 0)
