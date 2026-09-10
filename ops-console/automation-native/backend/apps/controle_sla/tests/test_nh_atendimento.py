# -*- coding: utf-8 -*-
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.controle_sla.models import SlaBreach
from apps.controle_sla.services.nh_atendimento_lookup import (
    build_nh_atendimento_index,
    resolve_nh_atendimento,
    resolve_nh_atendimento_detail,
)
from apps.prioridades_nh.models import NhPrioridadeFluxo


def _prio_row(
    *,
    now,
    prk_cliente: int,
    nom_cliente: str,
    prk_workflow: int,
    nom_workflow: str,
    prk_nh: int,
    nom_nh: str,
    prk_fluxo: int,
    nom_fluxo: str,
    prio: int,
):
    return NhPrioridadeFluxo.objects.create(
        prk_cliente=prk_cliente,
        nom_cliente=nom_cliente,
        prk_workflow=prk_workflow,
        nom_workflow=nom_workflow,
        prk_nivel_hierarquico=prk_nh,
        nom_nivel_hierarquico=nom_nh,
        prk_fluxo=prk_fluxo,
        nom_fluxo=nom_fluxo,
        num_prioridade_fluxo=prio,
        synced_at=now,
    )


def _breach(
    *,
    now,
    cod_cliente: int,
    nom_cliente: str,
    id_workflow: int,
    nom_workflow: str,
    nom_fluxo: str,
    qtd_fila: int,
):
    return SlaBreach.objects.create(
        cod_cliente=cod_cliente,
        nom_cliente=nom_cliente,
        nom_workflow=nom_workflow,
        id_workflow=id_workflow,
        nom_fluxo=nom_fluxo,
        qtd_fila=qtd_fila,
        dat_registro_antigo=now,
        idade_segundos=4000,
        sla_limite_segundos=3600,
        excedente_segundos=400,
        pct_sla=111.1,
        criticidade=SlaBreach.CRIT_ALTO,
        first_detected_at=now,
        last_seen_at=now,
    )


