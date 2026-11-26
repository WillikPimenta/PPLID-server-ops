from __future__ import annotations

import ipaddress
from pathlib import Path
import re

from .store import read_json

ENVIRONMENTS = ("MAIN", "DEV", "HOM")


def strategy(root: dict, environment: str) -> str:
    value = root.get(environment, {}).get("deployStrategy", "legacy")
    if value not in ("legacy", "blueGreen"):
        raise ValueError(f"Unknown deploy strategy: {value}")
    return value


def port(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
        raise ValueError(f"Expected an unprivileged integer port, got {value!r}")
    return value


class Config:
    def __init__(self, base: Path, root: dict, environment: str):
        if environment not in ENVIRONMENTS:
            raise ValueError("Invalid environment")
        self.base = base.resolve()
        self.environment = environment
        self.env = root[environment]
        if self.env.get("enabled", True) is not True:
            raise ValueError("Environment disabled")
        if strategy(root, environment) != "blueGreen":
            raise ValueError("blueGreen is not enabled")
        self.bg = self.env["blueGreen"]
        self.root = self.base / "deploy" / environment
        self.directory = self.root / "bluegreen"
        self.state_path = self.directory / "state.json"
        self.assets = self.directory / "assets"
        self.backend_port = port(self.env["backendPort"])
        self.frontend_port = port(self.env["frontendPort"])
        self.admin_port = port(self.bg["adminPort"])
        self.bind = root.get("bindHost", "127.0.0.1")
        ipaddress.IPv4Address(self.bind)
        self.slots = self.bg["slots"]
        if set(self.slots) != {"A", "B"}:
            raise ValueError("Exactly slots A and B are required")
        self.timeouts = {name: float(self.bg.get(name, default)) for name, default in (
            ("startupSeconds", 90), ("observeSeconds", 30), ("drainSeconds", 120),
            ("stopSeconds", 20), ("commandSeconds", 600))}
        if any(not 0 < value <= 3600 for value in self.timeouts.values()):
            raise ValueError("Timeouts must be positive and at most 3600 seconds")
        for key in ("minimumAvailableMb", "minimumDiskMb"):
            if type(self.bg.get(key)) is not int or self.bg[key] < 128:
                raise ValueError(f"{key} must be explicitly configured (>=128)")
        self.minimum_commit_mb = self.bg.get('minimumCommitMb', self.bg['minimumAvailableMb'])
        if type(self.minimum_commit_mb) is not int or self.minimum_commit_mb < 128:
            raise ValueError('minimumCommitMb must be an integer >=128')
        self.caddy = Path(self.bg["caddyPath"]).resolve()
        self.caddy_hash = self.bg["caddySha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", self.caddy_hash):
            raise ValueError("Pin caddySha256 to the verified executable")
        self.python = str(Path(self.bg["pythonPath"]).resolve())
        # Reserve every public/internal/admin port, including disabled environments.
        used = {}
        def reserve(value, label):
            number = port(value)
            if number in used:
                raise ValueError(f"Port collision: {label} and {used[number]} ({number})")
            used[number] = label
        if root.get("opsConsolePort"):
            reserve(root["opsConsolePort"], "opsConsole")
        for name in ENVIRONMENTS:
            env = root.get(name, {})
            for field in ("backendPort", "frontendPort"):
                if field in env:
                    reserve(env[field], f"{name}.{field}")
            bg = env.get("blueGreen", {})
            if "adminPort" in bg:
                reserve(bg["adminPort"], f"{name}.admin")
            for slot, values in bg.get("slots", {}).items():
                for field in ("backendPort", "frontendPort"):
                    reserve(values[field], f"{name}.{slot}.{field}")

    def release(self, sha: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            raise ValueError("Invalid release SHA")
        parent = (self.root / "releases").resolve()
        path = parent / sha
        if path.resolve().parent != parent or path.is_symlink() or path.is_junction():
            raise ValueError("Release must be a physical child of releases")
        return path

    @classmethod
    def load(cls, machine_path: Path, environment: str):
        machine_path = machine_path.resolve()
        machine = read_json(machine_path)
        base = Path(machine["baseDir"])
        config = cls(base, read_json(Path(machine.get("envConfigPath", base / "ops/config/env.config.json"))), environment)
        config.machine_path = machine_path
        return config
