"""Independent, read-only availability probe. No retries hide failed samples."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import statistics
import threading
import time
from pathlib import Path
import urllib.parse
import urllib.request

from .store import utc


def run(urls: list[str], output: Path, duration: float, frequency: float):
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + duration
    mutex = threading.Lock()
    results = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with output.open("w", encoding="utf-8") as stream:
        def sample(url):
            while time.monotonic() < deadline:
                # perf_counter uses QPC on Windows; monotonic on Python 3.12
                # can have ~15 ms resolution and report spurious zero latency.
                started = time.perf_counter()
                item = {"at": utc(), "url": url, "ok": False}
                try:
                    with opener.open(url, timeout=3) as response:
                        body = response.read()
                        item.update(status=response.status, ok=response.status == 200,
                                    sha=response.headers.get("X-Pplid-Release"))
                        if response.headers.get("Content-Type", "").startswith("application/json"):
                            payload = json.loads(body)
                            item["sha"] = payload.get("sha") or payload.get("version")
                except Exception as error:
                    item["error"] = str(error)
                item["latencyMs"] = round((time.perf_counter() - started) * 1000, 3)
                with mutex:
                    results.append(item)
                    stream.write(json.dumps(item) + "\n")
                    stream.flush()
                time.sleep(max(0, 1 / frequency - (time.perf_counter() - started)))
        with ThreadPoolExecutor(max_workers=len(urls)) as executor:
            list(executor.map(sample, urls))
    summary = {}
    for url in urls:
        samples = [item for item in results if item["url"] == url]
        latencies = sorted(item["latencyMs"] for item in samples)
        summary[url] = {"samples": len(samples), "errors": sum(not item["ok"] for item in samples),
                        "p50Ms": statistics.median(latencies),
                        "p95Ms": latencies[min(len(latencies) - 1, int(len(latencies) * .95))],
                        "maxMs": max(latencies)}
    output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--hz", type=float, default=5)
    args = parser.parse_args()
    if not 0 < args.hz <= 100 or not 0 < args.seconds <= 86400:
        parser.error("Use 0<hz<=100 and 0<seconds<=86400")
    for url in args.urls:
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            parser.error("HTTP(S) URLs only")
    print(json.dumps(run(args.urls, args.output, args.seconds, args.hz), indent=2))
