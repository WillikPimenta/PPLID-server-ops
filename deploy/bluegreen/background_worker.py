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
    os.chdir(backend)
    load_environment(backend, args.spec.parent, web_only=False)
    os.environ["PPLID_ENVIRONMENT"] = spec["environment"]
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    # Existing apps use this heuristic. In this dedicated process it is
    # intentional: web slots always use PPLID_WEB_ONLY=1 instead.
    sys.argv = ["waitress-pplid-background-supervisor"]
    from deploy_lock import background_lock
    with background_lock(spec["environment"]):
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import close_old_connections
        # This confirms Django's startup hooks completed, not business-level
        # health of every asynchronous job. Validate those in HOM separately.
        write_json(Path(spec["readyFile"]), {"ready": True, "sha": spec["sha"],
                   "instanceId": spec["instanceId"], "pid": os.getpid()})
        try:
            while not Path(spec["stopFile"]).exists():
                close_old_connections()
                try:
                    call_command("run_event_scheduler", once=True)
                except Exception:
                    logging.exception("Background event scheduler tick failed")
                for _ in range(300):
                    if Path(spec["stopFile"]).exists():
                        break
                    time.sleep(.1)
        finally:
            from apps.automacoes.services import get_robot_manager
            get_robot_manager().stop_all()


if __name__ == "__main__":
    main()
