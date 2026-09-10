# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from apps.cyber_psa.services.export import build_governance_markdown
from apps.cyber_psa.services.registry import findings_path, load_findings, load_registry
from apps.psa.services.documents import resolve_repo_doc
from apps.psa.services.registry import load_last_health_report, load_registry as load_portal_registry


def build_export_package(*, username: str) -> tuple[bytes, str]:
    registry = load_registry()
    findings = load_findings()
    portal = load_portal_registry()
    ops_health = load_last_health_report()
    now = datetime.now(timezone.utc)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        md = build_governance_markdown(username=username)
        zf.writestr("00-resumo-governanca.md", md)

        glossary_path = Path(__file__).resolve().parent.parent / "glossary.json"
        if glossary_path.is_file():
            zf.writestr("03-glossario-termos.json", glossary_path.read_text(encoding="utf-8"))

        manifest = {
            "generated_at": now.isoformat(),
            "generated_by": username,
            "env_profile": getattr(settings, "PPLID_ENV_PROFILE", "local"),
            "debug": bool(getattr(settings, "DEBUG", False)),
            "portal_modules": {
                "live": sum(1 for m in (portal.get("modules") or []) if m.get("status") == "live"),
                "placeholder": sum(1 for m in (portal.get("modules") or []) if m.get("status") == "placeholder"),
            },
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

        if findings:
            zf.writestr("01-cyber-findings.json", json.dumps(findings, indent=2, ensure_ascii=False))
        elif findings_path().is_file():
            zf.writestr("01-cyber-findings.json", findings_path().read_text(encoding="utf-8"))

        if ops_health:
            zf.writestr("02-portal-ops-health.json", json.dumps(ops_health, indent=2, ensure_ascii=False))

        for idx, doc in enumerate(registry.get("documents") or [], start=3):
            rel = doc.get("path", "")
            if not rel:
                continue
            try:
                file_path = resolve_repo_doc(rel)
            except (FileNotFoundError, ValueError):
                continue
            prefix = f"{idx:02d}-docs/"
            zf.write(file_path, arcname=f"{prefix}{file_path.name}")

    filename = f"psa-cyber-pplid-{now.strftime('%Y%m%d-%H%M')}.zip"
    return buffer.getvalue(), filename
