# -*- coding: utf-8 -*-
"""Status operacional intradiário de produtividade (fonte única).

Separa produção acumulada, ritmo vs esperado no tempo produtivo e resultado final.
Metodologia versionada — ver documentation/plano_correcao_status_produtividade.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

STATUS_METHODOLOGY_VERSION = "v2-intradiario-2026-08-05"

PRODUCTIVE_DAY_SECONDS = 19800  # 05:30
WARMUP_SECONDS = 30 * 60  # 30 min
CRITICAL_MIN_PRODUCTIVE_SECONDS = 5400  # 90 min
CRITICAL_WINDOWS_REQUIRED = 2

# Atingimento live (pace_attainment_pct)
AHEAD_MIN = 105.0
ON_TRACK_MIN = 95.0
ATTENTION_MIN = 85.0
BEHIND_MIN = 70.0

# Resultado final (final_attainment_pct)
FINAL_ON_TARGET_MIN = 100.0
FINAL_NEAR_MIN = 95.0
FINAL_BELOW_MIN = 80.0

StatusPhase = Literal["pre_shift", "live", "final", "unavailable"]

OperationalStatus = Literal[
    "not_started",
    "warming_up",
    "ahead",
    "on_track",
    "attention",
    "behind",
    "critical",
    "data_delayed",
    "unknown",
    "final_on_target",
    "final_near",
    "final_below",
    "final_critical",
]

VisualTone = Literal[
    "neutral",
    "info",
    "ok",
    "attention",
    "behind",
    "critical",
    "delayed",
]

STATUS_LABELS: dict[str, str] = {
    "not_started": "Não iniciado",
    "warming_up": "Em aquecimento",
    "ahead": "Adiantado",
    "on_track": "No ritmo",
    "attention": "Atenção",
    "behind": "Atrás",
    "critical": "Crítico",
    "data_delayed": "Dados atrasados",
    "unknown": "Sem classificação",
    "final_on_target": "Meta atingida",
    "final_near": "Próximo da meta",
    "final_below": "Abaixo da meta",
    "final_critical": "Resultado crítico",
}

STATUS_TONES: dict[str, VisualTone] = {
    "not_started": "neutral",
    "warming_up": "info",
    "ahead": "ok",
    "on_track": "ok",
    "attention": "attention",
    "behind": "behind",
    "critical": "critical",
    "data_delayed": "delayed",
    "unknown": "neutral",
    "final_on_target": "ok",
    "final_near": "attention",
    "final_below": "behind",
    "final_critical": "critical",
}

NON_CRITICAL_STATUSES = frozenset(
    {
        "not_started",
        "warming_up",
        "ahead",
        "on_track",
        "data_delayed",
        "unknown",
        "final_on_target",
        "final_near",
    }
)

PRIORITY_NEGATIVE_STATUSES = frozenset(
    {
        "attention",
        "behind",
        "critical",
        "final_below",
        "final_critical",
    }
)


@dataclass(frozen=True)
class OperationalStatusResult:
    status_phase: StatusPhase
    operational_status: OperationalStatus
    operational_status_label: str
    operational_status_reason: str
    visual_tone: VisualTone
    pace_actual_pct: float | None
    pace_expected_pct: float | None
    pace_attainment_pct: float | None
    pace_delta_pp: float | None
    applied_meta: float | None
    projected_closing_pct: float | None
    productive_logged_seconds: int | None
    status_evaluated_at: str | None
    status_methodology_version: str = STATUS_METHODOLOGY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "status_phase": self.status_phase,
            "operational_status": self.operational_status,
            "operational_status_label": self.operational_status_label,
            "operational_status_reason": self.operational_status_reason,
            "visual_tone": self.visual_tone,
            "pace_actual_pct": self.pace_actual_pct,
            "pace_expected_pct": self.pace_expected_pct,
            "pace_attainment_pct": self.pace_attainment_pct,
            "pace_delta_pp": self.pace_delta_pp,
            "applied_meta": self.applied_meta,
            "projected_closing_pct": self.projected_closing_pct,
            "productive_logged_seconds": self.productive_logged_seconds,
            "status_evaluated_at": self.status_evaluated_at,
            "status_methodology_version": self.status_methodology_version,
        }


def compute_pace_expected_pct(
    applied_meta: float | None,
    productive_elapsed_seconds: int | float | None,
    *,
    productive_day_seconds: int = PRODUCTIVE_DAY_SECONDS,
) -> float | None:
    """Esperado = meta × tempo_produtivo ÷ jornada (limitado à meta)."""
    if applied_meta is None or productive_elapsed_seconds is None:
        return None
    try:
        meta = float(applied_meta)
        elapsed = float(productive_elapsed_seconds)
        day = float(productive_day_seconds)
    except (TypeError, ValueError):
        return None
    if meta <= 0 or elapsed < 0 or day <= 0:
        return None
    expected = meta * min(elapsed, day) / day
    return round(min(expected, meta), 2)


def compute_pace_attainment_pct(
    pace_actual_pct: float | None,
    pace_expected_pct: float | None,
) -> float | None:
    if pace_actual_pct is None or pace_expected_pct is None:
        return None
    try:
        actual = float(pace_actual_pct)
        expected = float(pace_expected_pct)
    except (TypeError, ValueError):
        return None
    if expected <= 0:
        return None
    return round((actual / expected) * 100.0, 2)


def compute_projected_closing_pct(
    pace_attainment_pct: float | None,
    applied_meta: float | None,
    *,
    allow_projection: bool = True,
) -> float | None:
    if not allow_projection or pace_attainment_pct is None or applied_meta is None:
        return None
    try:
        return round((float(pace_attainment_pct) / 100.0) * float(applied_meta), 1)
    except (TypeError, ValueError):
        return None


def _live_status_from_attainment(
    attainment: float,
    *,
    productive_seconds: int,
    consecutive_critical_windows: int = 0,
) -> tuple[OperationalStatus, str]:
    if attainment >= AHEAD_MIN:
        return "ahead", f"Atingimento {attainment:.1f}% (≥ {AHEAD_MIN:.0f}%): adiantado."
    if attainment >= ON_TRACK_MIN:
        return "on_track", f"Atingimento {attainment:.1f}% no ritmo esperado."
    if attainment >= ATTENTION_MIN:
        return "attention", f"Atingimento {attainment:.1f}% — atenção."
    if attainment >= BEHIND_MIN:
        return "behind", f"Atingimento {attainment:.1f}% — atrás do esperado."

    if (
        productive_seconds >= CRITICAL_MIN_PRODUCTIVE_SECONDS
        and consecutive_critical_windows >= CRITICAL_WINDOWS_REQUIRED
    ):
        return (
            "critical",
            (
                f"Atingimento {attainment:.1f}% sustentado "
                f"({consecutive_critical_windows} janelas < {BEHIND_MIN:.0f}% "
                f"após {productive_seconds // 60} min produtivos)."
            ),
        )
    return (
        "behind",
        (
            f"Atingimento {attainment:.1f}% abaixo de {BEHIND_MIN:.0f}%, "
            "sem persistência suficiente para crítico."
        ),
    )


def _final_status_from_attainment(final_attainment: float) -> tuple[OperationalStatus, str]:
    if final_attainment >= FINAL_ON_TARGET_MIN:
        return "final_on_target", f"Resultado {final_attainment:.1f}% — meta atingida."
    if final_attainment >= FINAL_NEAR_MIN:
        return "final_near", f"Resultado {final_attainment:.1f}% — próximo da meta."
    if final_attainment >= FINAL_BELOW_MIN:
        return "final_below", f"Resultado {final_attainment:.1f}% — abaixo da meta."
    return "final_critical", f"Resultado {final_attainment:.1f}% — crítico."


def classify_operational_status(
    *,
    applied_meta: float | None,
    pace_actual_pct: float | None = None,
    productive_logged_seconds: int | None = None,
    has_login: bool | None = None,
    has_production: bool | None = None,
    shift_closed: bool = False,
    final_actual_pct: float | None = None,
    data_delayed: bool = False,
    consecutive_critical_windows: int = 0,
    evaluated_at: datetime | None = None,
    productive_day_seconds: int = PRODUCTIVE_DAY_SECONDS,
    pace_expected_pct: float | None = None,
) -> OperationalStatusResult:
    """Classifica status operacional (jornada aberta ou encerrada)."""
    evaluated_iso = None
    if evaluated_at is not None:
        evaluated_iso = (
            evaluated_at.isoformat()
            if hasattr(evaluated_at, "isoformat")
            else str(evaluated_at)
        )

    prod_secs = int(productive_logged_seconds or 0)
    if has_login is None:
        has_login = prod_secs > 0
    if has_production is None:
        has_production = pace_actual_pct is not None and float(pace_actual_pct or 0) > 0

    def _result(
        phase: StatusPhase,
        status: OperationalStatus,
        reason: str,
        *,
        actual: float | None = None,
        expected: float | None = None,
        attainment: float | None = None,
        delta: float | None = None,
        projected: float | None = None,
    ) -> OperationalStatusResult:
        return OperationalStatusResult(
            status_phase=phase,
            operational_status=status,
            operational_status_label=STATUS_LABELS[status],
            operational_status_reason=reason,
            visual_tone=STATUS_TONES[status],
            pace_actual_pct=actual,
            pace_expected_pct=expected,
            pace_attainment_pct=attainment,
            pace_delta_pp=delta,
            applied_meta=applied_meta,
            projected_closing_pct=projected,
            productive_logged_seconds=(
                prod_secs
                if prod_secs > 0
                else (productive_logged_seconds if productive_logged_seconds else None)
            ),
            status_evaluated_at=evaluated_iso,
        )

    if applied_meta is None:
        return _result(
            "unavailable",
            "unknown",
            "Operação sem meta identificada (Fraud/Compliance/Mista); sem classificação.",
            actual=pace_actual_pct,
        )

    if data_delayed and not shift_closed:
        expected = pace_expected_pct
        if expected is None:
            expected = compute_pace_expected_pct(
                applied_meta, prod_secs, productive_day_seconds=productive_day_seconds
            )
        attainment = compute_pace_attainment_pct(pace_actual_pct, expected)
        delta = None
        if pace_actual_pct is not None and expected is not None:
            delta = round(float(pace_actual_pct) - float(expected), 2)
        return _result(
            "live",
            "data_delayed",
            "Monitor mais recente que a produção além da tolerância — não classificar como baixa produtividade.",
            actual=pace_actual_pct,
            expected=expected,
            attainment=attainment,
            delta=delta,
            projected=None,
        )

    if shift_closed:
        final_pct = final_actual_pct if final_actual_pct is not None else pace_actual_pct
        if final_pct is None:
            return _result(
                "final",
                "unknown",
                "Jornada encerrada sem produção acumulada avaliável.",
            )
        final_attainment = compute_pace_attainment_pct(final_pct, applied_meta)
        if final_attainment is None:
            return _result(
                "final",
                "unknown",
                "Não foi possível calcular o atingimento final.",
                actual=final_pct,
            )
        status, reason = _final_status_from_attainment(final_attainment)
        return _result(
            "final",
            status,
            reason,
            actual=round(float(final_pct), 2),
            expected=float(applied_meta),
            attainment=final_attainment,
            delta=round(float(final_pct) - float(applied_meta), 2),
            projected=round(float(final_pct), 1),
        )

    if not has_login and not has_production:
        return _result(
            "pre_shift",
            "not_started",
            "Sem login e sem produção no período.",
        )

    if prod_secs > 0 and prod_secs < WARMUP_SECONDS:
        expected = pace_expected_pct
        if expected is None:
            expected = compute_pace_expected_pct(
                applied_meta, prod_secs, productive_day_seconds=productive_day_seconds
            )
        attainment = compute_pace_attainment_pct(pace_actual_pct, expected)
        delta = None
        if pace_actual_pct is not None and expected is not None:
            delta = round(float(pace_actual_pct) - float(expected), 2)
        return _result(
            "live",
            "warming_up",
            (
                f"Primeiros {WARMUP_SECONDS // 60} min produtivos "
                f"({prod_secs // 60} min decorridos) — sem priorização crítica."
            ),
            actual=pace_actual_pct,
            expected=expected,
            attainment=attainment,
            delta=delta,
            projected=None,
        )

    expected = pace_expected_pct
    if expected is None:
        expected = compute_pace_expected_pct(
            applied_meta, prod_secs, productive_day_seconds=productive_day_seconds
        )

    if pace_actual_pct is None or expected is None:
        return _result(
            "live" if has_login or has_production else "unavailable",
            "unknown",
            "Dados insuficientes para comparar realizado e esperado.",
            actual=pace_actual_pct,
            expected=expected,
        )

    attainment = compute_pace_attainment_pct(pace_actual_pct, expected)
    if attainment is None:
        return _result(
            "live",
            "unknown",
            "Esperado inválido para cálculo de atingimento.",
            actual=pace_actual_pct,
            expected=expected,
        )

    delta = round(float(pace_actual_pct) - float(expected), 2)
    status, reason = _live_status_from_attainment(
        attainment,
        productive_seconds=prod_secs,
        consecutive_critical_windows=consecutive_critical_windows,
    )
    projected = compute_projected_closing_pct(attainment, applied_meta, allow_projection=True)
    return _result(
        "live",
        status,
        reason,
        actual=round(float(pace_actual_pct), 2),
        expected=round(float(expected), 2),
        attainment=attainment,
        delta=delta,
        projected=projected,
    )


def is_priority_negative(status: str | None) -> bool:
    return status in PRIORITY_NEGATIVE_STATUSES


def is_never_critical(status: str | None) -> bool:
    return bool(status) and (
        status in NON_CRITICAL_STATUSES
        or status in {"warming_up", "data_delayed", "unknown", "not_started"}
    )


def operational_priority_rank(status: str | None) -> int:
    """Menor = mais urgente na ordenação de prioridades."""
    ranks = {
        "critical": 0,
        "final_critical": 0,
        "behind": 1,
        "final_below": 1,
        "attention": 2,
        "final_near": 3,
        "on_track": 5,
        "ahead": 5,
        "final_on_target": 5,
        "data_delayed": 6,
        "unknown": 7,
        "warming_up": 8,
        "not_started": 9,
    }
    return ranks.get(status or "unknown", 7)
