"""Install the small web-only contract in an application checkout.

No settings, credentials, database or existing robot implementation is changed.
Exact anchors make this fail rather than guessing when source has diverged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def apply(root: Path):
    replacements = [
        ("backend/apps/common/apps.py",
         "    def ready(self):\n        from .bi_sync_scheduler import ensure_scheduler_started",
         "    def ready(self):\n        import os\n        if os.environ.get(\"PPLID_WEB_ONLY\") == \"1\":\n            return\n        from .bi_sync_scheduler import ensure_scheduler_started"),
        ("backend/apps/automacoes/apps.py", "        if serving and not reloader_parent:",
         '        if serving and not reloader_parent and os.environ.get("PPLID_WEB_ONLY") != "1":'),
    ]
    guard = '        import os\n        if os.environ.get("PPLID_WEB_ONLY") == "1":\n            return\n'
    for app, signature in (("falhas_criticas", "    def ready(self):\n"),
                           ("ferias", "    def ready(self):\n"),
                           ("suporte_operacional", "    def ready(self) -> None:\n")):
        relative = f"backend/apps/{app}/apps.py"
        if (root / relative).exists():
            replacements.append((relative, signature, signature + guard))
    if (root / "backend/apps/escala_flex/apps.py").exists():
        before = "        try:\n            from .services.status_timeout_scheduler import start_status_timeout_scheduler"
        replacements.append(("backend/apps/escala_flex/apps.py", before, guard + "\n" + before))
    if (root / "backend/apps/ops_monitoring/apps.py").exists():
        before = '        start_metrics_flush_thread()\n        thread = threading.Thread(target=_purge_loop, name="ops-metrics-purge", daemon=True)'
        replacements.append(("backend/apps/ops_monitoring/apps.py", before,
                             '        start_metrics_flush_thread()\n' + guard + before.split('\n', 1)[1]))
    legacy_anchor = '. "$DeployRoot\\lib.ps1"\n'
    legacy_guard = '''
# Public listeners belong to the proxy once blue-green is configured.
$strategyConfig = Get-DeployConfig
$runtimeState = Join-Path (Get-PplidBaseDir) "deploy\\$Environment\\bluegreen\\state.json"
if ($strategyConfig.$Environment.deployStrategy -eq 'blueGreen' -or (Test-Path -LiteralPath $runtimeState)) {
    throw 'Operacao legada bloqueada em blue-green; use ops/deploy/bluegreen.ps1 e o runbook.'
}
'''
    for name in ('start_env', 'start_backend', 'start_frontend', 'stop_env', 'deploy_env'):
        relative = f'scripts/deploy/{name}.ps1'
        if (root / relative).exists():
            replacements.append((relative, legacy_anchor, legacy_anchor + legacy_guard))
    prepared = []
    for relative, before, after in replacements:
        path = root / relative
        text = path.read_text(encoding="utf-8-sig")
        if after in text:
            continue
        if text.count(before) != 1:
            raise ValueError(f"Anchor differs in {relative}; review the application manually")
        prepared.append((path, text.replace(before, after)))
    # Validate every anchor before the first write.
    for path, text in prepared:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    contract = root / "scripts/deploy/bluegreen-contract.json"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(json.dumps({"schemaVersion": 1, "webOnlyFlag": "PPLID_WEB_ONLY",
                                    "backgroundWorkers": "independent"}, indent=2) + "\n", encoding="utf-8")
    print("Application web-only guards and contract installed; no services restarted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application", type=Path)
    apply(parser.parse_args().application.resolve())
