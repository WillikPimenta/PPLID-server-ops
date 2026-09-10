from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import (
    DerivacaoEtapaComparativo,
    DerivacaoEtapaImportRun,
    DimCliente,
    DimEtapa,
    DimNomeAlias,
    DimWorkflow,
)
from apps.dimensoes_processos.services.derivacao_etapa.auto_resolver import auto_resolve_comparativo


User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class DerivacaoAutoResolverTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="auto_resolver_user",
            email="auto_resolver_user@test.local",
            password="x",
        )
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(group)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.scan = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            status=DerivacaoEtapaImportRun.STATUS_OK,
        )

    def test_auto_resolver_classifies_pending_rows(self):
        DerivacaoEtapaComparativo.objects.create(
            scan_run=self.scan,
            dimensao=DimNomeAlias.DIM_CLIENTE,
            nome_origem="JUVO BRASIL TECNOLOGIA LTDA",
            linhas_csv=1,
            registros_total=10,
            status=DerivacaoEtapaComparativo.STATUS_UNMATCHED,
        )
        DerivacaoEtapaComparativo.objects.create(
            scan_run=self.scan,
            dimensao=DimNomeAlias.DIM_WORKFLOW,
            nome_origem="POC | PILOTO - EXECUCAO BRSAFE",
            linhas_csv=1,
            registros_total=100,
            status=DerivacaoEtapaComparativo.STATUS_UNMATCHED,
        )
        DerivacaoEtapaComparativo.objects.create(
            scan_run=self.scan,
            dimensao=DimNomeAlias.DIM_ETAPA,
            nome_origem="OCR - 99PAY - Documentoscopia Especializada",
            linhas_csv=1,
            registros_total=500,
            status=DerivacaoEtapaComparativo.STATUS_UNMATCHED,
        )

        stats = auto_resolve_comparativo(scan_run_id=self.scan.pk, user=self.user, dry_run=False)

        self.assertEqual(stats.poc_teste, 2)
        self.assertEqual(stats.etapa_automatica, 1)
        self.assertEqual(DimCliente.objects.filter(operations=False).count(), 1)
        self.assertEqual(DimWorkflow.objects.filter(ind_considerar=False).count(), 1)
        self.assertTrue(
            DimNomeAlias.objects.filter(
                dimensao=DimNomeAlias.DIM_ETAPA,
                classificacao=DimNomeAlias.CLASS_ETAPA_AUTOMATICA,
            ).exists()
        )
        bucket = DimEtapa.objects.filter(manual=False, nome__icontains="OCR").first()
        self.assertIsNotNone(bucket)

    def test_auto_resolver_dry_run_does_not_persist(self):
        DerivacaoEtapaComparativo.objects.create(
            scan_run=self.scan,
            dimensao=DimNomeAlias.DIM_ETAPA,
            nome_origem="Integrador - TESTE",
            linhas_csv=1,
            registros_total=1,
            status=DerivacaoEtapaComparativo.STATUS_UNMATCHED,
        )

        stats = auto_resolve_comparativo(scan_run_id=self.scan.pk, user=self.user, dry_run=True)

        self.assertEqual(stats.etapa_automatica, 1)
        self.assertEqual(DimNomeAlias.objects.count(), 0)

    def test_auto_resolver_api(self):
        DerivacaoEtapaComparativo.objects.create(
            scan_run=self.scan,
            dimensao=DimNomeAlias.DIM_WORKFLOW,
            nome_origem="AMX-homolog-PV - AMX - homologação PV",
            linhas_csv=1,
            registros_total=5,
            status=DerivacaoEtapaComparativo.STATUS_UNMATCHED,
        )

        res = self.client.post(
            "/api/v1/dimensoes-processos/derivacao-etapa/comparativo/auto-resolver/",
            {"scan_run_id": self.scan.pk, "dry_run": True},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["dry_run"])
        self.assertEqual(res.data["stats"]["poc_teste"], 1)
