# -*- coding: utf-8 -*-
"""Seed do perfil sintético 355×344 para validação EO Intranet."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.auditoria.models import AuditoriaFalhaCadastro, AuditoriaMotivoFalha
from apps.auditoria.testing.qualidade_intranet_acceptance_profile import (
    CLIENTE,
    SEED_PREFIX,
    WORKFLOW,
    build_acceptance_rows,
    motivo_catalog_rows,
)
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.services.intranet_source import sync_queryset
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

User = get_user_model()


class Command(BaseCommand):
    help = "Carrega perfil sintético 355×344 (EO-ACC-*) e sincroniza projeção Intranet."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-sync", action="store_true")

    def handle(self, *args, **options) -> None:
        user = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if user is None:
            user = User.objects.create_user("eo_acc_seed", password="unused")

        DimCliente.objects.get_or_create(id_cliente=9100, defaults={"nome": CLIENTE})
        DimWorkflow.objects.get_or_create(id_workflow=9100, defaults={"nome": WORKFLOW})

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

        AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=SEED_PREFIX).delete()

        created = 0
        for sample in build_acceptance_rows():
            AuditoriaFalhaCadastro.objects.create(**sample, created_by=user)
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Criados {created} registros {SEED_PREFIX}*"))

        if options["skip_sync"]:
            return

        qs = AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=SEED_PREFIX)
        report = sync_queryset(
            qs.select_related("atividade", "created_by"),
            dry_run=bool(options["dry_run"]),
            force=True,
        )
        self.stdout.write(f"sync: {report.as_dict()}")

        from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha

        aud = QualidadeAuditado.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=SEED_PREFIX,
        ).count()
        fal = QualidadeFalha.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=SEED_PREFIX,
        ).count()
        self.stdout.write(f"projetados: auditados={aud} falhas={fal}")
