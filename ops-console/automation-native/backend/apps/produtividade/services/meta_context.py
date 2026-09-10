# -*- coding: utf-8 -*-
"""Meta diária dinâmica por composição operacional (Fraud / Compliance / Mista)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from django.db.models import QuerySet

META_FRAUD = 92.0
META_COMPLIANCE = 97.0
META_MISTA = 95.0
META_SHIFT_HOURS = 5.5  # 5h30 — alinhado a META_SHIFT_SECONDS

TeamClass = Literal["fraud", "compliance", "unknown"]
EvaluationType = Literal["fraud", "compliance", "mista", "nao_identificada"]
IndicatorStatus = Literal["atingiu", "abaixo", "nao_avaliavel"]

EVAL_LABELS = {
    "fraud": "Fraud",
    "compliance": "Compliance",
    "mista": "Mista",
    "nao_identificada": "Operação não identificada",
}

STATUS_LABELS = {
    "atingiu": "Atingiu a meta",
    "abaixo": "Abaixo da meta",
    "nao_avaliavel": "Não avaliável",
}


@dataclass(frozen=True)
class MetaEvaluation:
    evaluation_type: EvaluationType
    applied_meta: float | None
    hourly_threshold: float | None
    has_unknown_teams: bool
    teams_sample: tuple[str, ...]

    @property
    def evaluation_label(self) -> str:
        return EVAL_LABELS[self.evaluation_type]

    def status_for(self, realized_pct: float | None) -> IndicatorStatus:
        if self.applied_meta is None or realized_pct is None:
            return "nao_avaliavel"
        if realized_pct >= self.applied_meta:
            return "atingiu"
        return "abaixo"

    def gap_pp(self, realized_pct: float | None) -> float | None:
        if self.applied_meta is None or realized_pct is None:
            return None
        return round(realized_pct - self.applied_meta, 2)

    def justification(self, realized_pct: float | None = None) -> str:
        if self.evaluation_type == "nao_identificada":
            base = (
                "Não foi possível identificar a operação do contexto atual "
                "(registros sem classificação Fraud/Compliance ou operação não identificada). "
                "Por isso a avaliação é não avaliável e nenhuma meta foi aplicada."
            )
            if realized_pct is None:
                return base
            return (
                f"{base} A produtividade realizada foi de {_fmt_pct(realized_pct)}."
            )

        if self.evaluation_type == "fraud":
            tipo = (
                "Esta é uma avaliação Fraud, pois o contexto atual contém "
                "exclusivamente registros da operação Operacional Fraud."
            )
            meta = META_FRAUD
        elif self.evaluation_type == "compliance":
            tipo = (
                "Esta é uma avaliação Compliance, pois o contexto atual contém "
                "exclusivamente registros da operação Operacional Compliance."
            )
            meta = META_COMPLIANCE
        else:
            tipo = (
                "Esta é uma avaliação mista, pois o contexto atual contém "
                "registros das operações Fraud e Compliance."
            )
            meta = META_MISTA

        parts = [tipo, f"A meta aplicável é de {_fmt_pct(meta)}."]
        if self.has_unknown_teams:
            parts.append(
                "Registros com operação não identificada foram ignorados na definição da meta."
            )
        if realized_pct is not None:
            gap = self.gap_pp(realized_pct)
            status = self.status_for(realized_pct)
            parts.append(f"A produtividade realizada foi de {_fmt_pct(realized_pct)}.")
            if gap is not None:
                if gap == 0:
                    parts.append("O realizado ficou exatamente na meta.")
                elif gap > 0:
                    parts.append(
                        f"Ficando {_fmt_pp(gap)} ponto(s) percentual(is) acima da meta."
                    )
                else:
                    parts.append(
                        f"Ficando {_fmt_pp(abs(gap))} ponto(s) percentual(is) abaixo da meta."
                    )
            parts.append(f"Portanto, o indicador está {STATUS_LABELS[status].lower()}.")
        return " ".join(parts)

    def to_dict(self, realized_pct: float | None = None) -> dict:
        status = self.status_for(realized_pct)
        return {
            "evaluation_type": self.evaluation_type,
            "evaluation_label": self.evaluation_label,
            "applied_meta": self.applied_meta,
            "hourly_threshold": self.hourly_threshold,
            "realized_pct": None if realized_pct is None else round(float(realized_pct), 2),
            "gap_pp": self.gap_pp(realized_pct),
            "indicator_status": status,
            "indicator_status_label": STATUS_LABELS[status],
            "justification": self.justification(realized_pct),
            "has_unknown_teams": self.has_unknown_teams,
        }


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%".replace(".", ",")


def _fmt_pp(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def normalize_team(team: str | None) -> str:
    raw = (team or "").strip().lower().replace("/", " ")
    return " ".join(raw.split())


def classify_team(team: str | None) -> TeamClass:
    """Classificação principal do time (unknown se não for Fraud nem Compliance)."""
    classes = team_operation_classes(team)
    if classes == {"fraud"}:
        return "fraud"
    if classes == {"compliance"}:
        return "compliance"
    if classes == {"fraud", "compliance"}:
        # Time ambíguo no próprio rótulo — contribui para mista no conjunto.
        return "fraud"
    return "unknown"


def team_operation_classes(team: str | None) -> set[TeamClass]:
    norm = normalize_team(team)
    if not norm:
        return set()
    found: set[TeamClass] = set()
    if "fraud" in norm:
        found.add("fraud")
    if "compliance" in norm:
        found.add("compliance")
    return found


def resolve_meta_from_teams(teams: Iterable[str | None]) -> MetaEvaluation:
    classes: set[TeamClass] = set()
    has_unknown = False
    sample: list[str] = []
    seen_sample: set[str] = set()

    for team in teams:
        label = (team or "").strip()
        if label and label not in seen_sample and len(sample) < 8:
            sample.append(label)
            seen_sample.add(label)
        found = team_operation_classes(team)
        if not found:
            if label or team is not None:
                has_unknown = True
            continue
        classes |= found

    if not classes:
        return MetaEvaluation(
            evaluation_type="nao_identificada",
            applied_meta=None,
            hourly_threshold=None,
            has_unknown_teams=True,
            teams_sample=tuple(sample),
        )

    if classes == {"fraud"}:
        meta = META_FRAUD
        etype: EvaluationType = "fraud"
    elif classes == {"compliance"}:
        meta = META_COMPLIANCE
        etype = "compliance"
    else:
        meta = META_MISTA
        etype = "mista"

    return MetaEvaluation(
        evaluation_type=etype,
        applied_meta=meta,
        hourly_threshold=round(meta / META_SHIFT_HOURS, 2),
        has_unknown_teams=has_unknown,
        teams_sample=tuple(sample),
    )


def teams_from_queryset(qs: QuerySet) -> list[str]:
    return [
        t
        for t in qs.values_list("team", flat=True).distinct()
        if (t or "").strip()
    ]


def teams_by_agent(qs: QuerySet) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for mat, team in qs.values_list("matricula_norm", "team").distinct():
        key = (mat or "").strip()
        if not key:
            continue
        out.setdefault(key, set())
        if (team or "").strip():
            out[key].add(team.strip())
    return {k: sorted(v) for k, v in out.items()}


def agent_meta_map(qs: QuerySet) -> dict[str, MetaEvaluation]:
    return {
        mat: resolve_meta_from_teams(teams)
        for mat, teams in teams_by_agent(qs).items()
    }


def context_meta(qs: QuerySet) -> MetaEvaluation:
    return resolve_meta_from_teams(teams_from_queryset(qs))


def threshold_or_default(
    meta: MetaEvaluation,
    *,
    default: float = META_MISTA,
) -> float:
    """Limiar numérico para comparações legadas; usa mista quando não avaliável."""
    return float(meta.applied_meta) if meta.applied_meta is not None else float(default)
