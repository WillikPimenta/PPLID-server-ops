from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.controle_sla.models import EtapaGap


class EtapaGapActionsApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="sla.tester", password="x")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.gap = EtapaGap.objects.create(
            cod_cliente=1,
            nom_cliente="Cliente",
            nom_workflow="WF",
            nom_fluxo="Etapa X",
            situacao=EtapaGap.SIT_PENDENTE,
            ocorrencias=1,
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_ignorar_and_block_second_change(self, _mock_perm):
        url = f"/api/v1/controle-sla/gaps/{self.gap.id}/ignorar/"
        r1 = self.client.post(url)
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertEqual(r1.data["gap"]["situacao"], "ignorada")
        self.assertFalse(r1.data["gap"]["pode_tratar"])

        r2 = self.client.post(url)
        self.assertEqual(r2.status_code, 400)

        cad = self.client.post(
            f"/api/v1/controle-sla/gaps/{self.gap.id}/cadastrar/",
            {"projecao_sla_id": 99},
            format="json",
        )
        self.assertEqual(cad.status_code, 400)

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_cadastrar_links_projecao(self, _mock_perm):
        url = f"/api/v1/controle-sla/gaps/{self.gap.id}/cadastrar/"
        r = self.client.post(url, {"etapa_id": 42}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["gap"]["situacao"], "cadastrada")
        self.assertEqual(r.data["gap"]["etapa_id"], 42)
        self.assertEqual(r.data["gap"]["tratado_por"]["username"], "sla.tester")
        self.assertIsNotNone(r.data["gap"]["tratado_em"])
