"""Progress profiles: calibrate UI progress from average step durations in logs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.constants import ROBOT_MODES
from app.config.paths import PASTA_CONFIG

PROGRESS_PROFILES_FILENAME = "progress_profiles.json"
MIN_EXECUTIONS_FOR_CALIBRATION = 2
MAX_STEP_DELTA_SECONDS = 600  # ignora idle entre ciclos / outliers

# Ordem canônica do pipeline (calibração e fallback)
CANONICAL_STEPS: dict[str, list[str]] = {
    "nivel": [
        "Nivel: iniciando loop",
        "Infra: driver iniciado",
        "Autenticacao: acessando Okta",
        "Autenticacao: Okta concluido",
        "BRFlow: navegando via Okta",
        "Nivel: aguardando download",
        "Nivel: arquivo baixado",
        "Nivel: convertendo CSV",
        "Nivel: processando dados NHID",
        "Nivel: escrevendo XLSX",
        "Nivel: escrevendo JSON",
        "Nivel: validando OneDrive",
        "Nivel: ciclo concluido",
    ],
    "monitor": [
        "Monitor: iniciando loop",
        "BRFlow: pesquisando no Okta",
        "BRFlow: abrindo nova janela",
        "BRFlow: navegando para monitor",
        "Monitor: acionando download CSV",
        "Monitor: aguardando arquivo de download",
        "Monitor: convertendo CSV",
        "Monitor: finalizando conversao",
        "Monitor: ciclo concluido",
    ],
    "production": [
        "Producao: iniciando loop",
        "Autenticacao: acessando Okta",
        "Autenticacao: Okta concluido",
        "BRFlow: navegando via Okta",
        "BRFlow: acessando modulo de producao",
        "BRFlow: navegação concluída (período será configurado por dia)",
        "BRFlow: meta configurada",
        "Producao: processando CSV",
        "Producao: salvando XLSX",
        "Producao: ciclo concluido",
    ],
    "falhas_criticas": [
        "Falhas: abrindo Power BI",
        "Falhas: em 2º plano (Power BI/Excel)",
        "Falhas: 2º plano · aplicando filtros",
        "Falhas: aplicando filtros",
        "Falhas: exportando tabela",
        "Falhas: consolidando na Base",
        "Merge: +{n} linha(s) na Base ({before}→{after})",
        "Falhas: Base atualizada",
        "Falhas: gerando relatórios HTML/Outlook",
        "Falhas: gerando relatórios e abrindo e-mail",
        "Falhas: Abrindo e-mail",
        "Falhas: pipeline concluído",
    ],
    "prioridades_nh": [
        "Prioridades NH: iniciando Chrome",
        "Prioridades NH: autenticando Okta/BrFlow",
        "Prioridades NH: pesquisando",
        "Prioridades NH: gravado",
        "Prioridades NH: concluído",
    ],
}

# Ordered normalization rules: (compiled regex, replacement)
_NORMALIZE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^Monitor NH: .+ concluido$"), "Monitor NH: {user} concluido"),
    (re.compile(r"^Monitor NH: .+ falha$"), "Monitor NH: {user} falha"),
    (re.compile(r"^Tarefa (\d+)/(\d+): (.+)$"), r"Tarefa {i}/{n}: \3"),
    (re.compile(r"^Workflow (\d+)/(\d+): (.+)$"), r"Workflow {i}/{n}: \3"),
    (re.compile(r"^GED: download (\d+)%$"), "GED: download {pct}%"),
    (
        re.compile(r"^Monitor de eventos: baixando arquivo de .+$"),
        "Monitor de eventos: baixando arquivo de {date}",
    ),
    (re.compile(r"^Producao: configurando período dia \d+$"), "Producao: configurando período dia {n}"),
    (re.compile(r"^Baixando arquivo \d+/4$"), "Baixando arquivo {i}/4"),
    (re.compile(r"^Confer monitor: baixando por matricula$"), "Confer monitor: baixando por matricula"),
]

_GED_DOWNLOAD_RE = re.compile(r"^GED: download (\d+)%$")
_TASK_RE = re.compile(r"^Tarefa (\d+)/(\d+): ")
_WORKFLOW_RE = re.compile(r"^Workflow (\d+)/(\d+): ")

PATTERN_DEFS: dict[str, list[dict]] = {
    "excel": [
        {
            "regex": r"^Monitor NH: .+ concluido$",
            "group": "excel_user_done",
            "pct_mode": "linear_within_parent",
            "parent_pct_start": 10,
            "parent_pct_end": 95,
        },
    ],
    "rotina": [
        {
            "regex": r"^Tarefa \d+/\d+: .+$",
            "group": "rotina_task",
            "pct_mode": "linear_within_parent",
            "parent_pct_start": 58,
            "parent_pct_end": 98,
        },
    ],
    "replicacao_auditoria_d1": [
        {
            "regex": r"^Workflow \d+/\d+: .+$",
            "group": "replicacao_workflow",
            "pct_mode": "linear_within_parent",
            "parent_pct_start": 35,
            "parent_pct_end": 95,
        },
    ],
    "replicacao_auditoria": [
        {
            "regex": r"^Workflow \d+/\d+: .+$",
            "group": "replicacao_workflow",
            "pct_mode": "linear_within_parent",
            "parent_pct_start": 15,
            "parent_pct_end": 95,
        },
    ],
    "ged": [
        {
            "regex": r"^GED: download \d+%$",
            "group": "ged_browser_download",
            "pct_mode": "linear_within_parent",
            "parent_pct_start": 15,
            "parent_pct_end": 90,
        },
    ],
    "confer": [
        {
            "regex": r"^Confer monitor: baixando por matricula$",
            "group": "confer_matricula",
            "pct_mode": "linear_within_parent",
            "parent_pct_start": 92,
            "parent_pct_end": 99,
        },
    ],
}


def default_profiles_path() -> Path:
    return PASTA_CONFIG / PROGRESS_PROFILES_FILENAME


def normalize_step_label(msg: str) -> str:
    """Normalize dynamic progress messages for aggregation."""
    text = (msg or "").strip()
    if not text:
        return text
    for pattern, replacement in _NORMALIZE_RULES:
        if pattern.match(text):
            if "\\3" in replacement or "{i}" in replacement:
                return pattern.sub(replacement, text)
            return replacement
    return text


def _parse_profiles_payload(raw: Any) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for mode, profile in raw.items():
        if mode not in ROBOT_MODES or not isinstance(profile, dict):
            continue
        steps = profile.get("steps")
        if not isinstance(steps, list):
            steps = []
        patterns = profile.get("patterns")
        if not isinstance(patterns, list):
            patterns = []
        out[mode] = {
            "generated_at": str(profile.get("generated_at", "")),
            "steps": [s for s in steps if isinstance(s, dict)],
            "patterns": [p for p in patterns if isinstance(p, dict)],
        }
    return out


def _linear_pct_from_pattern(mode: str, msg: str) -> int | None:
    text = (msg or "").strip()
    for pat in PATTERN_DEFS.get(mode or "", []):
        regex = str(pat.get("regex", "")).strip()
        if not regex:
            continue
        try:
            if not re.match(regex, text):
                continue
        except re.error:
            continue
        if str(pat.get("pct_mode", "")).strip() != "linear_within_parent":
            continue
        parent_start = int(pat.get("parent_pct_start", 0))
        parent_end = int(pat.get("parent_pct_end", 100))
        span = max(1, parent_end - parent_start)
        ged_match = _GED_DOWNLOAD_RE.match(text)
        if ged_match:
            inner = int(ged_match.group(1))
            return parent_start + int((inner / 100) * span)
        task_match = _TASK_RE.match(text)
        if task_match:
            idx, total = int(task_match.group(1)), max(1, int(task_match.group(2)))
            return parent_start + int((idx / total) * span)
        wf_match = _WORKFLOW_RE.match(text)
        if wf_match:
            idx, total = int(wf_match.group(1)), max(1, int(wf_match.group(2)))
            return parent_start + int((idx / total) * span)
    return None


class ProgressProfileStore:
    """Loads and caches progress_profiles.json; resolves pct from step messages."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_profiles_path()
        self._mtime: float | None = None
        self._profiles: dict[str, dict] = {}
        self._step_index: dict[str, dict[str, int]] = {}
        self._pattern_index: dict[str, list[tuple[re.Pattern[str], dict]]] = {}

    def reload_if_needed(self) -> None:
        if not self.path.exists():
            self._profiles = {}
            self._step_index = {}
            self._pattern_index = {}
            self._mtime = None
            return
        mtime = self.path.stat().st_mtime
        if self._mtime is not None and mtime == self._mtime:
            return
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except Exception:
            raw = {}
        self._profiles = _parse_profiles_payload(raw)
        self._step_index = {}
        self._pattern_index = {}
        for mode, profile in self._profiles.items():
            exact: dict[str, int] = {}
            for step in profile.get("steps", []):
                match = str(step.get("match", "")).strip()
                pct = step.get("pct")
                if not match or pct is None:
                    continue
                try:
                    exact[match] = max(0, min(100, int(pct)))
                except (TypeError, ValueError):
                    continue
            self._step_index[mode] = exact

            compiled: list[tuple[re.Pattern[str], dict]] = []
            for pat in profile.get("patterns", []):
                regex = str(pat.get("regex", "")).strip()
                if not regex:
                    continue
                try:
                    compiled.append((re.compile(regex), pat))
                except re.error:
                    continue
            self._pattern_index[mode] = compiled

        self._mtime = self.path.stat().st_mtime if self.path.exists() else None

    def has_profile(self, mode: str | None) -> bool:
        if not mode:
            return False
        self.reload_if_needed()
        return mode in self._profiles and bool(self._profiles[mode].get("steps"))

    def resolve_pct(self, mode: str | None, msg: str, hint_pct: int | None = None) -> int | None:
        if not mode:
            return hint_pct
        self.reload_if_needed()
        profile = self._profiles.get(mode)
        if not profile:
            return hint_pct

        text = (msg or "").strip()
        normalized = normalize_step_label(text)

        exact = self._step_index.get(mode, {})
        if normalized in exact:
            return _sanity_check_pct(hint_pct, exact[normalized], text)
        if text in exact:
            return _sanity_check_pct(hint_pct, exact[text], text)

        for regex, pat in self._pattern_index.get(mode, []):
            if not regex.match(text):
                continue
            pct_mode = str(pat.get("pct_mode", "")).strip()
            if pct_mode == "linear_within_parent":
                parent_pct = int(pat.get("parent_pct_end", 100))
                parent_start = int(pat.get("parent_pct_start", 0))
                ged_match = _GED_DOWNLOAD_RE.match(text)
                if ged_match:
                    inner = int(ged_match.group(1))
                    span = max(1, parent_pct - parent_start)
                    return parent_start + int((inner / 100) * span)
                task_match = _TASK_RE.match(text)
                if task_match:
                    idx, total = int(task_match.group(1)), max(1, int(task_match.group(2)))
                    span = max(1, parent_pct - parent_start)
                    return parent_start + int((idx / total) * span)
                wf_match = _WORKFLOW_RE.match(text)
                if wf_match:
                    idx, total = int(wf_match.group(1)), max(1, int(wf_match.group(2)))
                    span = max(1, parent_pct - parent_start)
                    return parent_start + int((idx / total) * span)
            try:
                return max(0, min(100, int(pat.get("pct", hint_pct or 0))))
            except (TypeError, ValueError):
                continue

        return hint_pct


