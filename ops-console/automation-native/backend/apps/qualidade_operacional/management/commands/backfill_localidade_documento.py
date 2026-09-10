# -*- coding: utf-8 -*-
"""Backfill de localidade_documento (UF do documento) nos fatos EO."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeIntranetProjection,
)
from apps.qualidade_operacional.services.filter_catalog import refresh_filter_catalog
from apps.qualidade_operacional.services.localidade_documento import (
    normalize_localidade_documento,
    resolve_localidade_documento,
)


def _resolve_falha_localidade_documento(
    falha: QualidadeFalha,
    *,
    source_uf_by_falha_id: dict[int, str],
) -> str:
    from_uf = resolve_localidade_documento(uf=falha.uf)
    if from_uf:
        return from_uf
    return normalize_localidade_documento(source_uf_by_falha_id.get(falha.id))


def _resolve_auditado_localidade_documento(
    auditado: QualidadeAuditado,
    *,
    source_uf_by_auditado_id: dict[int, str],
    falha_uf_by_protocolo: dict[str, str],
) -> str:
    from_source = normalize_localidade_documento(
        source_uf_by_auditado_id.get(auditado.id)
    )
    if from_source:
        return from_source
    protocolo = (auditado.protocolo or "").strip()
    if protocolo:
        sibling = falha_uf_by_protocolo.get(protocolo)
        if sibling:
            return sibling
    return ""


def _old_auditado_ids_for_localidade(localidade: str) -> set[int]:
    mats_qs = (
        QualidadeFalha.objects.exclude(matricula="")
        .filter(localidade__iexact=localidade)
        .values_list("matricula", flat=True)
        .distinct()
    )
    if not mats_qs:
        return set()
    return set(
        QualidadeAuditado.objects.filter(matricula__in=mats_qs).values_list("id", flat=True)
    )


def _new_auditado_ids_for_localidade(localidade: str) -> set[int]:
    return set(
        QualidadeAuditado.objects.filter(localidade_documento__iexact=localidade).values_list(
            "id", flat=True
        )
    )


class Command(BaseCommand):
    help = (
        "Preenche localidade_documento (UF do documento) em qualidade_auditado e "
        "qualidade_falha, com relatório de divergências vs. filtro antigo por localidade do agente."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente calcula e reporta (padrão se --apply não for passado).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persiste localidade_documento e atualiza catálogo de filtros.",
        )
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--json-out",
            type=str,
            default="",
            help="Caminho opcional para salvar o relatório em JSON.",
        )

    def handle(self, *args, **options) -> None:
        apply = bool(options["apply"])
        dry_run = not apply
        batch_size = max(1, int(options["batch_size"] or 500))

        source_uf_by_falha_id: dict[int, str] = {}
        source_uf_by_auditado_id: dict[int, str] = {}
        for row in QualidadeIntranetProjection.objects.select_related("source").values(
            "falha_id",
            "auditado_id",
            "source__uf_documento",
        ):
            uf = row.get("source__uf_documento") or ""
            if row.get("falha_id"):
                source_uf_by_falha_id[int(row["falha_id"])] = uf
            if row.get("auditado_id"):
                source_uf_by_auditado_id[int(row["auditado_id"])] = uf

        falha_uf_by_protocolo: dict[str, str] = {}
        for protocolo, uf, loc_doc in QualidadeFalha.objects.exclude(protocolo="").values_list(
            "protocolo", "uf", "localidade_documento"
        ):
            value = resolve_localidade_documento(uf=uf or "", source_uf=loc_doc or "")
            if value:
                falha_uf_by_protocolo[str(protocolo).strip()] = value

        report: dict[str, object] = {
            "dry_run": dry_run,
            "falhas_atualizadas": 0,
            "auditados_atualizados": 0,
            "falhas_uf_vazia": 0,
            "divergencia_filtro_antigo": [],
            "auditados_ganho_perda": {},
        }

        falhas_to_update: list[QualidadeFalha] = []
        for falha in QualidadeFalha.objects.iterator(chunk_size=batch_size):
            target = _resolve_falha_localidade_documento(
                falha,
                source_uf_by_falha_id=source_uf_by_falha_id,
            )
            if not target:
                report["falhas_uf_vazia"] = int(report["falhas_uf_vazia"]) + 1  # type: ignore[operator]
            if falha.localidade_documento != target:
                falha.localidade_documento = target
                falhas_to_update.append(falha)
                report["falhas_atualizadas"] = int(report["falhas_atualizadas"]) + 1  # type: ignore[operator]
                protocolo = (falha.protocolo or "").strip()
                if protocolo and target:
                    falha_uf_by_protocolo[protocolo] = target

        auditados_to_update: list[QualidadeAuditado] = []
        for auditado in QualidadeAuditado.objects.iterator(chunk_size=batch_size):
            target = _resolve_auditado_localidade_documento(
                auditado,
                source_uf_by_auditado_id=source_uf_by_auditado_id,
                falha_uf_by_protocolo=falha_uf_by_protocolo,
            )
            if auditado.localidade_documento != target:
                auditado.localidade_documento = target
                auditados_to_update.append(auditado)
                report["auditados_atualizados"] = int(report["auditados_atualizados"]) + 1  # type: ignore[operator]

        divergencias: list[dict[str, str]] = []
        sample_seen = 0
        for falha in QualidadeFalha.objects.exclude(
            Q(localidade="") | Q(localidade_documento="")
        ).iterator(chunk_size=batch_size):
            agent_loc = (falha.localidade or "").strip()
            doc_loc = (falha.localidade_documento or "").strip()
            if not agent_loc or not doc_loc:
                continue
            if agent_loc.casefold() == doc_loc.casefold():
                continue
            if sample_seen >= 20:
                break
            divergencias.append(
                {
                    "protocolo": falha.protocolo or "",
                    "localidade_agente": agent_loc,
                    "localidade_documento": doc_loc,
                    "matricula": falha.matricula or "",
                }
            )
            sample_seen += 1
        report["divergencia_filtro_antigo"] = divergencias

        ganho_perda: dict[str, dict[str, int]] = defaultdict(lambda: {"ganho": 0, "perda": 0})
        localidades = sorted(
            {
                value
                for value in QualidadeFalha.objects.exclude(localidade_documento="")
                .values_list("localidade_documento", flat=True)
                .distinct()
            }
            | {
                value
                for value in QualidadeFalha.objects.exclude(localidade="")
                .values_list("localidade", flat=True)
                .distinct()
            }
        )
        for loc in localidades[:50]:
            old_ids = _old_auditado_ids_for_localidade(loc)
            new_ids = _new_auditado_ids_for_localidade(loc)
            ganho = len(new_ids - old_ids)
            perda = len(old_ids - new_ids)
            if ganho or perda:
                ganho_perda[loc] = {"ganho": ganho, "perda": perda}
        report["auditados_ganho_perda"] = dict(ganho_perda)

        self.stdout.write(
            self.style.NOTICE(
                f"Modo: {'dry-run' if dry_run else 'apply'} | "
                f"falhas_atualizadas={report['falhas_atualizadas']} | "
                f"auditados_atualizados={report['auditados_atualizados']} | "
                f"falhas_uf_vazia={report['falhas_uf_vazia']}"
            )
        )
        self.stdout.write(
            f"Divergências agente×documento (amostra): {len(divergencias)}"
        )
        for item in divergencias[:5]:
            self.stdout.write(
                f"  - {item['protocolo']}: agente={item['localidade_agente']} "
                f"documento={item['localidade_documento']}"
            )
        if ganho_perda:
            self.stdout.write("Auditados ganho/perda vs. subquery antiga (por valor):")
            for loc, counts in sorted(ganho_perda.items())[:10]:
                self.stdout.write(
                    f"  - {loc}: +{counts['ganho']} / -{counts['perda']}"
                )

        json_out = (options.get("json_out") or "").strip()
        if json_out:
            path = Path(json_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Relatório JSON: {path}"))

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run: nenhuma alteração persistida."))
            return

        with transaction.atomic():
            if falhas_to_update:
                QualidadeFalha.objects.bulk_update(
                    falhas_to_update,
                    ["localidade_documento"],
                    batch_size=batch_size,
                )
            if auditados_to_update:
                QualidadeAuditado.objects.bulk_update(
                    auditados_to_update,
                    ["localidade_documento"],
                    batch_size=batch_size,
                )
        refresh_filter_catalog()
        self.stdout.write(self.style.SUCCESS("Backfill concluído e catálogo atualizado."))
