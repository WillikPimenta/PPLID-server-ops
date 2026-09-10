"""Short-lived credential validation helper; secrets arrive only via environment."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    args = parser.parse_args()
    bundle = Path(args.bundle).resolve()
    sys.path.insert(0, str(bundle / "automacoes"))
    os.chdir(bundle / "automacoes")
    from app.services.robot_manager import robot_manager

    matricula = os.environ.pop("OPS_BOT_USER", "")
    senha = os.environ.pop("OPS_BOT_PASSWORD", "")
    headless_env = (os.environ.get("OKTA_VALIDATE_HEADLESS") or "1").strip().lower()
    headless = headless_env not in {"0", "false", "no", "n", "off"}
    ok, message, _status = robot_manager.validate_okta_credentials(
        matricula, senha, timeout=180, headless=headless, session_id="ops-console"
    )
    print(json.dumps({"ok": bool(ok), "message": str(message)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
