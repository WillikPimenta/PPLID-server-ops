from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy.bluegreen.config import Config
from deploy.bluegreen.manager import Manager
from deploy.bluegreen import processes
from deploy.bluegreen.store import atomic_json


def unused_ports(count):
    streams = [socket.socket() for _ in range(count)]
    try:
        for stream in streams:
            stream.bind(("127.0.0.1", 0))
        return [stream.getsockname()[1] for stream in streams]
    finally:
        for stream in streams:
            stream.close()


@pytest.fixture
def laboratory(tmp_path):
    caddy = ROOT / ".local/bluegreen-tools/caddy-2.11.9/caddy.exe"
    if not caddy.is_file():
        pytest.skip("Provision pinned Caddy with deploy/bluegreen/install_caddy.py")
    ports = unused_ports(7)
    config_root = {"bindHost": "127.0.0.1", "MAIN": {"enabled": True, "deployStrategy": "blueGreen",
        "backendPort": ports[0], "frontendPort": ports[1], "blueGreen": {
            "adminPort": ports[2], "caddyPath": str(caddy), "caddySha256": hashlib.sha256(caddy.read_bytes()).hexdigest(),
            "pythonPath": sys.executable, "minimumAvailableMb": 128, "minimumDiskMb": 128,
            "workersOwnershipConfirmed": True,
            "startupSeconds": 15, "observeSeconds": .2, "drainSeconds": .4, "stopSeconds": 5,
            "slots": {"A": {"backendPort": ports[3], "frontendPort": ports[4]},
                      "B": {"backendPort": ports[5], "frontendPort": ports[6]}}}}}
    config = Config(tmp_path, config_root, "MAIN")
    manager = Manager(config)
    yield config, manager, config_root
    # Test cleanup only terminates processes whose exact identity was saved.
    background = config.directory / 'background.json'
    if background.exists():
        record = json.loads(background.read_text(encoding='utf-8'))
        if record.get('process'):
            processes.stop(record['process'], Path(record['stopFile']), 2, force=True)
    if config.state_path.exists():
        try:
            state = manager.read()
            for slot in state["slots"].values():
                if slot.get("process"):
                    processes.stop(slot["process"], Path(slot["stopFile"]), 2, force=True)
            if state.get("proxy"):
                from deploy.bluegreen.caddy import OPENER
                import urllib.request
                try:
                    with OPENER.open(urllib.request.Request(manager.proxy.url + "/stop", data=b"", headers={"Origin": manager.proxy.url}), timeout=3):
                        pass
                except OSError:
                    pass
                processes.stop(state["proxy"], None, 2, force=True)
        except Exception:
            pass


@pytest.fixture
def make_release():
    def make(config, character, *, broken=False):
        sha = character * 40
        release = config.release(sha[:8])
        backend = release / "backend"
        backend.mkdir(parents=True)
        # Real Waitress + Django + SQLite, independent of any corporate DB.
        (backend / "config").mkdir()
        (backend / "config/__init__.py").write_text("")
        (backend / "config/settings.py").write_text(
            "SECRET_KEY='isolated-test-only'\nINSTALLED_APPS=[]\n"
            "DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}}\n"
            "DEFAULT_AUTO_FIELD='django.db.models.AutoField'\n", encoding="utf-8")
        (backend / ".env").write_text("BI_SYNC_SCHEDULER_AUTOSTART=True\n")
        wsgi = '''import json, os, time
from django.core.wsgi import get_wsgi_application
django_application = get_wsgi_application()
def application(environ, start_response):
    if environ.get('PATH_INFO') == '/slow':
        start_response('200 OK', [('Content-Type','text/plain')])
        yield b'start'
        time.sleep(1)
        yield b'finish'
        return
    body = environ['wsgi.input'].read(int(environ.get('CONTENT_LENGTH') or 0))
    result = json.dumps({'sha': RELEASE_SHA, 'method': environ['REQUEST_METHOD'],
                         'body': body.decode(), 'cookie': environ.get('HTTP_COOKIE'),
                         'forwarded': environ.get('HTTP_X_FORWARDED_FOR'),
                         'webOnly': os.environ.get('PPLID_WEB_ONLY'),
                         'scheduler': os.environ.get('BI_SYNC_SCHEDULER_AUTOSTART')}).encode()
    start_response('200 OK', [('Content-Type','application/json'), ('Set-Cookie','session=kept; HttpOnly; SameSite=Lax')])
    yield result
'''
        if broken:
            wsgi = "raise RuntimeError('injected candidate startup failure')\n"
        (backend / "config/wsgi.py").write_text("RELEASE_SHA=" + repr(sha[:8]) + "\n" + wsgi, encoding="utf-8")
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(backend / ".venv")
        (release / "frontend/dist/assets").mkdir(parents=True)
        (release / "frontend/dist/index.html").write_text(f"<!doctype html><title>{sha[:8]}</title><p>{sha[:8]}</p>")
        (release / f"frontend/dist/assets/{sha[:8]}.js").write_text(f"window.release='{sha[:8]}';")
        atomic_json(release / "meta.json", {"sha": sha[:8], "shaFull": sha, "built": True})
        atomic_json(release / "scripts/deploy/bluegreen-contract.json", {
            "schemaVersion": 1, "webOnlyFlag": "PPLID_WEB_ONLY", "backgroundWorkers": "independent"})
        return sha[:8]
    return make
