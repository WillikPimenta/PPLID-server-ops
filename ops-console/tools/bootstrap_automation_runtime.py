"""Bootstrap native automation runtime (.venv + deps) for ops-console."""
from __future__ import annotations

import json
import sys
from pathlib import Path

OPS_CONSOLE = Path(__file__).resolve().parent.parent
if str(OPS_CONSOLE) not in sys.path:
    sys.path.insert(0, str(OPS_CONSOLE))

import server  # noqa: E402
import server_automations as sa  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg != "--force"]
    config_path = Path(args[0] if args else server.DEFAULT_CONFIG)
    config = server.load_config(config_path)
    result = sa.ensure_automation_runtime(config, force=force)
    print(json.dumps(result, ensure_ascii=False))
    if result.get("ready"):
        print("Runtime de automações pronto.", flush=True)
        return 0
    print(f"Runtime indisponível: {result.get('reason')}", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
