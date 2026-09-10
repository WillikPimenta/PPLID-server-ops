# -*- coding: utf-8 -*-
"""Identidade imutável dos arquivos revisados na análise de derivação."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def build_source_manifest(files: list[Path]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for path in sorted(files, key=lambda item: item.name.casefold()):
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return manifest


def fingerprint_source_manifest(manifest: list[dict[str, Any]]) -> str:
    canonical = json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
