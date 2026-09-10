# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import QualidadeDimAlias
from apps.qualidade_operacional.services.intranet_source import (
    SyncReport,
    build_lookup_caches,
    map_source_to_payloads,
    sync_one,
)
from apps.qualidade_operacional.services.source_config import MAPPING_VERSION


@override_settings(
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="intranet",
)
class DimAliasResolutionTests(TestCase):
    def setUp(self):
        DimCliente.objects.create(id_cliente=10, nome="Picpay Serviços SA")
        DimWorkflow.objects.create(id_workflow=623, nome="TIM Brasil - Documentoscopia Especializada")
        DimWorkflow.objects.create(id_workflow=836, nome="TIM Brasil - Documentoscopia Especializada")
        QualidadeDimAlias.objects.get_or_create(
            kind=QualidadeDimAlias.KIND_CLIENTE,
            alias_key="PICPAY",
            defaults={"target_id": 10, "evidence": "test"},
        )
        QualidadeDimAlias.objects.get_or_create(
            kind=QualidadeDimAlias.KIND_WORKFLOW,
            alias_key="TIM BRASIL - DOCUMENTOSCOPIA ESPECIALIZADA",
            defaults={"target_id": 623, "evidence": "canonical"},
        )

    def _source(self, **kwargs) -> AuditoriaFalhaCadastro:
        defaults = {
            "protocolo": "ALIAS-1",
            "tipo_falha": "Colaborador",
            "usuario": "c10001a",
            "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "analise_concluida_em": timezone.now(),
            "modulo": "G Auditoria",
            "motivo_falha": "Documento ilegível",
            "etapa_falha": "Análise",
            "brflow_parsed": {
                "data_analise": "2026-08-05",
                "cliente": "PICPAY",
                "workflow": "TIM Brasil - Documentoscopia Especializada",
                "tipo_conclusao": "Manual",
                "resultado_analise": "Aprovado",
            },
            "cliente": "PICPAY",
        }
        defaults.update(kwargs)
        return AuditoriaFalhaCadastro.objects.create(**defaults)

    def test_alias_resolves_cliente_and_ambiguous_workflow(self):
        source = self._source()
        caches = build_lookup_caches([source])
        auditado, _falha, warnings, _fp = map_source_to_payloads(source, caches)
        self.assertEqual(auditado["id_cliente"], 10)
        self.assertEqual(auditado["id_workflow"], 623)
        self.assertTrue(any("cliente_alias" in w for w in warnings))
        self.assertTrue(any("workflow_alias" in w for w in warnings))

    def test_mapping_version_reprocessa_regra_de_status_falha(self):
        self.assertEqual(MAPPING_VERSION, 8)

    def test_data_analise_ausente_warns_non_reinspecao(self):
        source = self._source(
            brflow_parsed={
                "cliente": "PICPAY",
                "workflow": "TIM Brasil - Documentoscopia Especializada",
                "tipo_conclusao": "Manual",
            }
        )
        caches = build_lookup_caches([source])
        _auditado, _falha, warnings, _fp = map_source_to_payloads(source, caches)
        self.assertIn("data_analise_ausente", warnings)

    def test_reinspecao_allows_null_data_analise_without_extra_warning(self):
        source = self._source(
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            brflow_parsed={
                "cliente": "PICPAY",
                "workflow": "TIM Brasil - Documentoscopia Especializada",
            },
        )
        caches = build_lookup_caches([source])
        _auditado, _falha, warnings, _fp = map_source_to_payloads(source, caches)
        self.assertNotIn("data_analise_ausente", warnings)

    def test_sync_reprojects_on_mapping_version_change(self):
        source = self._source()
        report1 = SyncReport()
        sync_one(source, dry_run=False, report=report1)
        self.assertGreater(report1.created + report1.updated, 0)
        report2 = SyncReport()
        sync_one(source, dry_run=False, report=report2)
        self.assertEqual(report2.unchanged, 1)
