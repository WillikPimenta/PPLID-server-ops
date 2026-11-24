"""Run locally on the server; never depends on Portal/Postgres."""
import argparse
import getpass
import hashlib
import json
import secrets
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["enable", "disable"])
parser.add_argument("--minutes", type=int, default=30)
args = parser.parse_args()
if not 1 <= args.minutes <= 60:
    parser.error("minutes must be between 1 and 60")
data_dir = root / "data"
data_dir.mkdir(parents=True, exist_ok=True)
path = data_dir / "emergency-access.json"
if args.action == "disable":
    path.unlink(missing_ok=True)
else:
    password = secrets.token_urlsafe(32)
    record = {"id": secrets.token_hex(16), "expiresAt": time.time() + args.minutes * 60,
              "passwordHash": hashlib.sha256(password.encode()).hexdigest()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)
    print("Usuario: recovery")
    print("Senha temporaria:", password)
    print("Validade (minutos):", args.minutes)
with (data_dir / "emergency-access-audit.log").open("a", encoding="utf-8") as audit:
    audit.write(json.dumps({"at": time.time(), "operator": getpass.getuser(), "action": args.action}) + "\n")
