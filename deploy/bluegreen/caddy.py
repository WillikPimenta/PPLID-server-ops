"""Fixed Caddy listeners and pools. Normal slot switches never reload Caddy."""
from __future__ import annotations

import hashlib
import json
import urllib.request

from .config import Config
from .store import digest

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(url: str, *, value=None, timeout=5, origin=None):
    data = None if value is None else json.dumps(value).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    if origin:
        req.add_header("Origin", origin)
    with OPENER.open(req, timeout=timeout) as response:
        body = response.read()
        return json.loads(body) if body else None


def route(handlers: list, match=None) -> dict:
    result = {"handle": handlers, "terminal": True}
    if match:
        result["match"] = [match]
    return result


def build_config(config: Config, slots: dict, active: str | None) -> dict:
    servers = {}
    if active:
        for service, public in (("backend", config.backend_port), ("frontend", config.frontend_port)):
            handler = pool(config, service)
            routes = [route([handler])]
            if service == "frontend":
                routes.insert(0, route([pool(config, "backend")], {"path": [
                    "/api", "/api/*", "/media/*", "/static/*", "/falhas", "/falhas/*", "/admin", "/admin/*"]}))
            servers[f"public_{service}"] = {
                "listen": [f"{config.bind}:{public}"], "automatic_https": {"disable": True},
                "routes": routes}
    # Empty trusted proxy list: Caddy derives forwarding headers from the peer,
    # rather than trusting X-Forwarded-* supplied by a browser.
    return {"admin": {"listen": f"127.0.0.1:{config.admin_port}", "enforce_origin": True,
                      "origins": [f"127.0.0.1:{config.admin_port}"], "config": {"persist": False}},
            "apps": {"http": {"servers": servers}}}


def pool(config: Config, service: str):
    # Fixed upstreams: ordinary deployment NEVER reloads HTTP listeners. A
    # lightweight web-only traffic endpoint gates participation. Warm both
    # destinations before withdrawing the old one to avoid an empty pool.
    return {"handler": "reverse_proxy",
            "upstreams": [{"dial": f"127.0.0.1:{config.slots[name][service + 'Port']}"} for name in ("A", "B")],
            "load_balancing": {"selection_policy": {"policy": "first"}},
            "health_checks": {"active": {"uri": "/__pplid/traffic", "interval": "250ms", "timeout": "1s"}},
            "stream_close_delay": f"{config.timeouts['drainSeconds']:g}s", "flush_interval": -1,
            "transport": {"protocol": "http", "dial_timeout": "3s"}}


def verify_binary(config: Config):
    if hashlib.sha256(config.caddy.read_bytes()).hexdigest() != config.caddy_hash:
        raise ValueError("Caddy executable hash differs from configured pin")


class Caddy:
    def __init__(self, config: Config):
        self.config = config
        self.url = f"http://127.0.0.1:{config.admin_port}"

    def get(self):
        return request(self.url + "/config/", origin=self.url)

    def load(self, value):
        # The caller writes an intent journal BEFORE invoking this method.
        import time
        import urllib.error
        error = None
        try:
            request(self.url + "/load", value=value, timeout=15, origin=self.url)
        except urllib.error.HTTPError:
            raise  # An explicit rejection is not an ambiguous acknowledgement.
        except OSError as failure:
            error = failure
        # On first activation Caddy can close the old administration connection
        # after accepting /load. Reconcile by GET; never repeat the mutation.
        deadline = time.monotonic() + 3
        while True:
            try:
                if digest(self.get()) == digest(value):
                    return digest(value)
            except OSError as failure:
                error = failure
            if time.monotonic() >= deadline:
                raise RuntimeError("Caddy configuration could not be verified after reload") from error
            time.sleep(.1)
