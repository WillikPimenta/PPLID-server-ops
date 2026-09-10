"""HTTP access log middleware — writes to PPLID_HTTP_ACCESS_LOG when set."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone


class AccessLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.log_path = os.environ.get("PPLID_HTTP_ACCESS_LOG", "").strip()

    def __call__(self, request):
        start = time.perf_counter()
        response = self.get_response(request)
        path = self.log_path
        if not path:
            return response
        duration_ms = int((time.perf_counter() - start) * 1000)
        remote = request.META.get("REMOTE_ADDR", "-")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        full_path = request.get_full_path()
        line = f"{ts} {request.method} {full_path} {response.status_code} {duration_ms}ms {remote}\n"
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass
        return response
