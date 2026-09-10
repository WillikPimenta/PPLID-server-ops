# -*- coding: utf-8 -*-
"""Fila de sync bot→banco: enqueue + claim + process + spawn drain (high/low)."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from apps.common.bot_db_sync_lanes import (
    LANE_HIGH,
    LANE_MID,
    LANES,
    normalize_lane,
    resolve_lane,
)
from apps.common.models import BotDbSyncJob

log = logging.getLogger(__name__)

_ACTIVE = (BotDbSyncJob.STATUS_PENDING, BotDbSyncJob.STATUS_RUNNING)
_drain_lock_handles: dict[str, BinaryIO] = {}
# Evita storm de Popen quando hooks/enqueue disparam spawn em rajada.
_SPAWN_COOLDOWN_SEC = 30.0
_last_spawn_at: dict[str, float] = {}
_last_spawn_cooldown_sec: dict[str, float] = {}
_PERMANENT_FAIL_MARKERS = (
    "permissionerror",
    "acesso negado",
    "permission denied",
    "winerror 5",
    "memoryerror",
    "unable to allocate",
    "cannot allocate memory",
)
# Alias compat (testes / imports antigos).
_PERM_FAIL_MARKERS = _PERMANENT_FAIL_MARKERS


def _drain_lock_path(lane: str) -> Path:
    base = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    return base / "tmp" / f"bot_db_sync_drain_{normalize_lane(lane)}.lock"


def _drain_log_path() -> Path:
    base = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    return base / "tmp" / "drain.log"


def _stale_minutes() -> int:
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    return max(1, int(get_bot_db_sync_runtime_config().stale_minutes))


def _lane_concurrency(lane: str | None) -> int:
    if lane is None:
        return 1
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    cfg = get_bot_db_sync_runtime_config()
    normalized = normalize_lane(lane)
    return max(
        1,
        int(
            cfg.high_concurrency
            if normalized == LANE_HIGH
            else cfg.mid_concurrency
            if normalized == LANE_MID
            else cfg.low_concurrency
        ),
    )


def _stale_max_requeue() -> int:
    return max(0, int(getattr(settings, "BOT_DB_SYNC_STALE_MAX_REQUEUE", 2)))


def _is_permanent_failure(message: str | None) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in _PERMANENT_FAIL_MARKERS)


def _is_permanent_permission_failure(message: str | None) -> bool:
    """Compat: inclui OOM/permissão (não recircula)."""
    return _is_permanent_failure(message)


def _is_oom_failure(message: str | None) -> bool:
    text = (message or "").lower()
    return any(
        marker in text
        for marker in (
            "memoryerror",
            "unable to allocate",
            "cannot allocate memory",
        )
    )


def _oom_spawn_cooldown_sec() -> float:
    return max(0.0, float(getattr(settings, "BOT_DB_SYNC_OOM_SPAWN_COOLDOWN_SEC", 300)))


def _mark_spawn_cooldown(lane: str, *, seconds: float | None = None) -> None:
    import time as _time

    lane = normalize_lane(lane)
    cooldown = float(seconds if seconds is not None else _SPAWN_COOLDOWN_SEC)
    _last_spawn_at[lane] = _time.monotonic()
    _last_spawn_cooldown_sec[lane] = cooldown


def _spawn_cooldown_for(lane: str) -> float:
    return float(_last_spawn_cooldown_sec.get(lane) or _SPAWN_COOLDOWN_SEC)


def _requeue_or_fail_running_job(job: BotDbSyncJob, *, reason: str) -> str:
    """Requeue → pending (se attempts ok) ou failed. Retorna 'requeued'|'failed'."""
    attempts = int(job.attempts or 0)
    max_requeue = _stale_max_requeue()
    now = timezone.now()
    # OneDrive/OOM: não recircula a fila.
    if _is_permanent_failure(job.message) or _is_permanent_failure(reason):
        kind = "memória" if _is_oom_failure(job.message) or _is_oom_failure(reason) else (
            "permissão/arquivo bloqueado"
        )
        job.status = BotDbSyncJob.STATUS_FAILED
        job.finished_at = now
        job.message = f"Falhou ({kind}): {reason}"[:2000]
        job.save(update_fields=["status", "finished_at", "message"])
        log.warning(
            "bot-db-sync permanent-fail job_id=%s reason=%s",
            job.pk,
            reason,
        )
        return "failed"
    if attempts <= max_requeue:
        job.status = BotDbSyncJob.STATUS_PENDING
        job.started_at = None
        job.finished_at = None
        job.message = f"Reenfileirado: {reason} (attempts={attempts})."[:2000]
        job.save(update_fields=["status", "started_at", "finished_at", "message"])
        log.warning(
            "bot-db-sync requeue job_id=%s attempts=%s reason=%s",
            job.pk,
            attempts,
            reason,
        )
        return "requeued"
    job.status = BotDbSyncJob.STATUS_FAILED
    job.finished_at = now
    job.message = f"Falhou: {reason} após {attempts} tentativas."[:2000]
    job.save(update_fields=["status", "finished_at", "message"])
    log.warning(
        "bot-db-sync failed job_id=%s attempts=%s reason=%s",
        job.pk,
        attempts,
        reason,
    )
    return "failed"


def recover_stale_running_jobs() -> dict:
    """
    Jobs `running` sem progresso há BOT_DB_SYNC_STALE_MINUTES:
    - requeue → pending (se attempts <= max requeue)
    - senão → failed
    """
    cutoff = timezone.now() - timedelta(minutes=_stale_minutes())
    stale_qs = BotDbSyncJob.objects.filter(
        status=BotDbSyncJob.STATUS_RUNNING,
        started_at__isnull=False,
        started_at__lt=cutoff,
    )
    requeued = 0
    failed = 0
    reason = f"stuck em running > {_stale_minutes()} min"
    for job in stale_qs.iterator():
        outcome = _requeue_or_fail_running_job(job, reason=reason)
        if outcome == "requeued":
            requeued += 1
        else:
            failed += 1
    return {"requeued": requeued, "failed": failed}


def recover_orphan_running_jobs_for_lane(lane: str) -> dict:
    """
    Caller detém (ou acabou de obter) o lock exclusivo do drain nesta lane:
    qualquer job ainda `running` nesta lane é órfão (processo anterior morreu).
    """
    lane = normalize_lane(lane)
    qs = BotDbSyncJob.objects.filter(
        status=BotDbSyncJob.STATUS_RUNNING,
        lane=lane,
    )
    requeued = 0
    failed = 0
    reason = f"órfão em running (drain {lane} sem processo ativo)"
    for job in qs.iterator():
        outcome = _requeue_or_fail_running_job(job, reason=reason)
        if outcome == "requeued":
            requeued += 1
        else:
            failed += 1
    return {"requeued": requeued, "failed": failed}


def reconcile_bot_db_sync_jobs(*, spawn_if_requeued: bool = False) -> dict:
    """
    Validação contínua: órfãos (lock do drain livre) + stale por idade.
    Chamar no poll da UI e no início do drain.
    """
    requeued = 0
    failed = 0
    orphan_requeued = 0
    orphan_failed = 0

    for lane in LANES:
        if not acquire_drain_process_lock(lane):
            continue
        try:
            # Lock livre → não há drain; running nesta lane é fantasma.
            result = recover_orphan_running_jobs_for_lane(lane)
            orphan_requeued += result["requeued"]
            orphan_failed += result["failed"]
        finally:
            release_drain_process_lock(lane)

    stale = recover_stale_running_jobs()
    from apps.common.bot_db_sync_gate import reconcile_stale_bot_db_sync_locks

    lock_recovery = reconcile_stale_bot_db_sync_locks()
    requeued = orphan_requeued + stale["requeued"]
    failed = orphan_failed + stale["failed"]

    if spawn_if_requeued and requeued:
        spawn_drain_worker()

    return {
        "requeued": requeued,
        "failed": failed,
        "orphan_requeued": orphan_requeued,
        "orphan_failed": orphan_failed,
        "stale_requeued": stale["requeued"],
        "stale_failed": stale["failed"],
        "lock_recovered": lock_recovery["recovered"],
        "lock_ignored": lock_recovery["ignored"],
        "lock_missing": lock_recovery["missing"],
    }


def acquire_drain_process_lock(lane: str = LANE_HIGH) -> bool:
    """Lock exclusivo do processo drain por lane (high, mid e low independentes)."""
    lane = normalize_lane(lane)
    if lane in _drain_lock_handles:
        return True

    path = _drain_lock_path(lane)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(f"{os.getpid()}\n".encode("ascii", errors="ignore"))
        handle.flush()
        _drain_lock_handles[lane] = handle
        return True
    except OSError:
        handle.close()
        return False


def release_drain_process_lock(lane: str = LANE_HIGH) -> None:
    lane = normalize_lane(lane)
    handle = _drain_lock_handles.pop(lane, None)
    if handle is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def enqueue_bot_db_sync(
    *,
    domain: str,
    source_path: str = "",
    report_type: str = "",
    force: bool = True,
    spawn: bool = True,
) -> tuple[BotDbSyncJob, bool]:
    """
    Enfileira sync. Retorna (job, created).
    Se já houver pending/running equivalente, reutiliza e não duplica.
    """
    source_path = (source_path or "").strip()
    report_type = (report_type or "").strip()
    lane = resolve_lane(domain, report_type)

    existing = (
        BotDbSyncJob.objects.filter(
            status__in=_ACTIVE,
            domain=domain,
            report_type=report_type,
            source_path=source_path,
        )
        .order_by("id")
        .first()
    )
    if existing:
        if spawn:
            spawn_drain_worker()
        return existing, False

    job = BotDbSyncJob.objects.create(
        domain=domain,
        report_type=report_type,
        source_path=source_path,
        force=bool(force),
        lane=lane,
        status=BotDbSyncJob.STATUS_PENDING,
    )
    log.info(
        "bot-db-sync enqueue job_id=%s lane=%s domain=%s report_type=%s path=%s",
        job.pk,
        lane,
        domain,
        report_type or "-",
        source_path or "-",
    )
    if spawn:
        spawn_drain_worker()
    return job, True


def _drain_lock_held(lane: str) -> bool:
    """True se outro processo já detém o lock exclusivo da lane."""
    lane = normalize_lane(lane)
    if lane in _drain_lock_handles:
        return True
    path = _drain_lock_path(lane)
    if not path.is_file():
        return False
    try:
        handle = open(path, "a+b")
    except OSError:
        return False
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return False
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            handle.close()
        except OSError:
            pass


def spawn_drain_worker(*, max_jobs: int | None = None, lane: str | None = None) -> bool:
    """
    Dispara `manage.py drain_bot_db_sync --lane …` em subprocesso(s).
    Sem `lane`, sobe high, mid e low (cada um sai se o lock da sua fila já estiver preso).
    Stdout/stderr vão para backend/tmp/drain.log.
    """
    import time as _time

    if max_jobs is None:
        from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

        max_jobs = get_bot_db_sync_runtime_config().drain_max_jobs
    max_jobs = max(1, int(max_jobs))

    if lane is None:
        spawned = [
            spawn_drain_worker(max_jobs=max_jobs, lane=lane_value)
            for lane_value in LANES
        ]
        return any(spawned)

    lane = normalize_lane(lane)
    now = _time.monotonic()
    last = float(_last_spawn_at.get(lane) or 0.0)
    cooldown = _spawn_cooldown_for(lane)
    if last and (now - last) < cooldown:
        log.info(
            "bot-db-sync spawn: skip lane=%s (cooldown %.0fs)",
            lane,
            cooldown - (now - last),
        )
        return False
    if _drain_lock_held(lane):
        log.info("bot-db-sync spawn: skip lane=%s (drain já ativo)", lane)
        return False
    if lane == LANE_HIGH:
        from apps.common.bot_db_sync_memory import memory_ok_for_heavy_sync

        if not memory_ok_for_heavy_sync():
            log.warning("bot-db-sync spawn: skip lane=high (RAM insuficiente)")
            return False

    manage_py = Path(settings.BASE_DIR) / "manage.py"
    if not manage_py.is_file():
        log.error("bot-db-sync spawn: manage.py não encontrado em %s", manage_py)
        return False

    cmd = [
        sys.executable,
        str(manage_py),
        "drain_bot_db_sync",
        "--lane",
        lane,
        "--max",
        str(max_jobs),
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # Novo process group: stop do Waitress não derruba drains em andamento.
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    log_path = _drain_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
    except OSError:
        log.exception("bot-db-sync spawn: não foi possível abrir %s", log_path)
        return False

    try:
        log_file.write(
            f"\n--- drain spawn lane={lane} pid_parent={os.getpid()} max={max_jobs} "
            f"at={timezone.now().isoformat(timespec='seconds')} ---\n"
        )
        log_file.flush()
        subprocess.Popen(
            cmd,
            cwd=str(settings.BASE_DIR),
            env=os.environ.copy(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=sys.platform != "win32",
            start_new_session=True,
        )
        try:
            log_file.close()
        except OSError:
            pass
        _mark_spawn_cooldown(lane, seconds=_SPAWN_COOLDOWN_SEC)
        log.info(
            "bot-db-sync spawn: drain iniciado lane=%s max=%s log=%s",
            lane,
            max_jobs,
            log_path,
        )
        return True
    except Exception:
        try:
            log_file.close()
        except OSError:
            pass
        log.exception("bot-db-sync spawn: falha ao iniciar drain lane=%s", lane)
        return False


def claim_next_job(*, lane: str | None = None) -> BotDbSyncJob | None:
    """Pega o próximo pending (opcionalmente da lane) e marca running."""
    with transaction.atomic():
        qs = (
            BotDbSyncJob.objects.select_for_update(skip_locked=True)
            .filter(status=BotDbSyncJob.STATUS_PENDING)
            .order_by("created_at", "id")
        )
        if lane is not None:
            qs = qs.filter(lane=normalize_lane(lane))
        job = qs.first()
        if not job:
            return None
        job.status = BotDbSyncJob.STATUS_RUNNING
        job.started_at = timezone.now()
        job.attempts = (job.attempts or 0) + 1
        job.save(update_fields=["status", "started_at", "attempts"])
        return job


def process_job(job: BotDbSyncJob) -> BotDbSyncJob:
    """Executa o runner do domínio (passa pelo bot_db_sync_slot interno)."""
    try:
        success, message, skipped = _dispatch_job(job)
        if skipped:
            job.status = BotDbSyncJob.STATUS_SKIPPED
        elif success:
            job.status = BotDbSyncJob.STATUS_DONE
        else:
            job.status = BotDbSyncJob.STATUS_FAILED
            if _is_oom_failure(message):
                _mark_spawn_cooldown(
                    job.lane or LANE_HIGH, seconds=_oom_spawn_cooldown_sec()
                )
        job.message = (message or "")[:2000]
    except MemoryError as exc:
        log.exception("bot-db-sync process job_id=%s MemoryError", job.pk)
        job.status = BotDbSyncJob.STATUS_FAILED
        job.message = f"MemoryError: {exc}"[:2000]
        _mark_spawn_cooldown(job.lane or LANE_HIGH, seconds=_oom_spawn_cooldown_sec())
    except Exception as exc:
        log.exception("bot-db-sync process job_id=%s failed", job.pk)
        job.status = BotDbSyncJob.STATUS_FAILED
        msg = str(exc)[:2000]
        job.message = msg
        if _is_oom_failure(msg):
            _mark_spawn_cooldown(
                job.lane or LANE_HIGH, seconds=_oom_spawn_cooldown_sec()
            )
    if (
        job.domain == BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION
        and job.status == BotDbSyncJob.STATUS_FAILED
        and int(job.attempts or 0)
        < max(1, int(getattr(settings, "QUALIDADE_PROJECTION_MAX_ATTEMPTS", 3)))
    ):
        job.status = BotDbSyncJob.STATUS_PENDING
        job.started_at = None
        job.finished_at = None
        job.message = f"Nova tentativa agendada: {job.message}"[:2000]
        job.save(update_fields=["status", "started_at", "finished_at", "message"])
        return job

    job.finished_at = timezone.now()
    job.save(update_fields=["status", "message", "finished_at"])
    return job


def _dispatch_job(job: BotDbSyncJob) -> tuple[bool, str, bool]:
    path = job.source_path or None
    force = bool(job.force)

    if job.domain == BotDbSyncJob.DOMAIN_PRODUTIVIDADE:
        from apps.produtividade.models import ProductivitySyncLog
        from apps.produtividade.services.sync_runner import run_sync_with_audit

        success, sync_log, skipped = run_sync_with_audit(
            path=path,
            user=None,
            trigger_source=ProductivitySyncLog.TRIGGER_SYSTEM,
            force=force,
        )
        return success, sync_log.message, skipped

    if job.domain == BotDbSyncJob.DOMAIN_MONITOR_EVENTOS:
        from apps.monitor_eventos.models import MonitorEventoSyncLog
        from apps.monitor_eventos.services.sync_runner import run_sync_with_audit

        success, sync_log, skipped = run_sync_with_audit(
            path=path,
            user=None,
            trigger_source=MonitorEventoSyncLog.TRIGGER_SYSTEM,
            force=force,
        )
        return success, sync_log.message, skipped

    if job.domain == BotDbSyncJob.DOMAIN_ROTINA_BRUTO:
        from apps.automacoes.rotina_bruto_hooks import is_intermediate_rotina_bruto_file
        from apps.rotina_bruto.models import RotinaBrutoSyncLog
        from apps.rotina_bruto.services.sync_runner import run_sync_with_audit

        if not job.report_type:
            raise ValueError("rotina_bruto exige report_type")
        if is_intermediate_rotina_bruto_file(job.report_type, job.source_path or ""):
            return (
                True,
                f"Ignorado intermediário: {Path(job.source_path or '').name}",
                True,
            )
        success, sync_log, skipped = run_sync_with_audit(
            report_type=job.report_type,
            path=path,
            user=None,
            trigger_source=RotinaBrutoSyncLog.TRIGGER_SYSTEM,
            force=force,
        )
        # Após detalhado BRFlow, recalcula SLA útil (sem botão no portal).
        if (
            success
            and not skipped
            and str(job.report_type or "").strip().lower() == "detalhado"
        ):
            try:
                enqueue_bot_db_sync(
                    domain=BotDbSyncJob.DOMAIN_MONITORAMENTO_SLA,
                    report_type="90",
                    source_path="",
                    force=True,
                    spawn=True,
                )
            except Exception:
                log.exception("falha ao enfileirar monitoramento_sla após rotina detalhado")
        return success, sync_log.message, skipped

    if job.domain == BotDbSyncJob.DOMAIN_FALHAS_CRITICAS:
        from apps.falhas_criticas.models import SyncAuditLog
        from apps.falhas_criticas.services.sync_runner import run_sync_with_audit

        success, sync_log = run_sync_with_audit(
            path=path,
            user=None,
            trigger_source=SyncAuditLog.TRIGGER_SYSTEM,
        )
        return success, sync_log.message, False

    if job.domain == BotDbSyncJob.DOMAIN_REPLICACAO_D1:
        from apps.replicacao_d1.constants import REPORT_TYPE_REPLICADOS
        from apps.replicacao_d1.models import ReplicacaoD1SyncLog
        from apps.replicacao_d1.services.sync_runner import run_sync_with_audit

        report_type = (job.report_type or "").strip()
        if report_type == REPORT_TYPE_REPLICADOS:
            success, sync_log, skipped = run_sync_with_audit(
                path=path,
                user=None,
                trigger_source=ReplicacaoD1SyncLog.TRIGGER_SYSTEM,
                force=force,
                report_type=REPORT_TYPE_REPLICADOS,
                sync_job=job,
            )
        else:
            success, sync_log, skipped = run_sync_with_audit(
                path=path,
                user=None,
                trigger_source=ReplicacaoD1SyncLog.TRIGGER_SYSTEM,
                force=force,
                run_id=(report_type or None),
                sync_job=job,
            )
        return success, sync_log.message, skipped

    if job.domain == BotDbSyncJob.DOMAIN_PRODUTIVIDADE_CASE:
        from apps.produtividade_case.models import ProdutividadeCaseSyncLog
        from apps.produtividade_case.services.sync_runner import run_sync_with_audit

        report_type = (job.report_type or "").strip()
        if not report_type:
            raise ValueError("produtividade_case exige report_type")
        success, sync_log, skipped = run_sync_with_audit(
            report_type=report_type,
            path=path,
            user=None,
            trigger_source=ProdutividadeCaseSyncLog.TRIGGER_SYSTEM,
            force=force,
        )
        return success, sync_log.message, skipped

    if job.domain == BotDbSyncJob.DOMAIN_PRIORIDADES_NH:
        from apps.prioridades_nh.models import PrioridadesNhSyncLog
        from apps.prioridades_nh.services.sync_runner import run_sync_with_audit

        success, sync_log, skipped = run_sync_with_audit(
            path=path,
            user=None,
            trigger_source=PrioridadesNhSyncLog.TRIGGER_SYSTEM,
        )
        return success, sync_log.message, skipped

    if job.domain == BotDbSyncJob.DOMAIN_MONITORAMENTO_SLA:
        from apps.monitoramento_sla.services.sync_incremental import sync_monitoramento_sla

        days = 90
        if job.report_type and job.report_type.isdigit():
            days = int(job.report_type)
        run = sync_monitoramento_sla(days=days, user=None)
        ok = run.status == run.STATUS_OK
        return ok, run.message or run.status, False

    if job.domain == BotDbSyncJob.DOMAIN_REINSPECAO_GED:
        from apps.auditoria.services.reinspecao_ged_sync_runner import run_sync_with_audit

        success, message, skipped = run_sync_with_audit(path=path, force=force)
        return success, message, skipped

    if job.domain == BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION:
        from apps.auditoria.models import AuditoriaFalhaCadastro
        from apps.qualidade_operacional.services.intranet_source import sync_one
        from apps.qualidade_operacional.services.source_config import effective_source_active
        from apps.qualidade_operacional.signals import quality_projection_sync_guard

        if not effective_source_active():
            return True, "Fonte Intranet inativa; projeção ignorada.", True
        try:
            source_ids = [
                int(value) for value in str(job.source_path or "").split(",") if value
            ]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "qualidade_projection exige source_path com IDs numéricos"
            ) from exc
        if not source_ids:
            raise ValueError("qualidade_projection exige ao menos um ID")
        sources = list(
            AuditoriaFalhaCadastro.objects.select_related(
                "atividade", "auditor_ref", "auditor_responsavel", "analise_origem"
            )
            .filter(pk__in=source_ids)
            .order_by("pk")
        )
        if not sources:
            return True, "Origens não existem mais.", True
        with quality_projection_sync_guard():
            for source in sources:
                sync_one(source, bump_cache=False)
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )

        bump_quality_cache_version()
        return True, f"{len(sources)} projeção(ões) atualizada(s).", False

    raise ValueError(f"Domínio de sync desconhecido: {job.domain}")


def cancel_pending_job(job_id: int) -> BotDbSyncJob:
    """Cancela job pending → skipped. Raises ValueError se não for pending."""
    with transaction.atomic():
        job = (
            BotDbSyncJob.objects.select_for_update()
            .filter(pk=job_id)
            .first()
        )
        if job is None:
            raise LookupError(f"Job #{job_id} não encontrado.")
        if job.status != BotDbSyncJob.STATUS_PENDING:
            raise ValueError(
                f"Só é possível cancelar jobs pendentes (status atual: {job.status})."
            )
        job.status = BotDbSyncJob.STATUS_SKIPPED
        job.message = "Cancelado pelo usuário."
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "message", "finished_at"])
        return job


def serialize_bot_db_sync_job(job: BotDbSyncJob) -> dict:
    """Payload JSON de um job para a UI de fila."""
    from pathlib import Path

    domain_labels = dict(BotDbSyncJob.DOMAIN_CHOICES)
    status_labels = dict(BotDbSyncJob.STATUS_CHOICES)
    lane_labels = dict(BotDbSyncJob.LANE_CHOICES)

    source_name = ""
    if job.source_path:
        try:
            source_name = Path(job.source_path).name
        except Exception:
            source_name = job.source_path[-80:]
    lane = job.lane or BotDbSyncJob.LANE_LOW
    return {
        "id": job.pk,
        "domain": job.domain,
        "domain_label": domain_labels.get(job.domain, job.domain),
        "report_type": job.report_type or "",
        "lane": lane,
        "lane_label": lane_labels.get(lane, lane),
        "source_path": job.source_path or "",
        "source_name": source_name,
        "force": bool(job.force),
        "status": job.status,
        "status_label": status_labels.get(job.status, job.status),
        "message": (job.message or "")[:500],
        "attempts": int(job.attempts or 0),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def sync_lane_panel_snapshot() -> dict:
    """Jobs ativos por lane + status do subprocesso drain (para modal de diagnóstico)."""
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config
    from apps.common.bot_db_sync_gate import bot_db_sync_lock_snapshot

    cfg = get_bot_db_sync_runtime_config()
    active_by_lane: dict[str, list[dict]] = {}
    for lane in LANES:
        qs = (
            BotDbSyncJob.objects.filter(
                lane=lane,
                status__in=(BotDbSyncJob.STATUS_PENDING, BotDbSyncJob.STATUS_RUNNING),
            )
            .order_by("created_at", "id")
        )
        active_by_lane[lane] = [serialize_bot_db_sync_job(job) for job in qs]

    drain_status = {
        lane: {"drain_active": _drain_lock_held(lane)}
        for lane in LANES
    }
    return {
        "active_by_lane": active_by_lane,
        "drain_status": drain_status,
        "lock_leases": bot_db_sync_lock_snapshot(),
        "queue_wait_s": int(cfg.queue_wait_s),
    }


def drain_pending_jobs(*, max_jobs: int = 10, lane: str | None = None) -> dict:
    """Processa até max_jobs pendentes da lane. Requer lock de processo da lane."""
    orphan = {"requeued": 0, "failed": 0}
    if lane:
        # Já detemos o lock desta lane → qualquer running residual é órfão.
        orphan = recover_orphan_running_jobs_for_lane(lane)
    stale = recover_stale_running_jobs()
    counters = {"processed": 0, "done": 0, "failed": 0, "skipped": 0}
    deferred_memory = False
    max_jobs = max(1, int(max_jobs))
    concurrency = min(max_jobs, _lane_concurrency(lane))

    def memory_available() -> bool:
        nonlocal deferred_memory
        if not lane or normalize_lane(lane) != LANE_HIGH:
            return True
        from apps.common.bot_db_sync_memory import memory_ok_for_heavy_sync

        if memory_ok_for_heavy_sync():
            return True
        deferred_memory = True
        log.warning("bot-db-sync drain: deferindo claim lane=high (RAM insuficiente)")
        return False

    def process_one(job: BotDbSyncJob) -> None:
        process_job(job)
        counters["processed"] += 1
        if job.status == BotDbSyncJob.STATUS_DONE:
            counters["done"] += 1
        elif job.status == BotDbSyncJob.STATUS_SKIPPED:
            counters["skipped"] += 1
        else:
            counters["failed"] += 1

    if concurrency == 1:
        for _ in range(max_jobs):
            if not memory_available():
                break
            job = claim_next_job(lane=lane)
            if not job:
                break
            process_one(job)
    else:
        claim_lock = threading.Lock()
        counter_lock = threading.Lock()
        state_condition = threading.Condition()
        reserved = 0
        active_jobs = 0
        idle_grace_s = max(
            0.5,
            float(getattr(settings, "BOT_DB_SYNC_PARALLEL_IDLE_GRACE_S", 2.0)),
        )

        def worker() -> None:
            nonlocal active_jobs, reserved
            close_old_connections()
            idle_deadline = time.monotonic() + idle_grace_s
            try:
                while True:
                    with claim_lock:
                        if reserved >= max_jobs or not memory_available():
                            return
                        job = claim_next_job(lane=lane)
                        if job:
                            reserved += 1
                    if not job:
                        with state_condition:
                            if reserved >= max_jobs:
                                return
                            if active_jobs > 0:
                                idle_deadline = time.monotonic() + idle_grace_s
                            remaining = idle_deadline - time.monotonic()
                            if remaining <= 0:
                                return
                            state_condition.wait(timeout=min(0.2, remaining))
                        continue
                    with state_condition:
                        active_jobs += 1
                    try:
                        process_job(job)
                        with counter_lock:
                            counters["processed"] += 1
                            if job.status == BotDbSyncJob.STATUS_DONE:
                                counters["done"] += 1
                            elif job.status == BotDbSyncJob.STATUS_SKIPPED:
                                counters["skipped"] += 1
                            else:
                                counters["failed"] += 1
                    finally:
                        with state_condition:
                            active_jobs -= 1
                            idle_deadline = time.monotonic() + idle_grace_s
                            state_condition.notify_all()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix=f"bot-sync-{lane or 'all'}",
        ) as executor:
            futures = [executor.submit(worker) for _ in range(concurrency)]
            for future in futures:
                future.result()
    return {
        **counters,
        "stale_requeued": stale["requeued"] + orphan["requeued"],
        "stale_failed": stale["failed"] + orphan["failed"],
        "orphan_requeued": orphan["requeued"],
        "orphan_failed": orphan["failed"],
        "deferred_memory": deferred_memory,
        "lane": lane or "all",
        "concurrency": concurrency,
    }
