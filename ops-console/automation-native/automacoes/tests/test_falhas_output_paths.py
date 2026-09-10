# -*- coding: utf-8 -*-
import json

from app.services.robot_manager import RobotProcessManager, FALHAS_OUTPUT_PREFIX


def test_append_log_parses_falhas_output():
    mgr = RobotProcessManager()
    payload = {
        "output_dir": r"C:\bots\report-falhas-criticas",
        "output_dir_display": "Planejamento - IDF - Bots\\report-falhas-criticas",
        "scope_dirs": [{"scope": "Brasília", "path": r"C:\bots\report-falhas-criticas\brasilia\2026-07-03", "display": "report-falhas-criticas\\brasilia\\2026-07-03"}],
        "generated_at": "2026-07-03T10:00:00",
    }
    line = f"{FALHAS_OUTPUT_PREFIX}{json.dumps(payload, ensure_ascii=False)}"
    mgr._append_log("falhas_criticas", line)
    stored = mgr.status()["falhas_criticas"]["execution"]["output_paths"]
    assert stored["output_dir_display"].startswith("Planejamento")