class NhAtendimentoLookupTests(TestCase):
    def test_resolve_prefers_nh_with_smaller_fila_sum_ahead(self):
        """
        Soma das filas das etapas à frente decide o NH.
        NH_TESTE: 5 etapas à frente com fila 10 cada → 50
        NH_TESTE1: 1 etapa à frente com fila 3 → 3 → escolhe NH_TESTE1.
        """
        now = timezone.now()
        for i in range(6, 11):
            wf = 1000 + i
            _prio_row(
                now=now,
                prk_cliente=10,
                nom_cliente="Cliente A",
                prk_workflow=wf,
                nom_workflow=f"WF Ahead {i}",
                prk_nh=1,
                nom_nh="NH_TESTE",
                prk_fluxo=1000 + i,
                nom_fluxo=f"Etapa Ahead {i}",
                prio=i,
            )
            _breach(
                now=now,
                cod_cliente=10,
                nom_cliente="Cliente A",
                id_workflow=wf,
                nom_workflow=f"WF Ahead {i}",
                nom_fluxo=f"Etapa Ahead {i}",
                qtd_fila=10,
            )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nh=1,
            nom_nh="NH_TESTE",
            prk_fluxo=2001,
            nom_fluxo="ETAPA1",
            prio=3,
        )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=3001,
            nom_workflow="WF Ahead Unica",
            prk_nh=2,
            nom_nh="NH_TESTE1",
            prk_fluxo=3001,
            nom_fluxo="Etapa Ahead Unica",
            prio=10,
        )
        _breach(
            now=now,
            cod_cliente=10,
            nom_cliente="Cliente A",
            id_workflow=3001,
            nom_workflow="WF Ahead Unica",
            nom_fluxo="Etapa Ahead Unica",
            qtd_fila=3,
        )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nh=2,
            nom_nh="NH_TESTE1",
            prk_fluxo=2001,
            nom_fluxo="ETAPA1",
            prio=5,
        )

        index = build_nh_atendimento_index()
        detail = resolve_nh_atendimento_detail(
            cod_cliente=10,
            id_workflow=100,
            nom_cliente="Cliente A",
            nom_workflow="WF A",
            nom_fluxo="ETAPA1",
            index=index,
        )
        self.assertEqual(detail["nh_atendimento"], "NH_TESTE1")
        self.assertEqual(detail["nh_etapas_a_frente"], 3)

    def test_fewer_etapas_but_larger_fila_loses(self):
        """1 etapa à frente com fila 100 perde para 3 etapas com fila 1 (soma 3)."""
        now = timezone.now()
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nh=1,
            nom_nh="NH Pesado",
            prk_fluxo=1,
            nom_fluxo="Alvo",
            prio=1,
        )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=201,
            nom_workflow="WF Heavy",
            prk_nh=1,
            nom_nh="NH Pesado",
            prk_fluxo=2,
            nom_fluxo="Ahead Heavy",
            prio=10,
        )
        _breach(
            now=now,
            cod_cliente=10,
            nom_cliente="Cliente A",
            id_workflow=201,
            nom_workflow="WF Heavy",
            nom_fluxo="Ahead Heavy",
            qtd_fila=100,
        )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nh=2,
            nom_nh="NH Leve",
            prk_fluxo=1,
            nom_fluxo="Alvo",
            prio=1,
        )
        for i in range(3):
            wf = 301 + i
            _prio_row(
                now=now,
                prk_cliente=10,
                nom_cliente="Cliente A",
                prk_workflow=wf,
                nom_workflow=f"WF Light {i}",
                prk_nh=2,
                nom_nh="NH Leve",
                prk_fluxo=10 + i,
                nom_fluxo=f"Ahead Light {i}",
                prio=10 + i,
            )
            _breach(
                now=now,
                cod_cliente=10,
                nom_cliente="Cliente A",
                id_workflow=wf,
                nom_workflow=f"WF Light {i}",
                nom_fluxo=f"Ahead Light {i}",
                qtd_fila=1,
            )

        index = build_nh_atendimento_index()
        detail = resolve_nh_atendimento_detail(
            cod_cliente=10,
            id_workflow=100,
            nom_fluxo="Alvo",
            index=index,
        )
        self.assertEqual(detail["nh_atendimento"], "NH Leve")
        self.assertEqual(detail["nh_etapas_a_frente"], 3)

    def test_resolve_by_names_when_ids_missing(self):
        now = timezone.now()
        _prio_row(
            now=now,
            prk_cliente=20,
            nom_cliente="Cliente B",
            prk_workflow=200,
            nom_workflow="WF B",
            prk_nh=3,
            nom_nh="NH Outro",
            prk_fluxo=2001,
            nom_fluxo="Outra Etapa",
            prio=1,
        )
        index = build_nh_atendimento_index()
        nh = resolve_nh_atendimento(
            cod_cliente=None,
            id_workflow=None,
            nom_cliente="Cliente B",
            nom_workflow="WF B",
            nom_fluxo="Outra Etapa",
            index=index,
        )
        self.assertEqual(nh, "NH Outro")

    def test_priority_zero_counts_as_end_of_queue(self):
        """
        Prioridade 0 = fim da fila: soma filas de todas as etapas positivas.
        """
        now = timezone.now()
        for i in range(1, 6):
            wf = 1000 + i
            _prio_row(
                now=now,
                prk_cliente=186,
                nom_cliente="Claro - Brsafe",
                prk_workflow=wf,
                nom_workflow=f"WF Outra {i}",
                prk_nh=1,
                nom_nh="BrScan - Antifraude - Análise visual Especial",
                prk_fluxo=1000 + i,
                nom_fluxo=f"Outra {i}",
                prio=i,
            )
            _breach(
                now=now,
                cod_cliente=186,
                nom_cliente="Claro - Brsafe",
                id_workflow=wf,
                nom_workflow=f"WF Outra {i}",
                nom_fluxo=f"Outra {i}",
                qtd_fila=4,
            )
        _prio_row(
            now=now,
            prk_cliente=186,
            nom_cliente="Claro - Brsafe",
            prk_workflow=101,
            nom_workflow="AMX - PreVenda",
            prk_nh=1,
            nom_nh="BrScan - Antifraude - Análise visual Especial",
            prk_fluxo=2001,
            nom_fluxo="Análise Visual - Novo Fluxo - AMX PréVenda",
            prio=0,
        )
        _prio_row(
            now=now,
            prk_cliente=186,
            nom_cliente="Claro - Brsafe",
            prk_workflow=301,
            nom_workflow="WF Ahead",
            prk_nh=2,
            nom_nh="NH Atendimento Rápido",
            prk_fluxo=3001,
            nom_fluxo="Ahead",
            prio=10,
        )
        _breach(
            now=now,
            cod_cliente=186,
            nom_cliente="Claro - Brsafe",
            id_workflow=301,
            nom_workflow="WF Ahead",
            nom_fluxo="Ahead",
            qtd_fila=2,
        )
        _prio_row(
            now=now,
            prk_cliente=186,
            nom_cliente="Claro - Brsafe",
            prk_workflow=101,
            nom_workflow="AMX - PreVenda",
            prk_nh=2,
            nom_nh="NH Atendimento Rápido",
            prk_fluxo=2001,
            nom_fluxo="Análise Visual - Novo Fluxo - AMX PréVenda",
            prio=2,
        )

        index = build_nh_atendimento_index()
        detail = resolve_nh_atendimento_detail(
            cod_cliente=186,
            id_workflow=101,
            nom_cliente="Claro - Brsafe",
            nom_workflow="AMX - PreVenda",
            nom_fluxo="Análise Visual - Novo Fluxo - AMX PréVenda",
            index=index,
        )
        self.assertEqual(detail["nh_atendimento"], "NH Atendimento Rápido")
        self.assertEqual(detail["nh_prioridade"], 2)
        self.assertEqual(detail["nh_etapas_a_frente"], 2)

    def test_resolve_empty_when_no_match(self):
        now = timezone.now()
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nh=1,
            nom_nh="NH Alfa",
            prk_fluxo=1001,
            nom_fluxo="Etapa Critica",
            prio=1,
        )
        index = build_nh_atendimento_index()
        nh = resolve_nh_atendimento(
            cod_cliente=10,
            id_workflow=100,
            nom_fluxo="Etapa Inexistente",
            index=index,
        )
        self.assertEqual(nh, "")

    def test_tie_break_higher_priority_when_same_fila_sum(self):
        """Mesma soma de filas à frente → maior num_prioridade_fluxo."""
        now = timezone.now()
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nh=1,
            nom_nh="NH A",
            prk_fluxo=10,
            nom_fluxo="Alvo",
            prio=3,
        )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=111,
            nom_workflow="WF Ahead A",
            prk_nh=1,
            nom_nh="NH A",
            prk_fluxo=11,
            nom_fluxo="Ahead A",
            prio=10,
        )
        _breach(
            now=now,
            cod_cliente=10,
            nom_cliente="Cliente A",
            id_workflow=111,
            nom_workflow="WF Ahead A",
            nom_fluxo="Ahead A",
            qtd_fila=5,
        )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nh=2,
            nom_nh="NH B",
            prk_fluxo=10,
            nom_fluxo="Alvo",
            prio=2,
        )
        _prio_row(
            now=now,
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=112,
            nom_workflow="WF Ahead B",
            prk_nh=2,
            nom_nh="NH B",
            prk_fluxo=12,
            nom_fluxo="Ahead B",
            prio=10,
        )
        _breach(
            now=now,
            cod_cliente=10,
            nom_cliente="Cliente A",
            id_workflow=112,
            nom_workflow="WF Ahead B",
            nom_fluxo="Ahead B",
            qtd_fila=5,
        )
        index = build_nh_atendimento_index()
        nh = resolve_nh_atendimento(
            cod_cliente=10,
            id_workflow=100,
            nom_fluxo="Alvo",
            index=index,
        )
        self.assertEqual(nh, "NH A")


class NhAtendimentoBreachesApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="sla.nh", password="x")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        now = timezone.now()
        NhPrioridadeFluxo.objects.create(
            prk_cliente=10,
            nom_cliente="Cliente A",
            prk_workflow=100,
            nom_workflow="WF A",
            prk_nivel_hierarquico=1,
            nom_nivel_hierarquico="NH Atendimento Alfa",
            prk_fluxo=1001,
            nom_fluxo="Etapa Critica",
            num_prioridade_fluxo=1,
            synced_at=now,
        )
        SlaBreach.objects.create(
            cod_cliente=10,
            nom_cliente="Cliente A",
            nom_workflow="WF A",
            id_workflow=100,
            nom_fluxo="Etapa Critica",
            qtd_fila=3,
            dat_registro_antigo=now,
            idade_segundos=4000,
            sla_limite_segundos=3600,
            excedente_segundos=400,
            pct_sla=111.1,
            criticidade=SlaBreach.CRIT_ALTO,
            first_detected_at=now,
            last_seen_at=now,
        )

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    def test_breaches_include_nh_atendimento_without_persisting(self, _mock_perm):
        resp = self.client.get("/api/v1/controle-sla/breaches/")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["nh_atendimento"], "NH Atendimento Alfa")
        self.assertEqual(results[0]["nh_agentes_alocados"], 0)
        self.assertNotIn("nh_atendimento", {f.name for f in SlaBreach._meta.get_fields()})

    @patch("apps.access.resolve.user_has_permission", return_value=True)
    @patch(
        "apps.controle_sla.views._nh_agentes_alocados_by_name",
        return_value={"nh atendimento alfa": 4},
    )
    def test_breaches_include_nh_agentes_alocados_from_schedule(self, _mock_counts, _mock_perm):
        resp = self.client.get("/api/v1/controle-sla/breaches/")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["nh_agentes_alocados"], 4)
