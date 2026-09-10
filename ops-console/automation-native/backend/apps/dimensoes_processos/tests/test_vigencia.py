"""Testes de vigência (criar / finalizar / rotacionar) MetaEtapa e ProjecaoSla."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PLAN_ANALISTA, role_group_name
from apps.dimensoes_processos.models import (
    DimCliente,
    DimEtapa,
    DimNivelHierarquico,
    DimServico,
    DimWorkflow,
    MetaEtapa,
    ProjecaoSla,
)
from apps.dimensoes_processos.services import vigencia as vig

User = get_user_model()


class VigenciaServiceTests(TestCase):
    def setUp(self):
        self.etapa = DimEtapa.objects.create(id_etapa=1, nome="Etapa A")
        self.servico = DimServico.objects.create(id_servico=10, nome="Serviço X", meta_dia=100)
        self.cliente = DimCliente.objects.create(id_cliente=1, nome="Cliente A")
        self.workflow = DimWorkflow.objects.create(id_workflow=1, nome="WF A")
        self.nh = DimNivelHierarquico.objects.create(id_nh=1, nome="NH1")

    def test_criar_e_rotacionar_meta_sem_gap(self):
        atual = vig.criar_meta_ciclo(
            data_inicio=date(2026, 1, 1),
            etapa_id=self.etapa.pk,
            servico_id=self.servico.pk,
            meta_dia=50,
        )
        self.assertIsNone(atual.data_fim)

        fechado, novo = vig.rotacionar_meta_ciclo(
            atual,
            data_inicio_novo=date(2026, 3, 1),
            meta_dia=80,
        )
        self.assertEqual(fechado.data_fim, date(2026, 2, 28))
        self.assertEqual(novo.data_inicio, date(2026, 3, 1))
        self.assertIsNone(novo.data_fim)
        self.assertEqual(novo.meta_dia, 80)
        self.assertEqual(novo.etapa_id, self.etapa.pk)

    def test_criar_meta_rejeita_segundo_vigente(self):
        vig.criar_meta_ciclo(
            data_inicio=date(2026, 1, 1),
            etapa_id=self.etapa.pk,
            servico_id=None,
            meta_dia=10,
        )
        with self.assertRaises(ValidationError):
            vig.criar_meta_ciclo(
                data_inicio=date(2026, 2, 1),
                etapa_id=self.etapa.pk,
                servico_id=None,
                meta_dia=20,
            )

    def test_finalizar_meta(self):
        atual = vig.criar_meta_ciclo(
            data_inicio=date(2026, 1, 1),
            etapa_id=self.etapa.pk,
            servico_id=self.servico.pk,
            meta_dia=50,
        )
        fechado = vig.finalizar_ciclo(atual, date(2026, 1, 31))
        self.assertEqual(fechado.data_fim, date(2026, 1, 31))

    def test_rotacionar_exige_data_posterior(self):
        atual = vig.criar_meta_ciclo(
            data_inicio=date(2026, 1, 10),
            etapa_id=self.etapa.pk,
            servico_id=self.servico.pk,
            meta_dia=50,
        )
        with self.assertRaises(ValidationError):
            vig.rotacionar_meta_ciclo(atual, data_inicio_novo=date(2026, 1, 10), meta_dia=60)

    def test_rotacionar_sla_sem_gap(self):
        atual = vig.criar_sla_ciclo(
            data_inicio=date(2026, 1, 1),
            cliente_id=self.cliente.pk,
            workflow_id=self.workflow.pk,
            nivel_hierarquico_id=self.nh.pk,
            dias_semana="{0..4}",
            sla_segundos=3600,
        )
        fechado, novo = vig.rotacionar_sla_ciclo(
            atual,
            data_inicio_novo=date(2026, 4, 1),
            sla_segundos=7200,
        )
        self.assertEqual(fechado.data_fim, date(2026, 3, 31))
        self.assertEqual(novo.sla_segundos, 7200)
        self.assertEqual(novo.dias_semana, "{0..4}")
        self.assertIsNone(novo.data_fim)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class VigenciaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.plan = User.objects.create_user(
            username="plan_ciclo", password="x", email="plan_ciclo@test.local"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.plan.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))

        self.op = User.objects.create_user(
            username="op_ciclo", password="x", email="op_ciclo@test.local"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        self.op.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))

        self.etapa = DimEtapa.objects.create(id_etapa=2, nome="Etapa API")
        self.cliente = DimCliente.objects.create(id_cliente=2, nome="Cli API")
        self.workflow = DimWorkflow.objects.create(id_workflow=2, nome="WF API")
        self.nh = DimNivelHierarquico.objects.create(id_nh=2, nome="NH2")

    def test_plan_pode_criar_e_rotacionar_meta(self):
        self.client.force_authenticate(user=self.plan)
        res = self.client.post(
            "/api/v1/dimensoes-processos/metas-etapa/ciclos/",
            {
                "acao": "criar",
                "data_inicio": "2026-01-01",
                "etapa": self.etapa.pk,
                "meta_dia": "40",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        pk = res.data["item"]["id"]

        res2 = self.client.post(
            "/api/v1/dimensoes-processos/metas-etapa/ciclos/",
            {"acao": "rotacionar", "id": pk, "data_inicio": "2026-02-01", "meta_dia": "55"},
            format="json",
        )
        self.assertEqual(res2.status_code, 201, res2.data)
        self.assertEqual(res2.data["fechado"]["data_fim"], "2026-01-31")
        self.assertIsNone(res2.data["item"]["data_fim"])

    def test_op_denied_ciclos(self):
        self.client.force_authenticate(user=self.op)
        res = self.client.post(
            "/api/v1/dimensoes-processos/metas-etapa/ciclos/",
            {"acao": "criar", "data_inicio": "2026-01-01", "etapa": self.etapa.pk, "meta_dia": "1"},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_list_vigente_filter(self):
        MetaEtapa.objects.create(
            data_inicio=date(2025, 1, 1),
            data_fim=date(2025, 12, 31),
            etapa=self.etapa,
            meta_dia=10,
        )
        MetaEtapa.objects.create(
            data_inicio=date(2026, 1, 1),
            data_fim=None,
            etapa=self.etapa,
            meta_dia=20,
        )
        self.client.force_authenticate(user=self.plan)
        res = self.client.get("/api/v1/dimensoes-processos/metas-etapa/", {"vigente": "1"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        self.assertIsNone(res.data["results"][0]["data_fim"])

    def test_plan_pode_criar_sla(self):
        self.client.force_authenticate(user=self.plan)
        res = self.client.post(
            "/api/v1/dimensoes-processos/projecao-sla/ciclos/",
            {
                "acao": "criar",
                "data_inicio": "2026-01-01",
                "cliente": self.cliente.pk,
                "workflow": self.workflow.pk,
                "nivel_hierarquico": self.nh.pk,
                "dias_semana": "{0..4}",
                "sla_segundos": 1800,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["item"]["dias_semana"], "{0..4}")
