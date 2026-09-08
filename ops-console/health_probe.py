"""Shared health probe coordinator for OPS-console.

Single-flight + TTL cache + stale-while-revalidate + backoff.
Used by overview, overview-lite, monitoring collector, and availability.
"""
from __future__ import annotations

import json
import logging
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger("ops.health_probe")

ENV_ORDER = ("MAIN", "DEV", "HOM")


def _is_env_enabled(config: dict[str, Any], env_name: str) -> bool:
    """Missing enabled defaults to True (backward compatible)."""
    env_cfg = config.get(env_name)
    if not isinstance(env_cfg, dict):
        return False
    enabled = env_cfg.get("enabled")
    if enabled is None:
        return True
    return bool(enabled)


def disabled_env_snapshot(env_name: str) -> dict[str, Any]:
    return {
        "reachable": False,
        "httpStatus": None,
        "status": "disabled",
        "database": None,
        "version": None,
        "components": {},
        "durationMs": 0,
        "error": "environment disabled",
        "checkedAt": _utc_now_iso(),
        "timedOut": False,
        "availabilityClass": "offline",
        "backendPortUp": False,
        "frontendPortUp": False,
        "consecutiveFailures": 0,
        "fromCache": True,
        "stale": False,
        "envDisabled": True,
        "ageMs": 0,
        "probeInFlight": False,
    }


# Tunables (override via configure() or env later).
DEFAULT_CACHE_TTL_SEC = 5.0
DEFAULT_PROBE_TIMEOUT_SEC = 1.0
DEFAULT_BACKOFF_INITIAL_SEC = 5.0
DEFAULT_BACKOFF_MAX_SEC = 60.0
DEFAULT_STALE_WINDOW_SEC = 120.0
DEFAULT_OFFLINE_FAILURES = 3
DEFAULT_GLOBAL_CONCURRENCY = 3
DEFAULT_PORT_TIMEOUT_SEC = 0.4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProbeConfig:
    cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC
    probe_timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC
    backoff_initial_sec: float = DEFAULT_BACKOFF_INITIAL_SEC
    backoff_max_sec: float = DEFAULT_BACKOFF_MAX_SEC
    stale_window_sec: float = DEFAULT_STALE_WINDOW_SEC
    offline_failures: int = DEFAULT_OFFLINE_FAILURES
    global_concurrency: int = DEFAULT_GLOBAL_CONCURRENCY
    port_timeout_sec: float = DEFAULT_PORT_TIMEOUT_SEC


