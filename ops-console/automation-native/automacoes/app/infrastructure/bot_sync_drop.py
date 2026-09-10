"""Drop files for bot→Django sync when stdout pipe breaks (Windows Errno 22).

The production bot prints ``PRODUCTION_DETALHADO_SAVED|path`` for the Django
hook. If that ``print`` fails (broken pipe / OneDrive), the portal never
enqueues produtividade sync — Confer/Case keep working via other channels.

Fallback: write a JSON drop under ``%APPDATA%/PLAN_IDF_SERASA_BOTS/sync_drop/``.
``RobotProcessManager.status()`` / stream loop drains and notifies callbacks.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from app.config.paths import PLAN_IDF_SERASA_BOTS

log = logging.getLogger(__name__)

DOMAIN_PRODUTIVIDADE = "produtividade"
DOMAIN_MONITOR_EVENTOS = "monitor_eventos"
DOMAIN_REINSPECAO_GED = "reinspecao_ged"

SYNC_DROP_DIR = PLAN_IDF_SERASA_BOTS / "sync_drop"


def sync_drop_dir() -> Path:
    path = Path(os.getenv("BOT_SYNC_DROP_DIR", str(SYNC_DROP_DIR)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_sync_drop(
    domain: str,
    source_path: str | Path,
    *,
    extra: dict | None = None,
) -> Path | None:
    """Persist a pending sync marker. Returns path or None on failure."""
    try:
        dest_dir = sync_drop_dir()
        payload = {
            "domain": str(domain).strip(),
            "source_path": str(Path(source_path).resolve()),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        if extra:
            payload["extra"] = extra
        name = f"{payload['domain']}_{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
        path = dest_dir / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return path
    except OSError as exc:
        log.warning("Falha ao gravar sync_drop domain=%s path=%s: %s", domain, source_path, exc)
        return None


def list_sync_drops() -> list[Path]:
    try:
        dest_dir = sync_drop_dir()
        return sorted(dest_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return []


def drain_sync_drops(
    handlers: dict[str, Callable[[str], None]],
    *,
    limit: int = 50,
) -> int:
    """Process pending drop files. ``handlers`` maps domain → callback(path).

    Returns number of successfully handled drops.
    """
    handled = 0
    for path in list_sync_drops()[: max(1, int(limit))]:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("sync_drop ilegível %s: %s — removendo", path.name, exc)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue

        domain = str(data.get("domain") or "").strip()
        source = str(data.get("source_path") or "").strip()
        cb = handlers.get(domain)
        if not domain or not source or cb is None:
            log.warning(
                "sync_drop sem handler domain=%s file=%s - mantendo para retry",
                domain,
                path.name,
            )
            continue

        try:
            cb(source)
            handled += 1
        except Exception as exc:
            log.exception("sync_drop callback falhou domain=%s: %s", domain, exc)
            # keep file for retry
            continue

        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("sync_drop: não removeu %s: %s", path.name, exc)

    return handled


def append_marker_to_robot_log(mode: str, line: str) -> bool:
    """Append a marker line to AppData robot log (survives broken stdout pipe)."""
    try:
        logs_dir = Path(os.getenv("ROBOT_LOGS_DIR", str(PLAN_IDF_SERASA_BOTS / "logs")))
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_path = logs_dir / f"{mode}.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with file_path.open("a", encoding="utf-8") as handler:
            handler.write(f"[{ts}] {line}\n")
        return True
    except OSError as exc:
        log.warning("Falha ao append marker no log %s: %s", mode, exc)
        return False