def _sanity_check_pct(
    hint_pct: int | None,
    resolved: int | None,
    msg: str = "",
) -> int | None:
    """Evita saltos absurdos quando o perfil calibrado está distorcido."""
    if resolved is None:
        return hint_pct
    if hint_pct is None:
        return resolved
    text = (msg or "").lower()
    if "concluido" in text or "concluído" in text:
        return resolved
    if hint_pct <= 0 and resolved > 10:
        return hint_pct
    if hint_pct < 45 and resolved > 75:
        return hint_pct
    if hint_pct < 55 and resolved > hint_pct + 40:
        return hint_pct
    return resolved


def sort_steps_canonical(mode: str, labels: list[str]) -> list[str]:
    canonical = CANONICAL_STEPS.get(mode, [])
    order_map = {label: idx for idx, label in enumerate(canonical)}
    return sorted(labels, key=lambda label: (order_map.get(label, 9999), label))


def fallback_linear_pct(mode: str | None, msg: str) -> int | None:
    if not mode:
        return None
    return _linear_pct_from_pattern(mode, msg)


_store: ProgressProfileStore | None = None


def get_profile_store(path: Path | None = None) -> ProgressProfileStore:
    global _store
    if path is not None:
        return ProgressProfileStore(path)
    if _store is None:
        _store = ProgressProfileStore()
    return _store


def load_profiles(path: Path | None = None) -> dict[str, dict]:
    store = ProgressProfileStore(path)
    store.reload_if_needed()
    return dict(store._profiles)


def resolve_pct(
    mode: str | None,
    msg: str,
    hint_pct: int | None = None,
    *,
    path: Path | None = None,
) -> int | None:
    return get_profile_store(path).resolve_pct(mode, msg, hint_pct)


def build_equal_weight_profile(
    mode: str,
    step_labels: list[str],
    *,
    generated_at: str | None = None,
) -> dict:
    """Fallback profile with equal time weight per step."""
    labels = [normalize_step_label(s) for s in step_labels if str(s).strip()]
    if not labels:
        return {"generated_at": generated_at or datetime.now().isoformat(timespec="seconds"), "steps": [], "patterns": []}
    n = len(labels)
    steps = []
    for i, label in enumerate(labels, start=1):
        pct = round((100 * i) / n)
        steps.append(
            {
                "match": label,
                "match_type": "exact",
                "avg_seconds": None,
                "pct": pct,
            }
        )
    steps[-1]["pct"] = 100
    return {
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
        "patterns": [],
    }