@dataclass
class EnvProbeState:
    last_result: dict[str, Any] | None = None
    last_valid_result: dict[str, Any] | None = None
    checked_at_mono: float = 0.0
    consecutive_failures: int = 0
    next_allowed_at: float = 0.0
    probe_future: Future | None = None
    last_failure: str | None = None
    last_state: str = "unknown"
    backend_port_up: bool | None = None
    frontend_port_up: bool | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class HealthProbeCoordinator:
    """One probe in flight per env; shared snapshots across overview + collector."""

    def __init__(self, config: ProbeConfig | None = None) -> None:
        self.cfg = config or ProbeConfig()
        self._states: dict[str, EnvProbeState] = {e: EnvProbeState() for e in ENV_ORDER}
        self._sem = threading.Semaphore(max(1, int(self.cfg.global_concurrency)))
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(self.cfg.global_concurrency)),
            thread_name_prefix="health-probe",
        )
        self._metrics_lock = threading.Lock()
        self._metrics: dict[str, Any] = {
            "health_probe_timeout_total": 0,
            "health_probe_cache_hit_total": 0,
            "health_probe_inflight": 0,
            "health_probe_executed_total": 0,
            "health_probe_coalesced_total": 0,
            "overview_cache_hit": 0,
        }
        self._last_log_at: dict[str, float] = {}
        self._http_probe_fn: Callable[..., dict[str, Any]] | None = None

    def configure(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self.cfg, key) and value is not None:
                setattr(self.cfg, key, value)
        # Rebuild semaphore if concurrency changed.
        self._sem = threading.Semaphore(max(1, int(self.cfg.global_concurrency)))

    def reset_for_tests(self) -> None:
        """Clear caches/state between unit tests."""
        for env in list(self._states):
            self._states[env] = EnvProbeState()
        with self._metrics_lock:
            for key in list(self._metrics):
                self._metrics[key] = 0
        self._last_log_at.clear()

    def metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            out = dict(self._metrics)
        inflight = sum(1 for s in self._states.values() if s.probe_future and not s.probe_future.done())
        out["health_probe_inflight"] = inflight
        ages = {}
        for env, st in self._states.items():
            if st.checked_at_mono:
                ages[env] = int((time.monotonic() - st.checked_at_mono) * 1000)
        out["health_snapshot_age_ms"] = ages
        out["health_consecutive_failures"] = {
            env: st.consecutive_failures for env, st in self._states.items()
        }
        return out

    def _bump(self, key: str, delta: int = 1) -> None:
        with self._metrics_lock:
            self._metrics[key] = int(self._metrics.get(key) or 0) + delta

    def _rate_limited_log(self, key: str, level: int, msg: str, *args: Any) -> None:
        now = time.monotonic()
        last = self._last_log_at.get(key, 0.0)
        if now - last < 15.0:
            return
        self._last_log_at[key] = now
        log.log(level, msg, *args)

    def _state(self, env_name: str) -> EnvProbeState:
        env = env_name.upper()
        if env not in self._states:
            self._states[env] = EnvProbeState()
        return self._states[env]

    @staticmethod
    def health_url(config: dict[str, Any], env_name: str) -> str | None:
        env_cfg = config.get(env_name, {}) or {}
        port = env_cfg.get("backendPort")
        if port is None:
            return None
        return f"http://127.0.0.1:{int(port)}/api/v1/health/"

    def probe_port(self, port: int | None) -> bool:
        if not port:
            return False
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=self.cfg.port_timeout_sec):
                return True
        except OSError:
            return False

    def _raw_http_probe(self, url: str, timeout: float) -> dict[str, Any]:
        if self._http_probe_fn is not None:
            return self._http_probe_fn(url, timeout)
        start = time.perf_counter()
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body) if body else {}
                duration_ms = int((time.perf_counter() - start) * 1000)
                return {
                    "reachable": True,
                    "httpStatus": response.status,
                    "status": data.get("status"),
                    "database": data.get("database"),
                    "version": data.get("version"),
                    "components": data.get("components") or {},
                    "durationMs": duration_ms,
                    "error": None,
                    "checkedAt": _utc_now_iso(),
                    "timedOut": False,
                }
        except urllib.error.HTTPError as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            return {
                "reachable": True,
                "httpStatus": exc.code,
                "status": "unhealthy",
                "database": "error",
                "version": None,
                "components": {},
                "durationMs": duration_ms,
                "error": detail or str(exc),
                "checkedAt": _utc_now_iso(),
                "timedOut": False,
            }
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - start) * 1000)
            err = str(exc)
            timed_out = "timed out" in err.lower() or "timeout" in err.lower()
            if timed_out:
                self._bump("health_probe_timeout_total")
            return {
                "reachable": False,
                "httpStatus": None,
                "status": "offline",
                "database": None,
                "version": None,
                "components": {},
                "durationMs": duration_ms,
                "error": err,
                "checkedAt": _utc_now_iso(),
                "timedOut": timed_out,
            }

    def classify(
        self,
        *,
        runtime: dict[str, Any],
        backend_port_up: bool | None,
        consecutive_failures: int,
        age_ms: int | None,
        stale: bool,
    ) -> str:
        """
        Availability class for UI/ops:
        - healthy / degraded / saturated / stale / offline / unknown
        """
        timed_out = bool(runtime.get("timedOut"))
        reachable = bool(runtime.get("reachable"))
        status = str(runtime.get("status") or "")
        db = runtime.get("database")

        if backend_port_up is False and consecutive_failures >= self.cfg.offline_failures:
            return "offline"
        if backend_port_up is False and not reachable and consecutive_failures >= self.cfg.offline_failures:
            return "offline"
        if timed_out and backend_port_up:
            return "saturated"
        if stale and runtime.get("lastValidUsed"):
            return "stale"
        if reachable and status in ("healthy",):
            return "healthy"
        if reachable and (status in ("degraded", "unhealthy") or db == "error"):
            return "degraded"
        if not reachable and backend_port_up:
            return "saturated"
        if not reachable and consecutive_failures >= self.cfg.offline_failures:
            return "offline"
        if age_ms is not None and age_ms > int(self.cfg.stale_window_sec * 1000):
            return "stale"
        return status or "unknown"

    def _enrich(
        self,
        env_name: str,
        runtime: dict[str, Any],
        *,
        st: EnvProbeState,
        from_cache: bool,
        stale: bool,
    ) -> dict[str, Any]:
        now = time.monotonic()
        age_ms = int((now - st.checked_at_mono) * 1000) if st.checked_at_mono else None
        out = dict(runtime)
        out["environment"] = env_name
        out["ageMs"] = age_ms
        out["stale"] = stale
        out["fromCache"] = from_cache
        out["probeInFlight"] = bool(st.probe_future and not st.probe_future.done())
        out["consecutiveFailures"] = st.consecutive_failures
        out["backendPortUp"] = st.backend_port_up
        out["frontendPortUp"] = st.frontend_port_up
        out["lastFailure"] = st.last_failure
        out["availabilityClass"] = self.classify(
            runtime=out,
            backend_port_up=st.backend_port_up,
            consecutive_failures=st.consecutive_failures,
            age_ms=age_ms,
            stale=stale,
        )
        out["health_probe_duration_ms"] = out.get("durationMs")
        out["health_snapshot_age_ms"] = age_ms
        return out

    def _execute_probe(self, config: dict[str, Any], env_name: str) -> dict[str, Any]:
        env_cfg = config.get(env_name, {}) or {}
        backend_port = int(env_cfg.get("backendPort") or 0)
        frontend_port = int(env_cfg.get("frontendPort") or 0)
        url = self.health_url(config, env_name)

        # Jitter so MAIN/DEV/HOM don't align.
        time.sleep(random.uniform(0.0, 0.05))

        acquired = self._sem.acquire(timeout=self.cfg.probe_timeout_sec + 0.5)
        if not acquired:
            return {
                "reachable": False,
                "httpStatus": None,
                "status": "offline",
                "database": None,
                "version": None,
                "components": {},
                "durationMs": 0,
                "error": "global concurrency limit",
                "checkedAt": _utc_now_iso(),
                "timedOut": True,
                "backendPortUp": None,
                "frontendPortUp": None,
            }

        try:
            backend_up = self.probe_port(backend_port) if backend_port else False
            frontend_up = self.probe_port(frontend_port) if frontend_port else False
            if not url:
                result = {
                    "reachable": False,
                    "httpStatus": None,
                    "status": "offline",
                    "database": None,
                    "version": None,
                    "components": {},
                    "durationMs": 0,
                    "error": "backend port not configured",
                    "checkedAt": _utc_now_iso(),
                    "timedOut": False,
                }
            else:
                result = self._raw_http_probe(url, self.cfg.probe_timeout_sec)
            result["backendPortUp"] = backend_up
            result["frontendPortUp"] = frontend_up
            return result
        finally:
            self._sem.release()

    def _apply_result(self, env_name: str, result: dict[str, Any]) -> dict[str, Any]:
        st = self._state(env_name)
        now = time.monotonic()
        with st.lock:
            st.checked_at_mono = now
            st.backend_port_up = result.get("backendPortUp")
            st.frontend_port_up = result.get("frontendPortUp")
            st.probe_future = None

            reachable = bool(result.get("reachable"))
            timed_out = bool(result.get("timedOut"))
            success = reachable and result.get("status") in ("healthy", "degraded")

            if success:
                st.consecutive_failures = 0
                st.last_failure = None
                st.next_allowed_at = now  # success clears backoff
                st.last_valid_result = dict(result)
                st.last_result = dict(result)
            else:
                st.consecutive_failures += 1
                st.last_failure = str(result.get("error") or result.get("status") or "probe failed")
                # Exponential backoff with jitter.
                exp = min(
                    self.cfg.backoff_max_sec,
                    self.cfg.backoff_initial_sec * (2 ** max(0, st.consecutive_failures - 1)),
                )
                st.next_allowed_at = now + exp + random.uniform(0.0, 0.5)
                # Preserve last valid within stale window for SWR.
                if st.last_valid_result and st.checked_at_mono:
                    # last_valid age tracked via separate field on result
                    pass
                if timed_out and st.backend_port_up and st.last_valid_result:
                    # Serve stale while marking saturated.
                    merged = dict(st.last_valid_result)
                    merged["timedOut"] = True
                    merged["error"] = result.get("error")
                    merged["durationMs"] = result.get("durationMs")
                    merged["lastValidUsed"] = True
                    merged["checkedAt"] = result.get("checkedAt")
                    merged["backendPortUp"] = st.backend_port_up
                    merged["frontendPortUp"] = st.frontend_port_up
                    st.last_result = merged
                    result = merged
                else:
                    st.last_result = dict(result)

            prev = st.last_state
            enriched = self._enrich(env_name, st.last_result or result, st=st, from_cache=False, stale=False)
            new_state = enriched.get("availabilityClass") or "unknown"
            if new_state != prev:
                self._rate_limited_log(
                    f"transition:{env_name}",
                    logging.WARNING if new_state in ("offline", "saturated") else logging.INFO,
                    "health state %s: %s -> %s failures=%s",
                    env_name,
                    prev,
                    new_state,
                    st.consecutive_failures,
                )
                st.last_state = str(new_state)
            elif not success:
                self._rate_limited_log(
                    f"fail:{env_name}",
                    logging.WARNING,
                    "health probe failed env=%s err=%s failures=%s",
                    env_name,
                    st.last_failure,
                    st.consecutive_failures,
                )
            self._bump("health_probe_executed_total")
            return enriched

    def _start_probe(self, config: dict[str, Any], env_name: str) -> Future:
        st = self._state(env_name)

        def _run() -> dict[str, Any]:
            raw = self._execute_probe(config, env_name)
            return self._apply_result(env_name, raw)

        fut = self._pool.submit(_run)
        st.probe_future = fut
        return fut

    def get_snapshot(
        self,
        config: dict[str, Any],
        env_name: str,
        *,
        wait: bool = True,
        force: bool = False,
        allow_stale: bool = True,
    ) -> dict[str, Any]:
        """
        Return health snapshot for env.

        wait=False: never block on probe; return cache/placeholder and maybe kick revalidate.
        wait=True: may wait for in-flight or start+wait when cache miss (still single-flight).
        """
        env_name = env_name.upper()
        if not _is_env_enabled(config, env_name):
            return disabled_env_snapshot(env_name)

        st = self._state(env_name)
        now = time.monotonic()

        with st.lock:
            age = (now - st.checked_at_mono) if st.checked_at_mono else None
            fresh = age is not None and age < self.cfg.cache_ttl_sec
            within_stale = age is not None and age < self.cfg.stale_window_sec
            backoff_blocked = now < st.next_allowed_at and not force

            if fresh and st.last_result and not force:
                self._bump("health_probe_cache_hit_total")
                return self._enrich(env_name, st.last_result, st=st, from_cache=True, stale=False)

            inflight = st.probe_future if (st.probe_future and not st.probe_future.done()) else None

            if within_stale and st.last_result and not force:
                # Stale-while-revalidate: return immediately, refresh in background.
                if not inflight and not backoff_blocked:
                    self._start_probe(config, env_name)
                self._bump("health_probe_cache_hit_total")
                self._bump("overview_cache_hit")
                return self._enrich(env_name, st.last_result, st=st, from_cache=True, stale=True)

            if allow_stale and st.last_valid_result and within_stale and not force:
                if not inflight and not backoff_blocked:
                    self._start_probe(config, env_name)
                self._bump("health_probe_cache_hit_total")
                merged = dict(st.last_valid_result)
                merged["lastValidUsed"] = True
                return self._enrich(env_name, merged, st=st, from_cache=True, stale=True)

            if inflight:
                self._bump("health_probe_coalesced_total")
                fut = inflight
            elif backoff_blocked and st.last_result:
                self._bump("health_probe_cache_hit_total")
                return self._enrich(env_name, st.last_result, st=st, from_cache=True, stale=True)
            else:
                fut = self._start_probe(config, env_name)

        if not wait:
            with st.lock:
                if st.last_result:
                    return self._enrich(env_name, st.last_result, st=st, from_cache=True, stale=True)
                if st.last_valid_result:
                    merged = dict(st.last_valid_result)
                    merged["lastValidUsed"] = True
                    return self._enrich(env_name, merged, st=st, from_cache=True, stale=True)
            # Placeholder while first probe runs.
            return self._enrich(
                env_name,
                {
                    "reachable": False,
                    "httpStatus": None,
                    "status": "unknown",
                    "database": None,
                    "version": None,
                    "components": {},
                    "durationMs": 0,
                    "error": "probe pending",
                    "checkedAt": _utc_now_iso(),
                    "timedOut": False,
                    "probePending": True,
                },
                st=st,
                from_cache=False,
                stale=True,
            )

        try:
            return fut.result(timeout=self.cfg.probe_timeout_sec + 1.0)
        except Exception as exc:  # noqa: BLE001
            with st.lock:
                if st.last_valid_result:
                    merged = dict(st.last_valid_result)
                    merged["lastValidUsed"] = True
                    merged["error"] = str(exc)
                    return self._enrich(env_name, merged, st=st, from_cache=True, stale=True)
            return self._enrich(
                env_name,
                {
                    "reachable": False,
                    "httpStatus": None,
                    "status": "offline",
                    "database": None,
                    "version": None,
                    "components": {},
                    "durationMs": 0,
                    "error": str(exc),
                    "checkedAt": _utc_now_iso(),
                    "timedOut": True,
                },
                st=st,
                from_cache=False,
                stale=False,
            )

    def get_all_snapshots(
        self,
        config: dict[str, Any],
        *,
        wait: bool = True,
        force: bool = False,
        envs: tuple[str, ...] | None = None,
    ) -> dict[str, dict[str, Any]]:
        targets = envs or ENV_ORDER
        return {
            env: self.get_snapshot(config, env, wait=wait, force=force)
            for env in targets
            if config.get(env)
        }

    def peek(self, env_name: str) -> dict[str, Any] | None:
        st = self._state(env_name)
        with st.lock:
            if not st.last_result:
                return None
            return self._enrich(env_name, st.last_result, st=st, from_cache=True, stale=True)


# Process-wide singleton
coordinator = HealthProbeCoordinator()


def get_coordinator() -> HealthProbeCoordinator:
    return coordinator
