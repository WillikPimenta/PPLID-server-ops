from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Prefetch

from apps.auditoria.models import (
    AuditoriaAtividadeProtocolo,
    AuditoriaComplianceFilaHistorico,
    AuditoriaFalhaCadastro,
    ReinspecaoFilaHistorico,
)
from apps.workforce.models import UserProfile


@dataclass(frozen=True)
class Resolution:
    status: str
    strategy: str
    user: object | None = None
    reason: str = ""


class Command(BaseCommand):
    help = (
        "Resolve conservadoramente o auditor finalizador de auditoria_falha_cadastro. "
        "O modo padrão é somente leitura."
    )

    REPORT_FIELDS = (
        "falha_id",
        "origem",
        "status",
        "strategy",
        "candidate_user_id",
        "candidate_username",
        "reason",
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente calcula e relata; é também o comportamento padrão.",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Persiste somente resoluções inequívocas em campos ainda nulos.",
        )
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--report-csv",
            default="",
            help="Caminho opcional para relatório CSV linha a linha.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size deve ser maior que zero.")

        apply = bool(options["apply"])
        report_path = Path(options["report_csv"]).expanduser() if options["report_csv"] else None
        report_file = None
        writer = None
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_file = report_path.open("w", encoding="utf-8-sig", newline="")
            writer = csv.DictWriter(report_file, fieldnames=self.REPORT_FIELDS)
            writer.writeheader()

        totals = Counter()
        by_origin: dict[str, Counter] = defaultdict(Counter)
        by_strategy = Counter()
        last_pk = 0

        try:
            while True:
                with transaction.atomic():
                    qs = self._base_queryset(apply=apply).filter(pk__gt=last_pk).order_by("pk")
                    batch = list(qs[:batch_size])
                    if not batch:
                        break

                    for falha in batch:
                        last_pk = falha.pk
                        totals["examined"] += 1
                        origem = self._origem(falha)
                        try:
                            # Savepoint por linha: um erro não invalida o lote inteiro.
                            with transaction.atomic():
                                resolution = self._resolve(falha)
                                if resolution.status == "resolved" and apply:
                                    changed = AuditoriaFalhaCadastro.objects.filter(
                                        pk=falha.pk,
                                        auditor_responsavel__isnull=True,
                                    ).update(auditor_responsavel=resolution.user)
                                    if changed:
                                        totals["applied"] += 1
                                    else:
                                        resolution = Resolution(
                                            "already_filled",
                                            "concurrent_existing_value",
                                            reason="O campo foi preenchido durante a execução.",
                                        )
                        except Exception as exc:  # noqa: BLE001 - relatório deve seguir auditável
                            resolution = Resolution("error", "error", reason=str(exc))

                        totals[resolution.status] += 1
                        by_origin[origem][resolution.status] += 1
                        by_strategy[resolution.strategy] += 1
                        if writer:
                            writer.writerow(
                                {
                                    "falha_id": falha.pk,
                                    "origem": origem,
                                    "status": resolution.status,
                                    "strategy": resolution.strategy,
                                    "candidate_user_id": getattr(resolution.user, "pk", "") or "",
                                    "candidate_username": getattr(
                                        resolution.user, "username", ""
                                    )
                                    or "",
                                    "reason": resolution.reason,
                                }
                            )
                    if report_file:
                        report_file.flush()
        finally:
            if report_file:
                report_file.close()

        summary = {
            "mode": "apply" if apply else "dry-run",
            "examined": totals["examined"],
            "already_filled": totals["already_filled"],
            "resolved": totals["resolved"],
            "applied": totals["applied"],
            "unresolved": totals["unresolved"],
            "ambiguous": totals["ambiguous"],
            "ignored": totals["ignored"],
            "errors": totals["error"],
            "by_origin": {key: dict(value) for key, value in sorted(by_origin.items())},
            "by_strategy": dict(sorted(by_strategy.items())),
            "report_csv": str(report_path.resolve()) if report_path else "",
        }
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))

    @staticmethod
    def _base_queryset(*, apply: bool):
        historico_conclusao = ReinspecaoFilaHistorico.objects.filter(
            tipo=ReinspecaoFilaHistorico.TIPO_CONCLUSAO,
            actor__isnull=False,
        ).select_related("actor")
        compliance_historico_conclusao = AuditoriaComplianceFilaHistorico.objects.filter(
            tipo=AuditoriaComplianceFilaHistorico.TIPO_CONCLUSAO,
            actor__isnull=False,
        ).select_related("actor")
        qs = AuditoriaFalhaCadastro.objects.select_related(
            "auditor_responsavel",
            "created_by",
            "protocolo_origem__analisado_por",
            "analise_origem",
        ).prefetch_related(
            Prefetch(
                "reinspecao_historico",
                queryset=historico_conclusao,
                to_attr="historicos_conclusao_com_actor",
            ),
            Prefetch(
                "auditoria_compliance_historico",
                queryset=compliance_historico_conclusao,
                to_attr="compliance_historicos_conclusao_com_actor",
            ),
        )
        return qs.select_for_update(of=("self",)) if apply else qs

    @staticmethod
    def _origem(falha: AuditoriaFalhaCadastro) -> str:
        return (falha.origem or falha.tipo_registro or "nao_informada").strip().lower()

    def _resolve(self, falha: AuditoriaFalhaCadastro) -> Resolution:
        if falha.auditor_responsavel_id:
            return Resolution("already_filled", "existing_fk", falha.auditor_responsavel)

        origem = self._origem(falha)
        if origem == AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO:
            resolution = self._from_contestacao_tratada(falha)
            if resolution:
                return resolution

        if origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO:
            resolution = self._from_reinspecao_history(falha)
            if resolution:
                return resolution

        resolution = self._from_direct_same_operation(falha)
        if resolution:
            return resolution

        resolution = self._from_legacy_exact_identifier(falha)
        if resolution:
            return resolution

        if self._is_known_import(falha):
            return Resolution(
                "ignored",
                "trusted_source_without_explicit_auditor",
                reason="Importação identificada sem auditor explícito e inequívoco.",
            )

        return Resolution(
            "unresolved",
            "insufficient_evidence",
            reason="Nenhuma evidência inequívoca do usuário que finalizou a análise.",
        )

    @staticmethod
    def _from_contestacao_tratada(falha):
        protocolo = falha.protocolo_origem
        if (
            protocolo
            and protocolo.status == AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
            and protocolo.finalizado_em
            and protocolo.analisado_por_id
        ):
            return Resolution(
                "resolved",
                "contestacao_protocolo_finalizador",
                protocolo.analisado_por,
            )
        return None

    @staticmethod
    def _from_reinspecao_history(falha):
        actors = {
            item.actor_id: item.actor
            for item in (
                *getattr(falha, "historicos_conclusao_com_actor", []),
                *getattr(falha, "compliance_historicos_conclusao_com_actor", []),
            )
            if item.actor_id
        }
        if len(actors) == 1:
            return Resolution(
                "resolved",
                "reinspecao_historico_conclusao_actor",
                next(iter(actors.values())),
            )
        if len(actors) > 1:
            return Resolution(
                "ambiguous",
                "reinspecao_multiplos_finalizadores",
                reason="Mais de um actor distinto consta no histórico de conclusão.",
            )
        return None

    @staticmethod
    def _from_direct_same_operation(falha):
        if not falha.created_by_id or not falha.data_resposta or not falha.analise_concluida_em:
            return None
        if any(
            (
                falha.atividade_id,
                falha.protocolo_origem_id,
                falha.etapa_origem_id,
                falha.etapa_chave,
            )
        ):
            return None
        if falha.data_resposta != falha.analise_concluida_em:
            return None
        if abs((falha.created_at - falha.data_resposta).total_seconds()) > 5:
            return None
        if Command._is_known_import(falha):
            return None
        return Resolution(
            "resolved",
            "cadastro_direto_mesma_operacao",
            falha.created_by,
        )

    @staticmethod
    def _from_legacy_exact_identifier(falha):
        identifier = (falha.auditor or "").strip()
        if not identifier:
            return None

        User = get_user_model()
        candidates = {user.pk: user for user in User.objects.filter(username=identifier)}
        for profile in UserProfile.objects.select_related("user").filter(
            agent__user_lan_id=identifier
        ):
            candidates[profile.user_id] = profile.user

        if len(candidates) == 1:
            return Resolution(
                "resolved",
                "auditor_legado_identificador_exato",
                next(iter(candidates.values())),
            )
        if len(candidates) > 1:
            return Resolution(
                "ambiguous",
                "auditor_legado_identificador_ambiguo",
                reason="O identificador exato aponta para mais de um usuário.",
            )
        return None

    @staticmethod
    def _is_known_import(falha) -> bool:
        payloads = [falha.brflow_parsed]
        if falha.analise_origem_id:
            payloads.extend(
                [
                    falha.analise_origem.brflow_parsed,
                    falha.analise_origem.trilha_parsed,
                    falha.analise_origem.contexto,
                ]
            )
        import_keys = {
            "homolog_import_source",
            "homolog_import_row_id",
            "source_file",
            "source_marker",
            "export_id",
        }
        return any(
            isinstance(payload, dict) and import_keys.intersection(payload)
            for payload in payloads
        )
