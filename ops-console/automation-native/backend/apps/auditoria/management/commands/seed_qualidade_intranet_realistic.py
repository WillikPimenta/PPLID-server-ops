# -*- coding: utf-8 -*-
"""Seed 110×7 sanitizado para validar alimentação EO (perfil realista)."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.auditoria.models import AuditoriaAtividade, AuditoriaFalhaCadastro, AuditoriaMotivoFalha
from apps.auditoria.testing.qualidade_intranet_realistic_profile import (
    AGENTE_COM_MATRICULA,
    AUDITORIA_CANDIDATES,
    CLIENTE,
    SEED_PREFIX,
    WORKFLOW,
    build_realistic_rows,
    motivo_catalog_rows,
)
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.workforce.models import Agent

User = get_user_model()


class Command(BaseCommand):
    help = (
        f"Insere perfil realista ({SEED_PREFIX}*) com 110 tratados "
        "(101 reinspeção, 2 contestação, 7 auditoria) para validar EO."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--purge",
            action="store_true",
            help=f"Remove registros {SEED_PREFIX}* antes de inserir.",
        )
        parser.add_argument(
            "--only-purge",
            action="store_true",
            help=f"Apenas remove {SEED_PREFIX}* sem inserir.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Após inserir, executa sync_qualidade_intranet (sem --force).",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        qs = AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=SEED_PREFIX)
        if options["purge"] or options["only_purge"]:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.WARNING(f"Removidos {deleted} registro(s) {SEED_PREFIX}*."))
            if options["only_purge"]:
                return

        user = User.objects.order_by("date_joined").first()
        DimCliente.objects.get_or_create(id_cliente=9001, defaults={"nome": CLIENTE})
        DimWorkflow.objects.get_or_create(id_workflow=9001, defaults={"nome": WORKFLOW})
        Agent.objects.get_or_create(
            user_lan_id=AGENTE_COM_MATRICULA,
            defaults={"full_name": "Agente EO Simulado", "active": True},
        )
        for row in motivo_catalog_rows():
            AuditoriaMotivoFalha.objects.get_or_create(
                motivo=row["motivo"],
                defaults={
                    "criticidade": row["criticidade"],
                    "segmentos": row["segmentos"],
                    "subsegmento": row["subsegmento"],
                    "active": True,
                },
            )

        created = 0
        for sample in build_realistic_rows():
            protocolo = sample["protocolo"]
            existing = AuditoriaFalhaCadastro.objects.filter(
                protocolo=protocolo,
                tipo_falha=sample["tipo_falha"],
                etapa_falha=sample.get("etapa_falha", ""),
            ).first()
            if existing:
                continue
            AuditoriaFalhaCadastro.objects.create(**sample, created_by=user)
            created += 1

        # Atividades concluídas para os 7 candidatos (exigência de elegibilidade com FK).
        for candidate in AUDITORIA_CANDIDATES:
            protocolo = candidate["protocolo"]
            for src in AuditoriaFalhaCadastro.objects.filter(protocolo=protocolo):
                if src.atividade_id:
                    continue
                atividade = AuditoriaAtividade.objects.create(
                    tipo=AuditoriaAtividade.TIPO_AUDITORIA,
                    nome=f"Aud {protocolo}",
                    status=AuditoriaAtividade.STATUS_CONCLUIDA,
                    cliente=CLIENTE,
                    workflow=WORKFLOW,
                    encerrado_em=src.analise_concluida_em,
                    created_by=user,
                )
                src.atividade = atividade
                src.save(update_fields=["atividade", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Inseridos {created} registro(s) | elegíveis esperados: 7 | prefixo={SEED_PREFIX}"
            )
        )
        self.stdout.write(
            "Ativar QUALIDADE_INTRANET_SOURCE_ENABLED + hybrid/intranet e rodar "
            "python manage.py sync_qualidade_intranet para backfill idempotente."
        )

        if options["sync"]:
            from django.core.management import call_command

            call_command("sync_qualidade_intranet")
