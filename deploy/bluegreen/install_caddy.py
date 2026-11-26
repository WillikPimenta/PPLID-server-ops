"""Download a pinned official Caddy archive, verify its checksum, and extract it.

This provisions a user-owned directory only; it installs no Windows service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile
from io import BytesIO

VERSION = "2.11.9"
BASE = f"https://github.com/caddyserver/caddy/releases/download/v{VERSION}"


def install(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    archive_name = f"caddy_{VERSION}_windows_amd64.zip"
    with urllib.request.urlopen(f"{BASE}/caddy_{VERSION}_checksums.txt", timeout=60) as response:
        checksums = response.read().decode()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1].lstrip("*") == archive_name)
    with urllib.request.urlopen(f"{BASE}/{archive_name}", timeout=120) as response:
        data = response.read()
    # Official release checksums currently use SHA-512. Do not assume SHA-256.
    if len(expected) != 128:
        raise ValueError("Unexpected official checksum format (expected SHA-512)")
    actual = hashlib.sha512(data).hexdigest()
    if actual != expected:
        raise ValueError("Caddy archive checksum mismatch")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        executable = archive.read("caddy.exe")
    target = destination / "caddy.exe"
    if target.exists() and target.read_bytes() != executable:
        raise ValueError("Refusing to overwrite a different Caddy executable")
    target.write_bytes(executable)
    receipt = {"version": VERSION, "url": f"{BASE}/{archive_name}", "archiveSha512": actual,
               "executableSha256": hashlib.sha256(executable).hexdigest()}
    (destination / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    print(json.dumps(install(parser.parse_args().destination), indent=2))
