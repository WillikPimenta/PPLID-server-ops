"""Web-only Waitress worker, launched with the release's Python interpreter.

No migrations, event provisioning, robots or schedules are started here.
Control/readiness files are unique per instance and reside outside the release.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import threading
import time


def write_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def load_environment(backend: Path, runtime: Path, *, web_only=True, worker_control=None, worker_owner=None,
                     supervisor_control=None, environment=None):
    # django-environ is an existing application dependency. Its parser handles
    # quoted values consistently with the application itself.
    import environ
    source = backend / ".env"
    # The app's bootstrap_environ can reload shared env with overwrite=True.
    # Point it to an instance snapshot containing mandatory web-only overrides.
    snapshot = runtime / "backend.env"
    overrides = "\nPPLID_WEB_ONLY=1\nBI_SYNC_SCHEDULER_AUTOSTART=False\n" if web_only else "\nPPLID_WEB_ONLY=0\n"
    overrides += "PPLID_SUPERVISOR_PID=" + ("0" if web_only else str(os.getpid())) + "\n"
    if worker_control:
        overrides += "PPLID_WORKER_CONTROL_DIR=" + str(worker_control) + "\n"
    if worker_owner:
        overrides += "PPLID_WORKER_OWNER=" + str(worker_owner) + "\n"
    # Keep these in the snapshot too: application bootstrap rereads it.
    if supervisor_control:
        overrides += "PPLID_SUPERVISOR_CONTROL_FILE=" + str(supervisor_control) + "\n"
    if environment:
        overrides += "PPLID_ENVIRONMENT=" + str(environment) + "\n"
    snapshot.write_text(source.read_text(encoding="utf-8-sig") + overrides, encoding="utf-8")
    environ.Env.read_env(snapshot, overwrite=True)
    os.environ["PPLID_BACKEND_ENV_FILE"] = str(snapshot)
    os.environ["PPLID_WEB_ONLY"] = "1" if web_only else "0"
    if web_only:
        os.environ["BI_SYNC_SCHEDULER_AUTOSTART"] = "False"


class WebApplication:
    def __init__(self, app, spec, ready, draining, check):
        self.app, self.spec, self.ready, self.draining, self.check = app, spec, ready, draining, check
        self.active = 0
        self.lock = threading.Lock()

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") == "/__pplid/traffic":
            try:
                allowed = json.loads(Path(self.spec["trafficFile"]).read_text(encoding="utf-8"))["allowed"]
                healthy = self.spec["instanceId"] in allowed and self.ready.is_set() and not self.draining.is_set()
            except (OSError, ValueError, KeyError):
                healthy = False
            start_response("200 OK" if healthy else "503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"ready" if healthy else b"standby"]
        if environ.get("PATH_INFO") == "/__pplid/ready":
            healthy = self.ready.is_set() and not self.draining.is_set()
            try:
                if healthy:
                    self.check()
            except Exception:
                healthy = False
            body = json.dumps({"ready": healthy, "sha": self.spec["sha"], "slot": self.spec["slot"],
                               "instanceId": self.spec["instanceId"]}).encode()
            start_response("200 OK" if healthy else "503 Service Unavailable",
                           [("Content-Type", "application/json"), ("Cache-Control", "no-store")])
            return [body]
        # Existing requests finish; requests arriving after retirement do not
        # accidentally start new work on a slot no longer receiving traffic.
        with self.lock:
            if self.draining.is_set():
                start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
                return [b"Instance draining"]
            self.active += 1
        iterable = None
        try:
            iterable = self.app(environ, start_response)
            yield_from = iterable
        except BaseException:
            with self.lock:
                self.active -= 1
            raise

        def response():
            try:
                yield from yield_from
            finally:
                try:
                    if hasattr(iterable, "close"):
                        iterable.close()
                finally:
                    with self.lock:
                        self.active -= 1
        return response()


def initialize(spec: dict, *, prepare_static=False):
    backend = Path(spec["release"]) / "backend"
    sys.path.insert(0, str(Path(spec['release']) / 'automacoes'))
    sys.path.insert(0, str(backend))
    os.chdir(backend)
    load_environment(backend, Path(spec["stopFile"]).parent, worker_control=spec.get("workerControlDir"),
                     worker_owner=spec.get("instanceId"), supervisor_control=spec.get("supervisorControlFile"),
                     environment=spec.get("environment"))
    os.environ["PPLID_ENVIRONMENT"] = spec["environment"]
    os.environ["PPLID_DEPLOYED_SHA_FILE"] = spec["shaFile"]
    # Never inherit a global deployment marker for candidate identity.
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django
    django.setup()
    from django.db import connection, close_old_connections
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise RuntimeError("Pending migrations: online promotion refused; apply reviewed compatible migrations separately")
    if prepare_static:
        from django.conf import settings
        if getattr(settings, "STATIC_ROOT", None):
            from django.core.management import call_command
            call_command("collectstatic", interactive=False, verbosity=0)
    def check():
        close_old_connections()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        close_old_connections()
    check()
    application = importlib.import_module("config.wsgi").application
    return application, check


def frontend_application(spec):
    from whitenoise import WhiteNoise
    dist = Path(spec["release"]) / "frontend/dist"
    index = (dist / "index.html").read_bytes()
    def fallback(environ, start_response):
        path = environ.get("PATH_INFO", "/")
        if path.startswith("/assets/") or "." in path.rsplit("/", 1)[-1]:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not found"]
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-cache"),
                                   ("X-Pplid-Release", spec["sha"])])
        return [index]
    def headers(headers, path, url):
        headers["X-Pplid-Release"] = spec["sha"]
        headers["Cache-Control"] = "public, max-age=31536000, immutable" if url.startswith("/assets/") else "no-cache"
    static = WhiteNoise(fallback, root=str(dist), add_headers_function=headers)
    # Autorefresh only for the append-only shared asset directory: old tabs
    # can request old chunks after both web slots have moved to newer releases.
    shared = WhiteNoise(static, root=spec["assetsDirectory"], prefix="/assets/", autorefresh=True,
                        add_headers_function=headers)
    return shared


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--prepare-static", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    # This launcher is intentionally named slot_worker, not waitress: old app
    # versions also use argv heuristics to start robots. Contract v1 adds an
    # explicit web-only guard and is required by the orchestrator.
    app, check = initialize(spec, prepare_static=args.prepare_static)
    if args.check_only:
        print(json.dumps({"ready": True, "sha": spec["sha"], "webOnly": True}))
        return
    from waitress import create_server
    ready, draining = threading.Event(), threading.Event()
    wrapped = WebApplication(app, spec, ready, draining, check)
    server = create_server(wrapped, host="127.0.0.1", port=spec["backendPort"],
                           threads=spec.get("threads", 16), channel_timeout=600, map={})
    frontend = WebApplication(frontend_application(spec), spec, ready, draining, check)
    frontend_server = create_server(frontend, host="127.0.0.1", port=spec["frontendPort"],
                                    threads=4, channel_timeout=600, map={})
    threading.Thread(target=frontend_server.run, daemon=True).start()
    ready.set()
    def retire():
        while not Path(spec["stopFile"]).exists():
            time.sleep(.1)
        draining.set()
        control = None
        if spec.get('workerControlDir') and (Path(spec['workerControlDir']) / 'state.json').exists():
            # Close only this web instance's admissions. Other slots and the
            # independent supervisor continue accepting their own work.
            from worker_protocol import Controller
            control = Controller(spec['workerControlDir'], spec['instanceId'])
        retired = False
        while True:
            with wrapped.lock:
                active = wrapped.active + frontend.active
            try:
                if control and not retired:
                    control.retire(spec['instanceId'])
                    retired = True
                own_units = [unit for unit in control.status()['units'] if unit['group'] == spec['instanceId']] if control else []
            except Exception:
                # An unreadable lease is not proof of completion.
                time.sleep(.2)
                continue
            if not active and not own_units:
                # Graceful process exit after application iterables are closed.
                server.close()
                frontend_server.close()
                os._exit(0)
            time.sleep(.1)
    threading.Thread(target=retire, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
