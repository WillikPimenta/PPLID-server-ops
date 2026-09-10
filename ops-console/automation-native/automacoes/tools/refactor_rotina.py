"""Refactor bot_rotina.py into app/bots/rotina/ package."""
from __future__ import annotations

import re
import shutil
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "bots" / "bot_rotina.py"
PKG = ROOT / "app" / "bots" / "rotina"
BACKUP = ROOT / "app" / "bots" / "bot_rotina.py.bak"

# Shared import block for domain modules
DOMAIN_IMPORTS = textwrap.dedent("""
    import csv
    import glob
    import logging
    import os
    import re
    import shutil
    import time
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    import pandas as pd
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        StaleElementReferenceException,
        TimeoutException,
        WebDriverException,
    )

    from app.config import *
    from app.core.common import ErrorRecoveryHandler
    from app.infrastructure.csv_processing import read_csv as cp_read_csv
    from app.infrastructure.file_mirror import espelhar_arquivo
    from app.infrastructure.selenium_helpers import (
        click_element,
        create_driver,
        login_okta_resiliente,
        send_keys_to_element,
        take_error_screenshot,
    )
""").strip()


def read_source() -> list[str]:
    text = SRC.read_text(encoding="utf-8")
    return text.splitlines(keepends=True)


def slice_lines(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def write(path: Path, doc: str, imports: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f'"""{doc}"""\nfrom __future__ import annotations\n\n{imports}\n\n{body.strip()}\n'
    path.write_text(content, encoding="utf-8")
    print(f"  {path.relative_to(ROOT)} ({len(content.splitlines())} lines)")


def build_constants(lines: list[str]) -> None:
    body = slice_lines(lines, 91, 227)
    imports = "from app.config import PLAN_IDF_SERASA_BOTS"
    write(PKG / "constants.py", "Constantes e identificadores de tarefas da rotina.", imports, body)


def build_state(lines: list[str]) -> None:
    body = slice_lines(lines, 229, 341) + "\n" + slice_lines(lines, 698, 710)
    imports = textwrap.dedent("""
        import logging
        import time

        from app.core.bot_runtime import BotRuntime
        from app.bots.rotina.constants import (
            TASK_TO_ROTINA_DESCRICAO,
            TAREFAS_DEMANDAS,
        )
    """).strip()
    # Fix private names to public in constants
    body = body.replace("_TAREFAS_DEMANDAS", "TAREFAS_DEMANDAS")
    write(PKG / "state.py", "Estado de execução e callbacks BotRuntime.", imports, body)


def build_notifications(lines: list[str]) -> None:
    body = slice_lines(lines, 274, 381)
    imports = textwrap.dedent("""
        import logging
        import os
        from datetime import datetime

        import pandas as pd

        from app.infrastructure.teams_adaptive_card import build_status_card, humanizar_observacao
        from app.bots.rotina.constants import (
            DEMANDAS_TABELA,
            EXPECTED_COLUMNS,
            OBS_HUMANIZADA,
            TASK_TO_ROTINA_DESCRICAO,
        )
        from app.bots.rotina.state import exec_summary
        from app.bots.rotina.csv_merge import CSVReader
    """).strip()
    body = body.replace("_exec_summary", "exec_summary")
    body = body.replace("_DEMANDAS_TABELA", "DEMANDAS_TABELA")
    body = body.replace("_OBS_HUMANIZADA", "OBS_HUMANIZADA")
    body = body.replace("_task_id_por_descricao_rotina", "task_id_por_descricao_rotina")
    # Add task_id helper and contar_linhas
    extra = slice_lines(lines, 274, 279).replace("_task_id_por_descricao_rotina", "task_id_por_descricao_rotina")
    contar = slice_lines(lines, 345, 369).replace("_exec_summary", "exec_summary")
    body = extra + "\n" + slice_lines(lines, 282, 381).replace("_exec_summary", "exec_summary").replace("_DEMANDAS_TABELA", "DEMANDAS_TABELA").replace("_OBS_HUMANIZADA", "OBS_HUMANIZADA")
    write(PKG / "notifications.py", "Notificações Teams e resumo de execução.", imports, body + "\n" + contar)


def build_csv_merge(lines: list[str]) -> None:
    body = slice_lines(lines, 388, 691)
    imports = textwrap.dedent("""
        import logging
        import os

        import pandas as pd

        from app.bots.rotina.constants import CSV_CHUNK_SIZE, CSV_ENCODINGS, CSV_SEPARATORS
    """).strip()
    write(PKG / "csv_merge.py", "Leitura CSV e merge inteligente BRFlow.", imports, body)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Source not found: {SRC}")
    lines = read_source()
    if not BACKUP.exists():
        shutil.copy2(SRC, BACKUP)
        print(f"Backup: {BACKUP}")
    PKG.mkdir(parents=True, exist_ok=True)
    print("Building rotina package modules...")
    build_constants(lines)
    build_state(lines)
    build_csv_merge(lines)
    build_notifications(lines)
    print("Phase 1 modules created. Run phase 2+ manually or extend script.")


if __name__ == "__main__":
    main()
