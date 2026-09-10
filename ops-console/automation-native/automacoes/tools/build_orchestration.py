"""Build orchestration.py from _orch_body.txt."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
orch_path = ROOT / "app" / "bots" / "rotina" / "_orch_body.txt"
out_path = ROOT / "app" / "bots" / "rotina" / "orchestration.py"

orch = orch_path.read_text(encoding="utf-8")
orch = orch.replace("_exec_summary", "exec_summary").replace("_TAREFAS_DEMANDAS", "TAREFAS_DEMANDAS")

marker_start = "            # ROTINAS (BRBR-4467 1, 2, 3 dias)"
marker_end = "            tarefas_executadas += 1"
start = orch.index(marker_start)
end = orch.index(marker_end)
new_middle = """            result = execute_task(drv, tarefa_id, settings)
            if result.should_break:
                break
            if not result.ok:
                continue

"""
orch = orch[:start] + new_middle + orch[end:]

header = '''"""Orquestração: start, stop, ciclo principal."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

from selenium.common.exceptions import TimeoutException, WebDriverException

from app.config import *
from app.core.common import MetricsContext, safe_close_driver
from app.infrastructure.selenium_helpers import create_driver, take_error_screenshot
from app.infrastructure.teams_notifier import notify
from app.bots.rotina.constants import (
    DOWNLOADS_TEMP_ROTINA,
    HORA_EXECUCAO_ROTINA,
    MINUTO_EXECUCAO_ROTINA,
    TAREFAS_DEMANDAS,
    TASK_DEFAULT_ALL,
    TASK_DEPENDENCIES,
)
from app.bots.rotina.io import _get_tz_br, _limpar_pasta, _resolver_datas_execucao
from app.bots.rotina.notifications import (
    _gerar_resumo_notificacao_erro,
    _gerar_resumo_notificacao_sucesso,
    _teams_notificacao_habilitada,
)
from app.bots.rotina.selenium_brflow import _fazer_login, _navegar_para_brflow
from app.bots.rotina.state import (
    _inicializar_status_tarefas,
    _registrar_erro,
    _registrar_status_tarefa,
    _reset_exec_summary,
    _reset_progress_state,
    _set_progress,
    _set_status,
    _timing_end_step,
    _timing_start_step,
    exec_summary,
    parar_event,
)
from app.bots.rotina.tasks import execute_task

log = logging.getLogger("robots.bot_rotina")
'''

out_path.write_text(header + "\n\n" + orch + "\n", encoding="utf-8")
print(f"Wrote {out_path} ({len(out_path.read_text(encoding='utf-8').splitlines())} lines)")
