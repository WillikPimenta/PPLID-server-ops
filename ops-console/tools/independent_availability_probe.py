"""Independent HTTP observer. Run separately from the Ops Console process.

Uses only the Python standard library. Results are independent evidence and
must not be merged with the console's local health-check denominator.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def probe(url: str, timeout: float) -> dict:
    started = time.monotonic()
    checked_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    status, healthy, failure = None, False, None
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "PPLID-External-Probe/1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read(65537)
            if len(body) > 65536:
                failure = "response_too_large"
            elif not 200 <= status < 300:
                failure = "http_error"
            else:
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeError):
                    data = None
                # Require a readiness response, so redirects to login pages and
                # proxy HTML cannot be mistaken for application availability.
                healthy = isinstance(data, dict) and data.get("status") in ("ok", "healthy", "degraded")
                if not healthy:
                    failure = "invalid_or_unhealthy_readiness"
    except urllib.error.HTTPError as exc:
        status, failure = exc.code, "http_error"
        exc.close()
    except (OSError, urllib.error.URLError, ValueError):
        # Do not log the URL, response body or exception text: these may contain
        # credentials, internal hostnames or proxy authentication details.
        failure = "connection_or_timeout"
    return {"source": "independent", "checkedAt": checked_at, "httpStatus": status,
            "successful": healthy, "durationMs": round((time.monotonic() - started) * 1000, 1),
            "failure": failure}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Public readiness URL, without credentials or query parameters")
    parser.add_argument("--name", default="MAIN")
    parser.add_argument("--output-dir", type=Path, required=True, help="Daily JSONL output directory")
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    url = urllib.parse.urlsplit(args.url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error("Use an HTTP(S) URL without credentials, query or fragment")
    if not 15 <= args.interval <= 3600 or not 0 < args.timeout < args.interval:
        parser.error("Interval must be 15..3600 seconds and timeout must be positive and less than interval")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic()
    try:
        while True:
            result = {**probe(args.url, args.timeout), "environment": args.name,
                      "intervalSec": args.interval, "observer": "independent-http-v1"}
            destination = args.output_dir / f"availability-{result['checkedAt'][:10]}.jsonl"
            with destination.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                return 0 if result["successful"] else 1
            deadline += args.interval
            now = time.monotonic()
            if deadline < now:
                deadline += (int((now - deadline) // args.interval) + 1) * args.interval
            time.sleep(max(0, deadline - now))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
