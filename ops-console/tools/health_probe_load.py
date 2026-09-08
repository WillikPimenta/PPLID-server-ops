"""Load / probe amplification harness for OPS-console health coordinator.

Measures overview-lite latency and actual probe counts under concurrent clients.

  python tools/health_probe_load.py
  python tools/health_probe_load.py --clients 10 --seconds 30 --slow
  python tools/health_probe_load.py --out tools/health_probe_after.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

OPS_CONSOLE = Path(__file__).resolve().parents[1]
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import health_probe as hp  # noqa: E402
import server  # noqa: E402
import server_ops  # noqa: E402


def percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[idx]


class SlowHealthHandler(BaseHTTPRequestHandler):
    delay_sec = 0.0
    hits = 0
    hits_lock = threading.Lock()
    max_inflight = 0
    inflight = 0

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        with SlowHealthHandler.hits_lock:
            SlowHealthHandler.hits += 1
            SlowHealthHandler.inflight += 1
            SlowHealthHandler.max_inflight = max(
                SlowHealthHandler.max_inflight, SlowHealthHandler.inflight
            )
        try:
            if path.endswith("/health/live/"):
                body = b'{"status":"alive","ok":true}'
            elif "health" in path:
                if SlowHealthHandler.delay_sec > 0:
                    time.sleep(SlowHealthHandler.delay_sec)
                body = b'{"status":"healthy","database":"ok","version":"bench","components":{"backend":"ok","database":"ok","migrations":"ok"}}'
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with SlowHealthHandler.hits_lock:
                SlowHealthHandler.inflight -= 1


def start_fake_backends(ports: list[int], delay: float) -> list[ThreadingHTTPServer]:
    SlowHealthHandler.delay_sec = delay
    SlowHealthHandler.hits = 0
    SlowHealthHandler.max_inflight = 0
    SlowHealthHandler.inflight = 0
    servers: list[ThreadingHTTPServer] = []
    for port in ports:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), SlowHealthHandler)
        httpd.daemon_threads = True
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        servers.append(httpd)
    return servers


def build_config(ports: dict[str, int]) -> dict[str, Any]:
    log_dir = OPS_CONSOLE / "tmp-load-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    status = log_dir / "deploy-status.json"
    status.write_text("{}", encoding="utf-8")
    cfg: dict[str, Any] = {
        "logDir": str(log_dir),
        "statusFile": str(status),
        "lanIp": "127.0.0.1",
    }
    for env, port in ports.items():
        cfg[env] = {
            "backendPort": port,
            "frontendPort": port + 1000,
            "branch": env.lower(),
            "repoDir": "C:/PPLID/repos/PPLID_" + env,
            "repoName": f"PPLID_{env}",
        }
    return cfg


def summarize(label: str, samples: list[float], extras: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "label": label,
        "n": len(samples),
        "p50_ms": round(percentile(samples, 0.50), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "p99_ms": round(percentile(samples, 0.99), 2),
        "max_ms": round(max(samples), 2) if samples else 0,
        "avg_ms": round(statistics.mean(samples), 2) if samples else 0,
        "min_ms": round(min(samples), 2) if samples else 0,
    }
    if extras:
        row.update(extras)
    return row


def run_scenario(
    *,
    clients: int,
    seconds: float,
    delay: float,
    interval_sec: float,
) -> dict[str, Any]:
    ports = {"MAIN": 18000, "DEV": 18001, "HOM": 18002}
    servers = start_fake_backends(list(ports.values()), delay=delay)
    config = build_config(ports)
    coord = hp.get_coordinator()
    coord.reset_for_tests()
    coord.configure(
        cache_ttl_sec=5.0,
        probe_timeout_sec=1.0,
        backoff_initial_sec=2.0,
        backoff_max_sec=10.0,
        stale_window_sec=120.0,
        global_concurrency=3,
    )
    # Use real HTTP against fake backends.
    coord._http_probe_fn = None

    stop_at = time.time() + seconds
    samples: list[float] = []
    samples_lock = threading.Lock()
    client_overlap = {"count": 0}
    threads_before = threading.active_count()

    def client_loop(_cid: int) -> None:
        in_flight = False
        while time.time() < stop_at:
            if in_flight:
                client_overlap["count"] += 1
                time.sleep(0.05)
                continue
            in_flight = True
            t0 = time.perf_counter()
            try:
                with patch_lite_io():
                    server.build_overview(config, lite=True)
            finally:
                elapsed = (time.perf_counter() - t0) * 1000
                with samples_lock:
                    samples.append(elapsed)
                in_flight = False
            time.sleep(interval_sec)

    # Warm coordinator once so lite path is snapshot-based.
    for env in ports:
        coord.get_snapshot(config, env, wait=True, force=True)
    hits_after_warm = SlowHealthHandler.hits

    with ThreadPoolExecutor(max_workers=clients) as pool:
        futs = [pool.submit(client_loop, i) for i in range(clients)]
        for f in as_completed(futs):
            f.result()

    threads_after = threading.active_count()
    metrics = coord.metrics()
    probe_hits = SlowHealthHandler.hits - hits_after_warm
    for s in servers:
        s.shutdown()

    # Theoretical unbounded: clients * (seconds/interval) * 3 envs
    theoretical_unbounded = clients * max(1, int(seconds / interval_sec)) * 3

    return {
        "scenario": "slow" if delay > 0 else "normal",
        "clients": clients,
        "seconds": seconds,
        "backend_delay_sec": delay,
        "interval_sec": interval_sec,
        "overview_lite": summarize("overview-lite", samples),
        "backend_health_hits_during_load": probe_hits,
        "backend_max_inflight": SlowHealthHandler.max_inflight,
        "theoretical_unbounded_probes": theoretical_unbounded,
        "amplification_ratio": round(probe_hits / max(1, theoretical_unbounded), 4),
        "client_overlap_attempts": client_overlap["count"],
        "threads_before": threads_before,
        "threads_after": threads_after,
        "coordinator_metrics": metrics,
    }


class patch_lite_io:
    """Avoid filesystem/git noise in load loop."""

    def __enter__(self):
        self._p1 = __import__("unittest.mock").mock.patch.object(
            server_ops, "load_deploy_state", return_value={}
        )
        self._p2 = __import__("unittest.mock").mock.patch.object(
            server_ops, "build_deploy_summary", return_value={}
        )
        self._p1.start()
        self._p2.start()
        return self

    def __exit__(self, *args):
        self._p1.stop()
        self._p2.stop()
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--slow", action="store_true", help="10s backend delay")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    delay = 10.0 if args.slow else args.delay

    # Baseline "before" estimate (no coordinator): clients * ticks * 6 (3 parallel + 3 serial)
    before_estimate = {
        "note": "Pre-fix estimate: overview did 3 parallel + 3 serial probes per refresh",
        "formula": "clients * ticks * 6",
        "example_20s_10clients_0.5s": int(10 * (20 / 0.5) * 6),
    }

    result = run_scenario(
        clients=args.clients,
        seconds=args.seconds,
        delay=delay,
        interval_sec=args.interval,
    )
    result["before_estimate"] = before_estimate
    print(json.dumps(result, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
