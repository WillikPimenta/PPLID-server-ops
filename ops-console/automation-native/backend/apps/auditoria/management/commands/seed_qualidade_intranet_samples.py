# -*- coding: utf-8 -*-
"""Gera tratados de teste em auditoria_falha_cadastro para o Indicador EO.

Uso:
  python manage.py seed_qualidade_intranet_samples
  python manage.py seed_qualidade_intranet_samples --purge
  python manage.py seed_qualidade_intranet_samples --count 20
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import AuditoriaFalhaCadastro, AuditoriaMotivoFalha
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.workforce.models import Agent

User = get_user_model()

SEED_PREFIX = "EO-SEED-"


def _base_samples(*, cliente: str, workflow: str, motivo: str, agente: str, auditor: str):
    """Cenários canônicos para validar projeção Intranet -> EO."""
    now = timezone.now()  # noqa: F841 — reserved for future timestamps
    def day(offset_days, hour=14):
        return timezone.make_aware(
            datetime(2026, 8, 5, hour, 0) + timedelta(days=offset_days)
        )

    common = {
        "modulo": "G Auditoria",
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
        "cliente": cliente,
        "auditor": auditor,
        "uf_documento": "SP",
        "tipo_documento": "RG",
        "nivel_dificuldade": "Médio",
        "qualidade_imagem": "Boa",
    }

    return [
        {
            **common,
            "protocolo": f"{SEED_PREFIX}SEM-FALHA-001",
            "tipo_falha": "Sem Falha",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": "",
            "etapa_falha": "Análise",
            "descricao_irregularidades": "",
            "resultado_cliente": "Aprovado",
            "novo_resultado": "",
            "analise_concluida_em": day(0),
            "brflow_parsed": {
                "data_analise": "05/08/2026",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Manual",
                "resultado_analise": "Aprovado",
            },
            "_label": "Sem Falha -> só auditado",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}COLAB-ATIVA-002",
            "tipo_falha": "Colaborador",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": motivo,
            "etapa_falha": "Análise Documental",
            "descricao_irregularidades": "Divergência entre documento e bureau.",
            "resultado_cliente": "Aprovado",
            "novo_resultado": "Reprovado",
            "analise_concluida_em": day(0, 15),
            "brflow_parsed": {
                "data_analise": "2026-08-05",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Manual",
                "resultado_analise": "Reprovado",
            },
            "_label": "Colaborador ativa -> auditado + falha Manual",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}AUTO-ATIVA-003",
            "tipo_falha": "Automático",
            "usuario": "",
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": motivo,
            "etapa_falha": "Automático",
            "descricao_irregularidades": "Falha automática de regra.",
            "resultado_cliente": "Suspeito",
            "novo_resultado": "Reprovado",
            "analise_concluida_em": day(1),
            "brflow_parsed": {
                "data_analise": "06/08/2026 10:30:00",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Automático",
                "resultado_analise": "Suspeito",
            },
            "_label": "Automático sem matrícula -> auditado + falha Automático",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}PROC-ATIVA-004",
            "tipo_falha": "Processual",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": motivo,
            "etapa_falha": "Biometria",
            "descricao_irregularidades": "Desvio processual na etapa.",
            "resultado_cliente": "Em análise",
            "novo_resultado": "Aprovado",
            "analise_concluida_em": day(1, 16),
            "brflow_parsed": {
                "data_analise": "2026-08-06T16:00:00",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Processual",
                "resultado_analise": "Em análise",
            },
            "_label": "Processual ativa -> auditado + falha Processual",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}MAPEAMENTO-005",
            "tipo_falha": "Mapeamento",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": motivo,
            "etapa_falha": "Análise",
            "descricao_irregularidades": "Classificado como mapeamento/sistema.",
            "resultado_cliente": "Aprovado",
            "novo_resultado": "Reprovado",
            "analise_concluida_em": day(2),
            "brflow_parsed": {
                "data_analise": "07/08/2026",
                "cliente": cliente,
                "workflow": workflow,
                "resultado_analise": "Aprovado",
            },
            "_label": "Mapeamento -> tipificação Automático",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}MANTIDA-006",
            "tipo_falha": "Colaborador",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA,
            "motivo_falha": motivo,
            "etapa_falha": "Análise Documental",
            "descricao_irregularidades": "Contestação improcedente; falha mantida.",
            "resultado_cliente": "Aprovado",
            "novo_resultado": "Reprovado",
            "analise_concluida_em": day(2, 11),
            "brflow_parsed": {
                "data_analise": "07/08/2026",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Manual",
                "resultado_analise": "Reprovado",
            },
            "_label": "Falha mantida -> conta como falha",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}RETIRADA-007",
            "tipo_falha": "Colaborador",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
            "motivo_falha": motivo,
            "etapa_falha": "Análise Documental",
            "descricao_irregularidades": "Falha retirada após contestação.",
            "resultado_cliente": "Reprovado",
            "novo_resultado": "Aprovado",
            "analise_concluida_em": day(3),
            "brflow_parsed": {
                "data_analise": "08/08/2026",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Manual",
                "resultado_analise": "Aprovado",
            },
            "_label": "Falha retirada -> só auditado (sem falha EO)",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}PROTOCOLO-DUP-A",
            "tipo_falha": "Colaborador",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": motivo,
            "etapa_falha": "Digitalização",
            "descricao_irregularidades": "Etapa 1 do mesmo protocolo.",
            "resultado_cliente": "Aprovado",
            "novo_resultado": "Reprovado",
            "analise_concluida_em": day(3, 9),
            "brflow_parsed": {
                "data_analise": "08/08/2026",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Manual",
                "resultado_analise": "Reprovado",
            },
            "_label": "Mesmo protocolo (linha A) - grain protocolo",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}PROTOCOLO-DUP-A",
            "tipo_falha": "Sem Falha",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": "",
            "etapa_falha": "Análise",
            "descricao_irregularidades": "Etapa 2 do mesmo protocolo (sem falha).",
            "resultado_cliente": "Aprovado",
            "novo_resultado": "",
            "analise_concluida_em": day(3, 10),
            "brflow_parsed": {
                "data_analise": "08/08/2026",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Manual",
                "resultado_analise": "Aprovado",
            },
            "_label": "Mesmo protocolo (linha B) - grain protocolo",
        },
        {
            **common,
            "protocolo": f"{SEED_PREFIX}ANTES-CORTE-010",
            "tipo_falha": "Colaborador",
            "usuario": agente,
            "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
            "motivo_falha": motivo,
            "etapa_falha": "Análise",
            "descricao_irregularidades": "Antes do corte tipico 01/08 (hybrid nao projeta).",
            "resultado_cliente": "Aprovado",
            "novo_resultado": "Reprovado",
            "analise_concluida_em": timezone.make_aware(datetime(2026, 7, 31, 18, 0)),
            "brflow_parsed": {
                "data_analise": "31/07/2026",
                "cliente": cliente,
                "workflow": workflow,
                "tipo_conclusao": "Manual",
                "resultado_analise": "Reprovado",
            },
            "_label": "31/07 - fora do corte hybrid 01/08",
        },
    ]


class Command(BaseCommand):
    help = (
        f"Insere tratados de teste ({SEED_PREFIX}*) em auditoria_falha_cadastro "
        "para validar alimentação do Indicador EO."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--purge",
            action="store_true",
            help=f"Remove registros com protocolo {SEED_PREFIX}* antes de inserir.",
        )
        parser.add_argument(
            "--only-purge",
            action="store_true",
            help=f"Apenas remove {SEED_PREFIX}* sem inserir.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Após inserir, roda sync_one(force=True) para projetar no EO (mesmo com flag off).",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        qs = AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=SEED_PREFIX)
        if options["purge"] or options["only_purge"]:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.WARNING(f"Removidos {deleted} registro(s) {SEED_PREFIX}*."))
            if options["only_purge"]:
                return

        cliente_row = DimCliente.objects.order_by("id_cliente").first()
        workflow_row = DimWorkflow.objects.order_by("id_workflow").first()
        motivo_row = AuditoriaMotivoFalha.objects.filter(active=True).order_by("sort_order", "id").first()
        agent = Agent.objects.filter(active=True).order_by("user_lan_id").first()
        user = User.objects.order_by("date_joined").first()

        cliente = (cliente_row.nome if cliente_row else "Oi S.A.").strip()
        workflow = (workflow_row.nome if workflow_row else "RMPLUS - OI PréVenda").strip()
        motivo = (motivo_row.motivo if motivo_row else "Documento ilegível").strip()
        agente = (agent.user_lan_id if agent else "c91848a").strip().lower()
        auditor = (user.username if user else "eo.seed").strip().lower()

        samples = _base_samples(
            cliente=cliente,
            workflow=workflow,
            motivo=motivo,
            agente=agente,
            auditor=auditor,
        )

        created = []
        for sample in samples:
            label = sample.pop("_label", "")
            # Evita duplicar se já existir o mesmo protocolo+tipo+etapa nesta seed
            existing = AuditoriaFalhaCadastro.objects.filter(
                protocolo=sample["protocolo"],
                tipo_falha=sample["tipo_falha"],
                etapa_falha=sample["etapa_falha"],
                origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            ).first()
            if existing:
                self.stdout.write(f"  skip  {sample['protocolo']} ({label})")
                created.append(existing)
                continue
            obj = AuditoriaFalhaCadastro.objects.create(
                **sample,
                created_by=user,
            )
            created.append(obj)
            msg = (
                f"  +{obj.pk:>6}  {obj.protocolo}  tipo={obj.tipo_falha}  "
                f"status={obj.status_falha}  -- {label}"
            )
            self.stdout.write(self.style.SUCCESS(msg))

        self.stdout.write("")
        self.stdout.write(
            self.style.NOTICE(
                f"Total seed: {len(created)} | cliente={cliente!r} | workflow={workflow!r} | agente={agente}"
            )
        )
        self.stdout.write(
            "Elegíveis para EO (após ativar fonte ou --sync): "
            "origem=auditoria, tipo_registro=auditoria, analise_status=concluido."
        )

        if options["sync"]:
            from apps.qualidade_operacional.services.intranet_source import SyncReport, sync_one

            report = SyncReport()
            for obj in created:
                # Recarrega com FKs
                src = (
                    AuditoriaFalhaCadastro.objects.select_related("atividade", "created_by")
                    .get(pk=obj.pk)
                )
                sync_one(src, force=True, report=report, bump_cache=False)
            from apps.qualidade_operacional.services.performance_cache import (
                bump_quality_cache_version,
            )

            bump_quality_cache_version()
            self.stdout.write(self.style.SUCCESS(f"Sync force: {report.as_dict()}"))
            self.stdout.write(
                self.style.WARNING(
                    "Projeções gravadas com --force. Em modo legacy elas ficam "
                    "ocultas no EO até ativar hybrid/intranet."
                )
            )
