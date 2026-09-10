# -*- coding: utf-8 -*-
"""Gates de retenção segura — artefatos só removíveis após ingestão comprovada."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.common.models import BotDataIngestion, BotDbSyncJob
from apps.replicacao_d1.models import ReplicacaoD1Protocolo, ReplicacaoD1Run

_TERMINAL_RUN_STATUSES = frozenset(
    {
        ReplicacaoD1Run.STATUS_COMPLETED,
        ReplicacaoD1Run.STATUS_PARTIAL,
        ReplicacaoD1Run.STATUS_FAILED,
        ReplicacaoD1Run.STATUS_CANCELLED,
    }
)
_SUCCESS_INGESTION = frozenset(
    {
        BotDataIngestion.STATUS_COMPLETED,
        BotDataIngestion.STATUS_PARTIAL,
    }
)


@dataclass
class RetentionGateResult:
    run_id: str
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "checks": dict(self.checks),
        }


def _latest_plano_ingestion(run_id: str) -> BotDataIngestion | None:
    return (
        BotDataIngestion.objects.filter(
            domain="replicacao_d1",
            kind="plano",
            run_id=run_id,
        )
        .select_related("artifact")
        .order_by("-finished_at", "-started_at", "-id")
        .first()
    )


def _has_active_sync_job(run_id: str) -> bool:
    return BotDbSyncJob.objects.filter(
        domain=BotDbSyncJob.DOMAIN_REPLICACAO_D1,
        status__in=(BotDbSyncJob.STATUS_PENDING, BotDbSyncJob.STATUS_RUNNING),
    ).filter(Q(report_type=run_id) | Q(source_path__icontains=run_id)).exists()


def evaluate_run_retention_gate(
    run_id: str,
    *,
    min_age_days: int = 0,
    require_ingestion: bool = True,
    skip_job_check: bool = False,
) -> RetentionGateResult:
    """Avalia se arquivos-fonte de um run podem ser removidos com segurança."""
    rid = str(run_id or "").strip()
    reasons: list[str] = []
    checks: dict[str, bool] = {}

    if not rid:
        return RetentionGateResult(
            run_id="",
            allowed=False,
            reasons=["run_id vazio"],
            checks={"run_id_present": False},
        )

    ing = _latest_plano_ingestion(rid)
    checks["ingestion_exists"] = ing is not None
    if require_ingestion and not ing:
        reasons.append("sem ingestão registrada no banco")

    if ing:
        checks["ingestion_success"] = (
            ing.status in _SUCCESS_INGESTION and int(ing.rows_loaded or 0) > 0
        )
        if not checks["ingestion_success"]:
            reasons.append(f"ingestão não concluída com carga ({ing.status})")

        artifact = ing.artifact
        checks["artifact_registered"] = artifact is not None
        checks["artifact_hash"] = bool(artifact and artifact.content_sha256)
        if not checks["artifact_registered"]:
            reasons.append("artefato lógico ausente")
        elif not checks["artifact_hash"]:
            reasons.append("artefato sem hash SHA-256")

        prot_count = ReplicacaoD1Protocolo.objects.filter(run_id=rid).count()
        checks["counts_reconciled"] = prot_count == 0 or int(ing.rows_loaded or 0) >= prot_count
        if not checks["counts_reconciled"]:
            reasons.append("contagens de ingestão abaixo dos protocolos persistidos")
    else:
        checks["ingestion_success"] = not require_ingestion
        checks["artifact_registered"] = not require_ingestion
        checks["artifact_hash"] = not require_ingestion
        checks["counts_reconciled"] = True

    run = ReplicacaoD1Run.objects.filter(run_id=rid).first()
    if run:
        checks["run_closed"] = run.status_canonical in _TERMINAL_RUN_STATUSES
        if not checks["run_closed"]:
            reasons.append(f"run não fechado ({run.status_canonical})")
        ref_dt = run.finished_at or run.synced_at or run.created_at
    else:
        checks["run_closed"] = not require_ingestion
        ref_dt = ing.finished_at if ing else None
        if require_ingestion:
            reasons.append("run ausente no banco")

    if min_age_days > 0:
        if ref_dt:
            cutoff = timezone.now() - timedelta(days=min_age_days)
            checks["retention_elapsed"] = ref_dt <= cutoff
        else:
            checks["retention_elapsed"] = False
        if not checks.get("retention_elapsed"):
            reasons.append("prazo mínimo de retenção não vencido")
    else:
        checks["retention_elapsed"] = True

    if skip_job_check:
        checks["no_active_job"] = True
    else:
        checks["no_active_job"] = not _has_active_sync_job(rid)
        if not checks["no_active_job"]:
            reasons.append("job de sync ativo associado ao run")

    allowed = not reasons and all(checks.values())
    return RetentionGateResult(run_id=rid, allowed=allowed, reasons=reasons, checks=checks)


def list_retention_candidates(
    run_ids: list[str],
    *,
    min_age_days: int = 0,
    require_ingestion: bool = True,
) -> list[RetentionGateResult]:
    return [
        evaluate_run_retention_gate(
            rid,
            min_age_days=min_age_days,
            require_ingestion=require_ingestion,
        )
        for rid in run_ids
    ]


def ingestion_eligible_for_purge(ingestion: BotDataIngestion) -> bool:
    """Lote técnico removível sem apagar fatos operacionais."""
    if ingestion.status == BotDataIngestion.STATUS_PROCESSING:
        return False
    if ingestion.status not in _SUCCESS_INGESTION:
        return True
    artifact = ingestion.artifact
    if artifact is None:
        return True
    if not artifact.content_sha256:
        return False
    if ingestion.domain == "replicacao_d1" and ingestion.kind == "plano" and ingestion.run_id:
        gate = evaluate_run_retention_gate(
            ingestion.run_id,
            require_ingestion=True,
            skip_job_check=True,
        )
        return gate.allowed
    return True
