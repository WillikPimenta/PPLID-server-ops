"""Explicit independent supervisor. Never launched by a web deployment."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time

from slot_worker import load_environment, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--pplid-supervised", action="store_true", required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    backend = Path(spec["release"]) / "backend"
    sys.path.insert(0, str(backend))
    sys.path.insert(0, str(Path(spec["release"]) / "automacoes"))
    os.chdir(backend)
    load_environment(backend, args.spec.parent, web_only=False, worker_control=spec["workerControlDir"],
                     worker_owner=spec['instanceId'], supervisor_control=spec.get('supervisorControlFile'),
                     environment=spec['environment'])
    os.environ["PPLID_ENVIRONMENT"] = spec["environment"]
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    # Existing apps use this heuristic. In this dedicated process it is
    # intentional: web slots always use PPLID_WEB_ONLY=1 instead.
    sys.argv = ["waitress-pplid-background-supervisor"]
    from deploy_lock import background_lock
    with background_lock(spec["environment"]):
        from app.core import worker_drain as drain
        import psutil
        control = drain.Controller(spec["workerControlDir"], spec['instanceId'])
        control.open(spec["instanceId"])
        startup_error = None
        bridge = None
        try:
            import django
            django.setup()
            from django.core.management import call_command
            from django.db import close_old_connections
            from supervisor_control import start_executor_control
            bridge = start_executor_control(spec['supervisorControlFile'], spec['environment'],
                                            spec['instanceId'], control.requested)
            write_json(Path(spec["readyFile"]), {"ready": True, "sha": spec["sha"],
                       "instanceId": spec["instanceId"], "pid": os.getpid()})
            while not Path(spec["stopFile"]).exists() and not control.requested():
                try:
                    with drain.cycle('event-scheduler') as admitted:
                        if not admitted:
                            break
                        close_old_connections()
                        call_command("run_event_scheduler", once=True)
                except Exception:
                    logging.exception("Background event scheduler tick failed")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and not Path(spec["stopFile"]).exists() and not control.requested():
                    time.sleep(.1)
        except Exception as error:
            startup_error = type(error).__name__
            logging.exception('Background supervisor failed; closing admission and draining')
        finally:
            control.request(spec["instanceId"])
            if bridge:
                bridge.close()
        # All scheduler loops stop admitting cycles. Existing units and their
        # process leases stay alive until work and cleanup finish naturally.
        while True:
            try:
                status = control.status()
                children = psutil.Process().children(recursive=True)
                write_json(Path(spec["drainStatusFile"]), {
                    'phase': 'draining', 'instanceId': spec['instanceId'],
                    'units': status['units'], 'remainingChildPids': [p.pid for p in children],
                    'startupError': startup_error})
                if not status['units'] and not children:
                    control.confirm_stopped(spec['instanceId'])
                    write_json(Path(spec['stoppedFile']), {'drained': True, 'sha': spec['sha'],
                               'instanceId': spec['instanceId'], 'pid': os.getpid(), 'startupError': startup_error})
                    return 1 if startup_error else 0
            except Exception:
                logging.exception('Drain is unconfirmed; supervisor and records preserved')
            time.sleep(.2)


if __name__ == "__main__":
    sys.exit(main())
