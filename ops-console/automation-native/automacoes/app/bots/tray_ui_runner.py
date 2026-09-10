"""Motor reutilizável para bots de automação visual na bandeja do Windows."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.infrastructure import screen_automation as sa
from app.infrastructure.cisco_vpn import get_vpn_state
from app.infrastructure.onedrive_process import (
    find_onedrive_exe,
    is_onedrive_running,
    restart_onedrive_background,
    start_onedrive_background,
)
from app.services import tray_install_icons as install_icons
from app.services.tray_install_icons import TrayIconTemplate
from app.services.tray_templates import resolve_templates_context


@dataclass(frozen=True)
class TrayUiBotConfig:
    service_name: str
    templates_subdir: str
    check_interval_env: str
    post_recover_wait_env: str
    default_check_interval_seconds: int = 60
    default_post_recover_wait_seconds: int = 5
    sync_stuck_watch_enabled: bool = False
    sync_stuck_poll_env: str = "ONEDRIVE_UI_SYNC_STUCK_POLL_SECONDS"
    sync_stuck_window_env: str = "ONEDRIVE_UI_SYNC_STUCK_WINDOW_SECONDS"
    sync_restart_cooldown_env: str = "ONEDRIVE_UI_SYNC_RESTART_COOLDOWN_SECONDS"
    sync_restart_wait_env: str = "ONEDRIVE_UI_SYNC_RESTART_WAIT_SECONDS"
    default_sync_stuck_poll_seconds: int = 5
    default_sync_stuck_window_seconds: int = 180
    default_sync_restart_cooldown_seconds: int = 1800
    default_sync_restart_wait_seconds: int = 15


class TrayUiBot:
    def __init__(self, config: TrayUiBotConfig, parar_event: threading.Event | None = None):
        self.config = config
        self.log = logging.getLogger(f"robots.{config.templates_subdir}_ui")
        self.parar_event = parar_event or threading.Event()
        self._status_callback = None
        self._progress_callback = None
        self._templates_ctx = resolve_templates_context(config.templates_subdir)
        self._templates_dir = self._templates_ctx.local_dir
        self._repo_templates_dir = self._templates_ctx.repo_dir
        self._flow_path = self._templates_ctx.flow_path
        self._capture_cache: dict[str, tuple] | None = None
        self._consecutive_recovery_failures = 0
        self._sync_restart_cooldown_until = 0.0
        self._consecutive_unknown = 0
        self._unknown_limit = 3
        self._last_icon_match: sa.MatchResult | None = None
        self._onedrive_start_cooldown_until = 0.0

    def _is_onedrive_service(self) -> bool:
        return self._service_id() == "onedrive"

    def _is_cisco_service(self) -> bool:
        return self._service_id() == "cisco"

    def _start_if_missing_wait_seconds(self, flow: dict | None = None) -> int:
        if flow is not None:
            startup = flow.get("startup")
            if isinstance(startup, dict):
                raw = startup.get("wait_after_start_seconds")
                if raw is not None:
                    try:
                        return max(1, int(raw))
                    except (TypeError, ValueError):
                        pass
        return max(1, self._env_int("ONEDRIVE_UI_START_WAIT_SECONDS", 15))

    def _start_if_missing_enabled(self, flow: dict) -> bool:
        if not self._is_onedrive_service():
            return False
        startup = flow.get("startup")
        if isinstance(startup, dict):
            raw = startup.get("start_if_not_running")
            if raw is not None:
                if isinstance(raw, str):
                    return raw.strip().lower() in ("1", "true", "yes", "on")
                return bool(raw)
        return True

    def _start_if_missing_cooldown_seconds(self) -> int:
        return max(60, self._env_int("ONEDRIVE_UI_START_COOLDOWN_SECONDS", 300))

    def _attempt_start_onedrive_if_missing(self, flow: dict, confidence: float) -> bool:
        """Inicia OneDrive.exe quando o processo não está rodando."""
        name = self.config.service_name
        if not self._start_if_missing_enabled(flow):
            return False

        if is_onedrive_running():
            return False

        now = time.monotonic()
        if now < self._onedrive_start_cooldown_until:
            remaining = max(1, int(self._onedrive_start_cooldown_until - now))
            self._emit_status(f"{name}: OneDrive ausente — nova tentativa de início em ~{remaining}s")
            return False

        if find_onedrive_exe() is None:
            self._emit_status(f"{name}: OneDrive.exe não encontrado — não foi possível iniciar")
            self._emit_progress(0, "OneDrive.exe ausente")
            return False

        self._emit_status(f"{name}: processo não encontrado — iniciando OneDrive.exe /background")
        self._emit_progress(20, "Iniciando OneDrive")

        if not start_onedrive_background():
            self._emit_status(f"{name}: falha ao iniciar OneDrive.exe")
            self._emit_progress(0, "Falha ao iniciar")
            self._onedrive_start_cooldown_until = time.monotonic() + self._start_if_missing_cooldown_seconds()
            return False

        wait_seconds = self._start_if_missing_wait_seconds(flow)
        self._onedrive_start_cooldown_until = time.monotonic() + self._start_if_missing_cooldown_seconds()
        self._emit_status(f"Aguardando {wait_seconds}s para OneDrive aparecer na bandeja...")
        if self.parar_event.wait(wait_seconds):
            return False

        self._capture_cache = {}
        state = self.resolve_tray_state(flow, confidence)
        self._emit_tray_diagnostics(flow, confidence, state)
        if state != "unknown":
            self._consecutive_unknown = 0
            self._emit_status(f"{name} iniciado — estado na bandeja: {state}")
            return True

        self._emit_status(f"{name}: iniciado mas ícone ainda indeterminado na bandeja")
        return False

    def _template_lookup_kwargs(self) -> dict:
        return {
            "repo_templates_dir": self._templates_dir,
            "fallback_templates_dir": None,
        }

    def set_status_callback(self, fn):
        self._status_callback = fn

    def set_progress_callback(self, fn):
        self._progress_callback = fn

    def _emit_status(self, msg: str):
        try:
            if callable(self._status_callback):
                self._status_callback(str(msg or ""))
        except Exception:
            pass
        self.log.info(msg)

    def _emit_progress(self, pct: int, msg: str = ""):
        try:
            if callable(self._progress_callback):
                self._progress_callback(int(pct), str(msg or ""))
        except Exception:
            pass
        self.log.info("Progress %s: %s", pct, msg)

    def _env_int(self, name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    def _check_interval_seconds(self) -> int:
        return max(15, self._env_int(self.config.check_interval_env, self.config.default_check_interval_seconds))

    def _recovery_failure_limit(self) -> int:
        return max(1, self._env_int("TRAY_UI_RECOVERY_FAILURE_LIMIT", 3))

    def _recovery_backoff_seconds(self) -> int:
        return max(60, self._env_int("TRAY_UI_RECOVERY_BACKOFF_SECONDS", 4 * 3600))

    def _begin_cycle_cache(self) -> None:
        self._capture_cache = {}

    def _end_cycle_cache(self) -> None:
        self._capture_cache = None

    def _service_id(self) -> str:
        return self.config.templates_subdir

    def _install_state_entries(self) -> list[tuple[str, TrayIconTemplate]]:
        return install_icons.list_all_state_entries(
            self._service_id(),
            **self._template_lookup_kwargs(),
        )

    def _get_tray_prepared_capture(self) -> tuple[np.ndarray, tuple[int, int]]:
        key = "tray_prepared"
        if self._capture_cache is not None and key in self._capture_cache:
            return self._capture_cache[key]
        screenshot, offset = sa.capture_region("tray")
        prepared, _fg = sa.prepare_tray_screenshot(screenshot)
        shot = (prepared, offset)
        if self._capture_cache is not None:
            self._capture_cache[key] = shot
        return shot

    def _match_install_icon(
        self,
        icon: TrayIconTemplate,
        confidence: float = 0.0,
    ) -> sa.MatchResult | None:
        screenshot, offset = self._get_tray_prepared_capture()
        return sa.find_template_masked_multiscale(
            icon.bgr,
            icon.mask,
            screenshot=screenshot,
            offset=offset,
            confidence=confidence,
            scales=sa.tray_match_scales(),
            prepare_tray=False,
        )

    def _resolve_step_templates(self, flow: dict, step: dict) -> list[Path]:
        names: list[str] = []
        raw_list = step.get("templates")
        if isinstance(raw_list, list):
            names.extend(str(item).strip() for item in raw_list if str(item).strip())
        single = str(step.get("template") or "").strip()
        if single and single not in names:
            names.insert(0, single)

        paths: list[Path] = []
        seen: set[str] = set()

        def _add(path: Path) -> None:
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                return
            if path.is_file():
                seen.add(key)
                paths.append(path)

        install_recovery = install_icons.list_recovery_icon_paths(
            self._service_id(),
            **self._template_lookup_kwargs(),
        )
        for path in install_recovery:
            _add(path)

        for name in names:
            resolved = self._templates_ctx.resolve_file(name)
            if resolved is not None:
                _add(resolved)
            else:
                self.log.warning("Template ausente para passo %s: %s", step.get("action"), name)
        return paths

    def _gather_tray_matches(self, flow: dict, confidence: float) -> list[dict]:
        records: list[dict] = []
        for kind, icon in self._install_state_entries():
            match = self._match_install_icon(icon, confidence=0.0)
            if match is None:
                continue
            icon_name = icon.source_path.name
            records.append(
                {
                    "kind": kind,
                    "icon": icon,
                    "match": match,
                    "threshold": self._icon_confidence(flow, icon_name, confidence),
                }
            )
        return records

    def _cluster_records(self, records: list[dict], tolerance_px: int) -> list[list[dict]]:
        clusters: list[list[dict]] = []
        for record in records:
            cx, cy = record["match"].center
            placed = False
            for cluster in clusters:
                ref = cluster[0]["match"].center
                if abs(cx - ref[0]) <= tolerance_px and abs(cy - ref[1]) <= tolerance_px:
                    cluster.append(record)
                    placed = True
                    break
            if not placed:
                clusters.append([record])
        return clusters

    def _cluster_kinds(self, cluster: list[dict]) -> set[str]:
        return {str(item["kind"]) for item in cluster}

    def _is_trustworthy_disconnected_cluster(self, cluster: list[dict]) -> bool:
        """Descarta disc isolado ou assets de instalação casando parcialmente com ícone saudável."""
        disc_hits = [item for item in cluster if item["kind"] == "disc"]
        if not disc_hits:
            return False
        kinds = self._cluster_kinds(cluster)
        if "sync" in kinds:
            return True
        if len(disc_hits) >= 2 and all(
            item["icon"].label in install_icons.FRAGILE_DISC_LABELS for item in disc_hits
        ):
            return False
        if "conn" in kinds:
            return not all(item["icon"].label in install_icons.FRAGILE_DISC_LABELS for item in disc_hits)
        if len(disc_hits) >= 2:
            return True
        label = disc_hits[0]["icon"].label
        return label not in install_icons.FRAGILE_DISC_LABELS

    @staticmethod
    def _disc_beats_conn(
        conn_score: float | None,
        disc_score: float | None,
        margin: float,
    ) -> bool:
        if disc_score is None:
            return False
        if conn_score is None:
            return True
        return disc_score > conn_score + margin

    def _cluster_tray_score(self, cluster: list[dict]) -> float:
        """Prioriza clusters com match forte de conectado/desconectado (ícone real na bandeja)."""
        conn_score = max(
            (item["match"].confidence for item in cluster if item["kind"] == "conn"),
            default=0.0,
        )
        disc_score = max(
            (item["match"].confidence for item in cluster if item["kind"] == "disc"),
            default=0.0,
        )
        sync_score = max(
            (item["match"].confidence for item in cluster if item["kind"] == "sync"),
            default=0.0,
        )
        kinds = self._cluster_kinds(cluster)
        count = len(cluster)

        tray_score = max(conn_score, disc_score)
        if tray_score >= 0.65:
            score = tray_score + 0.15
        else:
            score = sync_score

        if count >= 2:
            score += 0.20
        if "conn" in kinds and ("sync" in kinds or "disc" in kinds):
            score += 0.10

        if count == 1 and kinds == {"disc"}:
            score -= 0.35
        elif count == 1 and "disc" in kinds:
            label = cluster[0]["icon"].label
            if label in install_icons.FRAGILE_DISC_LABELS:
                score -= 0.25

        return score

    def _pick_best_cluster(self, clusters: list[list[dict]]) -> list[dict] | None:
        if not clusters:
            return None
        return max(clusters, key=self._cluster_tray_score)

    def _install_hint(self) -> str:
        return f"templates em {self._templates_dir} e TRAY_SCAN_REGION"

    def _region_cache_key(self, region: str | tuple[float, float, float, float] | None) -> str:
        if isinstance(region, tuple):
            return "custom"
        return (region or "tray").lower()

    def _get_capture(self, region: str | tuple[float, float, float, float] | None = "tray"):
        key = self._region_cache_key(region)
        if self._capture_cache is not None and key in self._capture_cache:
            return self._capture_cache[key]
        shot = sa.capture_region(region)
        if self._capture_cache is not None:
            self._capture_cache[key] = shot
        return shot

    def _match_template(
        self,
        template_path: Path,
        region: str | tuple[float, float, float, float] | None = "tray",
        confidence: float = 0.0,
    ) -> sa.MatchResult | None:
        screenshot, offset = self._get_capture(region)
        return sa.find_template(
            template_path,
            region=region,
            confidence=confidence,
            screenshot=screenshot,
            offset=offset,
        )

    def _template_path(self, name: str) -> Path:
        resolved = self._templates_ctx.resolve_file(name)
        return resolved if resolved is not None else self._templates_dir / name

    def load_flow(self) -> dict:
        flow_path = self._flow_path
        if not flow_path.exists():
            raise FileNotFoundError(f"Arquivo de fluxo não encontrado: {flow_path}")
        with flow_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("flow.json deve ser um objeto JSON")
        return data

    def flow_confidence(self, flow: dict) -> float:
        try:
            return float(flow.get("confidence", sa.DEFAULT_CONFIDENCE))
        except (TypeError, ValueError):
            return sa.DEFAULT_CONFIDENCE

    def _disconnected_paths(self, flow: dict) -> list[Path]:
        return install_icons.list_state_icon_paths(
            self._service_id(),
            "disconnected",
            **self._template_lookup_kwargs(),
        )

    def _connected_paths(self, flow: dict) -> list[Path]:
        return install_icons.list_state_icon_paths(
            self._service_id(),
            "connected",
            **self._template_lookup_kwargs(),
        )

    def _syncing_paths(self, flow: dict) -> list[Path]:
        return install_icons.list_state_icon_paths(
            self._service_id(),
            "syncing",
            **self._template_lookup_kwargs(),
        )

    def _icon_confidence(self, flow: dict, icon_name: str, default: float) -> float:
        for key in ("icon_confidence", "connected_icon_confidence", "disconnected_icon_confidence"):
            raw_map = flow.get(key)
            if not isinstance(raw_map, dict):
                continue
            raw = raw_map.get(icon_name)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return default

    def _disconnected_icon_names(self, flow: dict) -> list[str]:
        return [p.name for p in self._disconnected_paths(flow)]

    def _connected_icon_names(self, flow: dict) -> list[str]:
        return [p.name for p in self._connected_paths(flow)]

    def _syncing_icon_names(self, flow: dict) -> list[str]:
        return [p.name for p in self._syncing_paths(flow)]

    def _step_label(self, step: dict) -> str:
        action = str(step.get("action") or "").strip().lower()
        region = str(step.get("region") or "").lower()
        if action == "click" and region == "tray" and self._last_icon_match is not None:
            cx, cy = self._last_icon_match.center
            return f"icon@{cx},{cy}"
        raw_list = step.get("templates")
        if isinstance(raw_list, list) and raw_list:
            return " | ".join(str(item) for item in raw_list)
        return str(step.get("template") or "?")

    def _disconnected_margin(self, flow: dict) -> float:
        raw = flow.get("connected_disconnected_margin")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        return 0.10

    def _overlap_tolerance_px(self, flow: dict) -> int:
        raw = flow.get("tray_icon_overlap_px")
        if raw is not None:
            try:
                return max(4, int(raw))
            except (TypeError, ValueError):
                pass
        return 12

    def _state_for_tray_cluster(self, cluster: list[dict], margin: float, confidence: float) -> str | None:
        conn_hits = [item for item in cluster if item["kind"] == "conn"]
        disc_hits = [item for item in cluster if item["kind"] == "disc"]
        sync_hits = [item for item in cluster if item["kind"] == "sync"]
        conn_score = max((item["match"].confidence for item in conn_hits), default=None)
        disc_score = max((item["match"].confidence for item in disc_hits), default=None)
        trustworthy_disc = self._is_trustworthy_disconnected_cluster(cluster)

        best_disc = max(disc_hits, key=lambda item: item["match"].confidence, default=None)
        disc_threshold = (
            best_disc["threshold"]
            if best_disc is not None
            else confidence
        )

        if (
            trustworthy_disc
            and disc_score is not None
            and disc_score >= disc_threshold
            and self._disc_beats_conn(conn_score, disc_score, margin)
        ):
            return "disconnected"

        best_sync = max(sync_hits, key=lambda item: item["match"].confidence, default=None)
        if best_sync is not None and best_sync["match"].confidence >= best_sync["threshold"]:
            sync_val = best_sync["match"].confidence
            if conn_score is None or sync_val > conn_score:
                if (
                    disc_score is None
                    or sync_val > disc_score
                    or not self._disc_beats_conn(conn_score, disc_score, margin)
                ):
                    return "syncing"

        best_conn = max(conn_hits, key=lambda item: item["match"].confidence, default=None)
        if best_conn is not None:
            conn_val = best_conn["match"].confidence
            conn_thr = best_conn["threshold"]
            if conn_val >= conn_thr:
                return "connected"
            if (
                conn_val >= conn_thr - 0.05
                and not self._disc_beats_conn(conn_score, disc_score, margin)
            ):
                return "connected"

        if (
            trustworthy_disc
            and best_disc is not None
            and best_disc["match"].confidence >= confidence
            and self._disc_beats_conn(conn_score, disc_score, margin)
        ):
            return "disconnected"

        return None

    def _format_tray_scores(self, flow: dict, confidence: float, cluster: list[dict] | None) -> str:
        if not cluster:
            return "sem match na bandeja"
        chunks: list[str] = []
        for item in cluster:
            kind = item["kind"]
            icon: TrayIconTemplate = item["icon"]
            threshold = item["threshold"]
            match = item["match"]
            mark = "+" if match.confidence >= threshold else "-"
            chunks.append(f"{kind}:{icon.label}{mark}{match.confidence * 100:.0f}%")
        if self._last_icon_match is not None:
            cx, cy = self._last_icon_match.center
            chunks.insert(0, f"icon:{cx},{cy}")
        return ", ".join(chunks)

    def _emit_tray_diagnostics(self, flow: dict, confidence: float, state: str) -> None:
        records = self._gather_tray_matches(flow, confidence)
        cluster = self._pick_best_cluster(self._cluster_records(records, self._overlap_tolerance_px(flow)))
        summary = self._format_tray_scores(flow, confidence, cluster)
        self._emit_status(f"{self.config.service_name} [{state}] {summary}")

    def resolve_tray_state(self, flow: dict, confidence: float) -> str:
        """Retorna 'disconnected', 'syncing', 'connected' ou 'unknown'."""
        records = self._gather_tray_matches(flow, confidence)
        if not records:
            self._last_icon_match = None
            return "unknown"
        clusters = self._cluster_records(records, self._overlap_tolerance_px(flow))
        cluster = self._pick_best_cluster(clusters)
        if not cluster:
            self._last_icon_match = None
            return "unknown"
        best_match = max(cluster, key=lambda item: item["match"].confidence)
        self._last_icon_match = best_match["match"]
        margin = self._disconnected_margin(flow)
        state = self._state_for_tray_cluster(cluster, margin, confidence)
        return state or "unknown"

    def _is_healthy_state(self, state: str) -> bool:
        return state in ("connected", "syncing")

    def is_connected(self, flow: dict, confidence: float) -> bool:
        return self.resolve_tray_state(flow, confidence) in ("connected", "syncing")

    def is_disconnected(self, flow: dict, confidence: float) -> bool:
        if not self._disconnected_paths(flow):
            return False
        return self.resolve_tray_state(flow, confidence) == "disconnected"

    def _execute_step(self, step: dict, flow: dict, confidence: float) -> bool:
        action = str(step.get("action") or "").strip().lower()
        region = step.get("region") or "full"
        region_key = str(region).lower()

        if action == "click" and region_key == "tray":
            if self._last_icon_match is not None:
                cx, cy = self._last_icon_match.center
                sa.click_at(cx, cy)
                return True
            records = self._gather_tray_matches(flow, confidence)
            disc_records = [r for r in records if r["kind"] == "disc"]
            cluster = self._pick_best_cluster(
                self._cluster_records(disc_records, self._overlap_tolerance_px(flow))
            )
            if cluster:
                best = max(cluster, key=lambda item: item["match"].confidence)
                cx, cy = best["match"].center
                sa.click_at(cx, cy)
                return True
            return False

        templates = self._resolve_step_templates(flow, step)
        if not templates:
            return False

        step_confidence = step.get("confidence")
        try:
            conf = float(step_confidence) if step_confidence is not None else confidence
        except (TypeError, ValueError):
            conf = confidence

        timeout = float(step.get("timeout") or 15)
        stop_check = self.parar_event.is_set
        use_any = len(templates) > 1 or isinstance(step.get("templates"), list)
        scales = sa.tray_match_scales()
        match_mode = str(step.get("match_mode") or "grayscale").strip() or "grayscale"

        if action == "click":
            if use_any:
                return sa.click_any_template_masked_multiscale(
                    templates,
                    region=region,
                    confidence=conf,
                    prepare_tray=False,
                    scales=scales,
                    match_mode=match_mode,
                ) is not None
            return sa.click_any_template_masked_multiscale(
                [templates[0]],
                region=region,
                confidence=conf,
                prepare_tray=False,
                scales=scales,
                match_mode=match_mode,
            ) is not None

        if action == "wait_click":
            if use_any:
                return sa.wait_and_click_any_template_masked_multiscale(
                    templates,
                    region=region,
                    confidence=conf,
                    timeout=timeout,
                    stop_check=stop_check,
                    prepare_tray=False,
                    scales=scales,
                    match_mode=match_mode,
                ) is not None
            return sa.wait_and_click_any_template_masked_multiscale(
                [templates[0]],
                region=region,
                confidence=conf,
                timeout=timeout,
                stop_check=stop_check,
                prepare_tray=False,
                scales=scales,
                match_mode=match_mode,
            ) is not None

        if action == "wait":
            if use_any:
                return sa.wait_for_any_template(
                    templates,
                    region=region,
                    confidence=conf,
                    timeout=timeout,
                    stop_check=stop_check,
                ) is not None
            return sa.wait_for_template(
                templates[0],
                region=region,
                confidence=conf,
                timeout=timeout,
                stop_check=stop_check,
            ) is not None

        self.log.warning("Ação desconhecida no fluxo: %s", action)
        return False

    def _run_recovery_sequence(self, flow: dict, confidence: float) -> bool:
        steps = flow.get("steps") or []
        if not isinstance(steps, list) or not steps:
            self._emit_status("Nenhum passo configurado em flow.json")
            return False

        for index, step in enumerate(steps, start=1):
            if self.parar_event.is_set():
                return False
            if not isinstance(step, dict):
                continue
            action = step.get("action", "?")
            template_name = self._step_label(step)
            self._emit_status(f"Passo {index}/{len(steps)}: {action} -> {template_name}")
            self._emit_progress(40 + index * 10, f"Passo {index}: {template_name}")
            if not self._execute_step(step, flow, confidence):
                self._emit_status(f"Falha no passo {index} ({action} -> {template_name})")
                return False
            time.sleep(0.5)
        return True

    def _attempt_recovery(self, flow: dict, confidence: float) -> bool:
        name = self.config.service_name
        limit = self._recovery_failure_limit()
        if self._consecutive_recovery_failures >= limit:
            backoff_min = max(1, round(self._recovery_backoff_seconds() / 60))
            self._emit_status(
                f"{name}: reconexão pausada após {limit} falhas — retoma em ~{backoff_min} min"
            )
            self._emit_progress(0, "Reconexão em backoff")
            return False

        self._emit_status(f"{name} desconectado detectado — iniciando reconexão visual")
        self._emit_progress(30, "Desconectado detectado")

        if not self.is_disconnected(flow, confidence):
            self._emit_status("Ícone desconectado não confirmado na bandeja")
            self._emit_progress(0, "Ícone não encontrado")
            return False

        records = self._gather_tray_matches(flow, confidence)
        cluster = self._pick_best_cluster(
            self._cluster_records(records, self._overlap_tolerance_px(flow))
        )
        if cluster is None or not self._is_trustworthy_disconnected_cluster(cluster):
            self._emit_status(f"{name}: desconexão não confirmada (cluster isolado ou frágil)")
            self._emit_progress(0, "Desconexão não confirmada")
            return False

        if not self._run_recovery_sequence(flow, confidence):
            self._emit_progress(20, "Falha na sequência de cliques")
            self._consecutive_recovery_failures += 1
            return False

        wait_seconds = max(
            1,
            self._env_int(self.config.post_recover_wait_env, self.config.default_post_recover_wait_seconds),
        )
        self._emit_status(f"Aguardando {wait_seconds}s para validar reconexão...")
        if self.parar_event.wait(wait_seconds):
            return False

        self._capture_cache = {}
        if self._is_cisco_service():
            vpn = get_vpn_state()
            self._emit_status(f"{name} vpncli pós-recovery: {vpn.state} (cmd={vpn.command or 'n/a'})")
            if vpn.state == "connected":
                self._consecutive_recovery_failures = 0
                self._emit_status(f"{name} reconectado com sucesso (vpncli)")
                self._emit_progress(100, "OK")
                return True
            self._consecutive_recovery_failures += 1
            self._emit_status(f"Reconexão inconclusa — {name} ainda {vpn.state} no vpncli")
            self._emit_progress(50, "Reconexão inconclusa")
            return False

        state = self.resolve_tray_state(flow, confidence)
        if self._is_healthy_state(state):
            self._consecutive_recovery_failures = 0
            label = "sincronizando" if state == "syncing" else "reconectado"
            self._emit_status(f"{name} {label} com sucesso (validação visual)")
            self._emit_progress(100, "OK")
            return True

        self._consecutive_recovery_failures += 1
        self._emit_status(f"Reconexão inconclusa — {name} ainda com problemas")
        self._emit_progress(50, "Reconexão inconclusa")
        return False

    def _sync_config(self, flow: dict) -> dict:
        raw = flow.get("sync")
        if isinstance(raw, dict):
            return raw
        legacy = flow.get("sync_stuck_watch")
        if isinstance(legacy, dict):
            return {
                "enabled": True,
                "watch": legacy,
                "recovery": {"action": "restart_process"},
            }
        return {}

    def _sync_enabled(self, flow: dict) -> bool:
        cfg = self._sync_config(flow)
        if not cfg:
            return self.config.sync_stuck_watch_enabled
        raw = cfg.get("enabled")
        if raw is None:
            return self.config.sync_stuck_watch_enabled
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)

    def _sync_watch_config(self, flow: dict) -> dict:
        cfg = self._sync_config(flow)
        watch = cfg.get("watch")
        return watch if isinstance(watch, dict) else {}

    def _sync_recovery_config(self, flow: dict) -> dict:
        cfg = self._sync_config(flow)
        recovery = cfg.get("recovery")
        return recovery if isinstance(recovery, dict) else {}

    def _sync_stuck_poll_seconds(self, flow: dict) -> int:
        cfg = self._sync_watch_config(flow)
        raw = cfg.get("poll_seconds")
        if raw is not None:
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                pass
        return max(1, self._env_int(self.config.sync_stuck_poll_env, self.config.default_sync_stuck_poll_seconds))

    def _sync_stuck_window_seconds(self, flow: dict) -> int:
        cfg = self._sync_watch_config(flow)
        raw = cfg.get("window_seconds")
        if raw is not None:
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                pass
        return max(1, self._env_int(self.config.sync_stuck_window_env, self.config.default_sync_stuck_window_seconds))

    def _sync_recovery_action(self, flow: dict) -> str:
        cfg = self._sync_recovery_config(flow)
        action = str(cfg.get("action") or "restart_process").strip().lower()
        return action or "restart_process"

    def _sync_restart_cooldown_seconds(self, flow: dict | None = None) -> int:
        if flow is not None:
            cfg = self._sync_recovery_config(flow)
            raw = cfg.get("cooldown_seconds")
            if raw is not None:
                try:
                    return max(60, int(raw))
                except (TypeError, ValueError):
                    pass
        return max(60, self._env_int(self.config.sync_restart_cooldown_env, self.config.default_sync_restart_cooldown_seconds))

    def _sync_restart_wait_seconds(self, flow: dict | None = None) -> int:
        if flow is not None:
            cfg = self._sync_recovery_config(flow)
            raw = cfg.get("wait_after_seconds")
            if raw is not None:
                try:
                    return max(1, int(raw))
                except (TypeError, ValueError):
                    pass
        return max(1, self._env_int(self.config.sync_restart_wait_env, self.config.default_sync_restart_wait_seconds))

    def _handle_syncing_state(self, flow: dict, confidence: float) -> bool:
        name = self.config.service_name
        poll_seconds = self._sync_stuck_poll_seconds(flow)
        window_seconds = self._sync_stuck_window_seconds(flow)
        wait_restart = self._sync_restart_wait_seconds(flow)
        recovery_action = self._sync_recovery_action(flow)

        self._consecutive_recovery_failures = 0
        self._emit_status(
            f"{name} sincronizando — validando a cada {poll_seconds}s (janela {window_seconds}s)"
        )
        self._emit_progress(60, "Sincronizando — vigilância ativa")

        started = time.monotonic()
        deadline = started + window_seconds
        last_status_at = started
        consecutive_disconnected = 0
        disconnected_confirm_polls = 2

        while time.monotonic() < deadline:
            if self.parar_event.wait(poll_seconds):
                self._emit_status(f"Vigilância de sync interrompida ({name})")
                return False

            self._capture_cache = {}
            state = self.resolve_tray_state(flow, confidence)
            now = time.monotonic()
            elapsed = int(now - started)

            if state != "syncing":
                self._emit_status(f"{name}: sync encerrou (estado: {state})")
                self._emit_tray_diagnostics(flow, confidence, state)
                if state == "disconnected":
                    consecutive_disconnected += 1
                    if consecutive_disconnected < disconnected_confirm_polls:
                        self._emit_status(
                            f"{name}: desconexão transitória "
                            f"({consecutive_disconnected}/{disconnected_confirm_polls}) — aguardando confirmação"
                        )
                        continue
                    return self._attempt_recovery(flow, confidence)
                consecutive_disconnected = 0
                if self._is_healthy_state(state):
                    self._emit_progress(100, f"{name} OK após sync")
                    return True
                self._emit_status(f"Estado do {name} indeterminado após sync")
                self._emit_progress(0, "Estado indeterminado")
                return False

            consecutive_disconnected = 0

            if now - last_status_at >= 30:
                self._emit_status(
                    f"{name} [syncing] validação {min(elapsed, window_seconds)}/{window_seconds}s — ainda sincronizando"
                )
                last_status_at = now

        now = time.monotonic()
        if now < self._sync_restart_cooldown_until:
            remaining = max(1, int(self._sync_restart_cooldown_until - now))
            self._emit_status(
                f"{name}: sync > {window_seconds}s — reinício em cooldown (~{remaining}s restantes)"
            )
            self._emit_progress(100, f"{name} sincronizando (cooldown)")
            return True

        if recovery_action == "none":
            self._emit_status(
                f"{name}: sync > {window_seconds}s — vigilância encerrada (recovery desabilitada no flow)"
            )
            self._emit_progress(100, f"{name} sincronizando (sem reinício)")
            return True

        self._emit_status(
            f"{name}: sync > {window_seconds}s — reiniciando processo (taskkill + /background)"
        )
        self._emit_progress(70, "Reiniciando OneDrive")

        if not restart_onedrive_background():
            self._emit_status(f"{name}: falha ao reiniciar OneDrive.exe")
            self._emit_progress(50, "Falha no reinício")
            return False

        self._sync_restart_cooldown_until = time.monotonic() + self._sync_restart_cooldown_seconds(flow)
        self._emit_status(f"Aguardando {wait_restart}s após reinício do OneDrive...")
        if self.parar_event.wait(wait_restart):
            return False

        self._capture_cache = {}
        state = self.resolve_tray_state(flow, confidence)
        self._emit_tray_diagnostics(flow, confidence, state)

        if self._is_healthy_state(state):
            self._emit_status(f"{name} recuperado após reinício (estado: {state})")
            self._emit_progress(100, "OK após reinício")
            return True

        if state == "disconnected":
            return self._attempt_recovery(flow, confidence)

        self._emit_status(f"{name}: reinício concluído mas estado ainda problemático ({state})")
        self._emit_progress(50, "Reinício inconcluso")
        return False

    def run_cycle(self, flow: dict, confidence: float) -> bool:
        self._begin_cycle_cache()
        try:
            if self._is_onedrive_service() and not is_onedrive_running():
                self._attempt_start_onedrive_if_missing(flow, confidence)

            if self._is_cisco_service():
                self._emit_progress(10, "Verificando vpncli")
                vpn = get_vpn_state()
                self._emit_status(
                    f"{self.config.service_name} vpncli: {vpn.state}"
                    + (f" ({vpn.command})" if vpn.command else "")
                )
                if vpn.state == "connected":
                    self._consecutive_unknown = 0
                    self._consecutive_recovery_failures = 0
                    self._emit_progress(100, f"{self.config.service_name} OK")
                    return True
                if vpn.state == "disconnected":
                    self._consecutive_unknown = 0
                    return self._attempt_recovery(flow, confidence)
                self._emit_status(
                    f"{self.config.service_name}: vpncli inconclusivo — fallback visual na bandeja"
                )

            self._emit_progress(10, "Verificando bandeja")
            state = self.resolve_tray_state(flow, confidence)
            self._emit_tray_diagnostics(flow, confidence, state)

            if state == "unknown":
                if self._is_onedrive_service() and not is_onedrive_running():
                    started = self._attempt_start_onedrive_if_missing(flow, confidence)
                    if started:
                        state = self.resolve_tray_state(flow, confidence)
                        if state == "connected":
                            self._emit_progress(100, f"{self.config.service_name} OK")
                            return True
                        if state == "syncing":
                            if self._sync_enabled(flow):
                                return self._handle_syncing_state(flow, confidence)
                            self._emit_progress(100, f"{self.config.service_name} sincronizando")
                            return True
                        if state == "disconnected":
                            return self._attempt_recovery(flow, confidence)
                self._consecutive_unknown += 1
                if self._consecutive_unknown >= self._unknown_limit:
                    self._emit_status(
                        f"{self.config.service_name}: {self._unknown_limit} ciclos unknown — "
                        f"{self._install_hint()}"
                    )
                self._emit_status(f"Estado do {self.config.service_name} indeterminado na bandeja")
                self._emit_progress(0, "Estado indeterminado")
                return False

            self._consecutive_unknown = 0

            if state == "connected":
                self._consecutive_recovery_failures = 0
                self._emit_progress(100, f"{self.config.service_name} OK")
                return True

            if state == "syncing":
                if self._sync_enabled(flow):
                    return self._handle_syncing_state(flow, confidence)
                self._consecutive_recovery_failures = 0
                self._emit_progress(100, f"{self.config.service_name} sincronizando")
                return True

            if state == "disconnected":
                return self._attempt_recovery(flow, confidence)

            self._emit_status(f"Estado do {self.config.service_name} indeterminado (ícone não visível)")
            self._emit_progress(0, "Estado indeterminado")
            return False
        finally:
            self._end_cycle_cache()

    def validate_flow(self, flow: dict) -> bool:
        if not install_icons.has_minimum_icons(
            self._service_id(),
            **self._template_lookup_kwargs(),
        ):
            self._emit_status(
                f"{self.config.service_name}: templates ausentes em {self._templates_dir} "
                "(esperado connected.png após seed do repositório)"
            )
            self._emit_progress(0, "Templates ausentes")
            return False
        return True

    def run_loop(self, settings=None):
        del settings
        name = self.config.service_name

        if os.name != "nt":
            self._emit_status(f"Bot {name} UI disponível apenas em Windows")
            self._emit_progress(0, "Ambiente não suportado")
            return

        try:
            flow = self.load_flow()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._emit_status(f"Erro ao carregar flow.json: {exc}")
            self._emit_progress(0, "flow.json inválido")
            return

        confidence = self.flow_confidence(flow)
        interval_seconds = self._check_interval_seconds()
        interval_minutes = max(1, round(interval_seconds / 60))

        self._emit_status(f"Bot {name} UI iniciado")
        self._emit_progress(0, f"Monitoramento visual a cada {interval_minutes} min")

        if not self.validate_flow(flow):
            self._emit_status(f"Bot {name} UI encerrado — templates ausentes")
            return

        while not self.parar_event.is_set():
            try:
                cycle_ok = self.run_cycle(flow, confidence)
                if not self.parar_event.is_set():
                    if cycle_ok:
                        self._emit_status(f"{name} OK — próxima verificação em {interval_minutes} min")
                    else:
                        self._emit_status(
                            f"Problema ou reconexão pendente no {name} — nova tentativa em {interval_minutes} min"
                        )
            except Exception as exc:
                self.log.exception("Erro no ciclo de monitoramento %s UI", name)
                self._emit_progress(0, f"Erro no ciclo: {exc}")
                self._emit_status(f"Erro no monitoramento {name} UI: {exc}")

            if self.parar_event.wait(interval_seconds):
                break

        self._emit_status(f"Bot {name} UI parado")
        self._emit_progress(0, "Parado")

    def start(self, settings=None):
        if self.parar_event.is_set():
            self.parar_event.clear()
        worker = threading.Thread(target=self.run_loop, args=(settings,), daemon=True)
        worker.start()
        return worker

    def stop(self):
        self.parar_event.set()


DEFAULT_TRAY_SERVICES: tuple[TrayUiBotConfig, ...] = (
    TrayUiBotConfig(
        service_name="OneDrive",
        templates_subdir="onedrive",
        check_interval_env="ONEDRIVE_UI_CHECK_INTERVAL_SECONDS",
        post_recover_wait_env="ONEDRIVE_UI_POST_RECOVER_WAIT_SECONDS",
        sync_stuck_watch_enabled=True,
    ),
    TrayUiBotConfig(
        service_name="Cisco",
        templates_subdir="cisco",
        check_interval_env="CISCO_UI_CHECK_INTERVAL_SECONDS",
        post_recover_wait_env="CISCO_UI_POST_RECOVER_WAIT_SECONDS",
    ),
)


class CombinedTrayUiBot:
    """Monitora vários serviços da bandeja (OneDrive, Cisco, etc.) em um único loop."""

    def __init__(
        self,
        configs: tuple[TrayUiBotConfig, ...] | list[TrayUiBotConfig] = DEFAULT_TRAY_SERVICES,
    ):
        self.configs = tuple(configs)
        self.log = logging.getLogger("robots.tray_ui")
        self.parar_event = threading.Event()
        self._bots = [TrayUiBot(config, self.parar_event) for config in self.configs]
        self._status_callback = None
        self._progress_callback = None

    def set_status_callback(self, fn):
        self._status_callback = fn
        for bot in self._bots:
            bot.set_status_callback(fn)

    def set_progress_callback(self, fn):
        self._progress_callback = fn
        for bot in self._bots:
            bot.set_progress_callback(fn)

    def _env_int(self, name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    def _check_interval_seconds(self) -> int:
        for env_name in (
            "TRAY_UI_CHECK_INTERVAL_SECONDS",
            "ONEDRIVE_UI_CHECK_INTERVAL_SECONDS",
            "CISCO_UI_CHECK_INTERVAL_SECONDS",
        ):
            raw = os.getenv(env_name, "").strip()
            if raw:
                return max(15, self._env_int(env_name, 60))
        return 60

    def run_cycle(self) -> bool:
        all_ok = True
        total = len(self._bots)
        for index, bot in enumerate(self._bots, start=1):
            if self.parar_event.is_set():
                return False
            try:
                flow = bot.load_flow()
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                bot._emit_status(f"Erro ao carregar flow de {bot.config.service_name}: {exc}")
                all_ok = False
                continue
            if not bot.validate_flow(flow):
                all_ok = False
                continue
            confidence = bot.flow_confidence(flow)
            base_pct = int((index - 1) / total * 100)
            bot._emit_progress(base_pct, f"Verificando {bot.config.service_name}")
            service_ok = bot.run_cycle(flow, confidence)
            if not service_ok:
                all_ok = False
        return all_ok

    def run_loop(self, settings=None):
        del settings

        if os.name != "nt":
            names = ", ".join(bot.config.service_name for bot in self._bots)
            for bot in self._bots:
                bot._emit_status(f"Bot bandeja ({names}) disponível apenas em Windows")
            self._bots[0]._emit_progress(0, "Ambiente não suportado")
            return

        interval_seconds = self._check_interval_seconds()
        interval_minutes = max(1, round(interval_seconds / 60))
        names = ", ".join(bot.config.service_name for bot in self._bots)

        self._bots[0]._emit_status(f"Bot bandeja iniciado ({names})")
        self._bots[0]._emit_progress(0, f"Monitoramento visual a cada {interval_minutes} min")

        for bot in self._bots:
            try:
                flow = bot.load_flow()
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                bot._emit_status(f"Erro ao carregar flow.json ({bot.config.service_name}): {exc}")
                bot._emit_progress(0, "flow.json inválido")
                return
            if not bot.validate_flow(flow):
                bot._emit_status(f"Bot bandeja encerrado — templates ausentes ({bot.config.service_name})")
                return

        while not self.parar_event.is_set():
            try:
                cycle_ok = self.run_cycle()
                if not self.parar_event.is_set():
                    if cycle_ok:
                        self._bots[0]._emit_status(
                            f"Bandeja OK ({names}) — próxima verificação em {interval_minutes} min"
                        )
                        self._bots[0]._emit_progress(100, "Tudo OK")
                    else:
                        self._bots[0]._emit_status(
                            f"Problema na bandeja ({names}) — nova tentativa em {interval_minutes} min"
                        )
            except Exception as exc:
                self.log.exception("Erro no ciclo de monitoramento da bandeja")
                self._bots[0]._emit_progress(0, f"Erro no ciclo: {exc}")
                self._bots[0]._emit_status(f"Erro no monitoramento da bandeja: {exc}")

            if self.parar_event.wait(interval_seconds):
                break

        self._bots[0]._emit_status("Bot bandeja parado")
        self._bots[0]._emit_progress(0, "Parado")

    def start(self, settings=None):
        if self.parar_event.is_set():
            self.parar_event.clear()
        worker = threading.Thread(target=self.run_loop, args=(settings,), daemon=True)
        worker.start()
        return worker

    def stop(self):
        self.parar_event.set()

    def get_service_bot(self, templates_subdir: str) -> TrayUiBot | None:
        for bot in self._bots:
            if bot.config.templates_subdir == templates_subdir:
                return bot
        return None
