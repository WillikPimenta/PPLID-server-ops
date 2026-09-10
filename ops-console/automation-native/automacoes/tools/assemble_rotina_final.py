"""Final assembly of rotina package modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "app" / "bots" / "rotina"


def rb(name: str) -> str:
    for base in (PKG, PKG / "tasks"):
        p = base / name
        if p.exists():
            return apply_renames(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(name)


def apply_renames(text: str) -> str:
    for old, new in {
        "_exec_summary": "exec_summary",
        "_timing_start": "timing_start",
        "_TAREFAS_DEMANDAS": "TAREFAS_DEMANDAS",
        "_DEMANDAS_TABELA": "DEMANDAS_TABELA",
        "_OBS_HUMANIZADA": "OBS_HUMANIZADA",
    }.items():
        text = text.replace(old, new)
    return text


def w(path: Path, header: str, *parts: str) -> None:
    body = "\n\n".join(p.strip() for p in parts if p.strip())
    path.write_text(f"{header.strip()}\n\n{body}\n", encoding="utf-8")
    print(f"{path.relative_to(ROOT)}: {len(path.read_text(encoding='utf-8').splitlines())} lines")


def main() -> None:
    w(
        PKG / "state.py",
        '''"""Estado de execução e callbacks BotRuntime."""
from __future__ import annotations

import logging
import time

from app.core.bot_runtime import BotRuntime
from app.bots.rotina.constants import TAREFAS_DEMANDAS, TASK_TO_ROTINA_DESCRICAO

log = logging.getLogger("robots.bot_rotina")

_runtime = BotRuntime()
parar_event = _runtime.parar_event
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state

timing_start = {}

exec_summary = {
    "arquivos": [],
    "total_duplicatas_merge": 0,
    "total_duplicatas_internas": 0,
    "total_linhas_finais": 0,
    "erros": [],
    "task_status": {},
}''',
        rb("_state_body.txt"),
        rb("_timing_body.txt"),
    )

    w(
        PKG / "csv_merge.py",
        '''"""Leitura CSV e merge inteligente BRFlow."""
from __future__ import annotations

import logging
import os

import pandas as pd

from app.bots.rotina.constants import CSV_CHUNK_SIZE, CSV_ENCODINGS, CSV_SEPARATORS

log = logging.getLogger("robots.bot_rotina")''',
        rb("csv_merge_body.txt"),
    )

    w(
        PKG / "notifications.py",
        '''"""Notificações Teams e resumo de execução."""
from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd

from app.infrastructure.teams_adaptive_card import build_status_card, humanizar_observacao
from app.bots.rotina.constants import DEMANDAS_TABELA, EXPECTED_COLUMNS, OBS_HUMANIZADA, TASK_TO_ROTINA_DESCRICAO
from app.bots.rotina.state import exec_summary
from app.bots.rotina.csv_merge import CSVReader

log = logging.getLogger("robots.bot_rotina")''',
        rb("_notif_body.txt"),
        rb("_contar_body.txt"),
    )

    w(
        PKG / "io.py",
        '''"""I/O de arquivos, parquet, downloads e utilitários."""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.config import *
from app.infrastructure.file_mirror import espelhar_arquivo
from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA, EXPECTED_COLUMNS
from app.bots.rotina.csv_merge import CSVReader, MergeInteligente
from app.bots.rotina.state import (
    _registrar_arquivo_resumo,
    _registrar_duplicatas_internas,
    _registrar_erro,
    _set_progress,
    _set_status,
)

log = logging.getLogger("robots.bot_rotina")''',
        rb("_io_utils_body.txt"),
        rb("_io_csv_body.txt"),
        rb("_io_download_body.txt"),
        rb("_io_parquet_body.txt"),
        rb("_io_dates_body.txt"),
    )

    w(
        PKG / "selenium_brflow.py",
        '''"""Selenium BRFlow: login, navegação e download."""
from __future__ import annotations

import glob
import logging
import os
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException

from app.config import *
from app.infrastructure.selenium_helpers import click_element, login_okta_resiliente, send_keys_to_element
from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA
from app.bots.rotina.io import (
    _aguardar_download_completo,
    _limpar_downloads_temp_inicio,
    _limpar_pasta,
)
from app.bots.rotina.state import _registrar_erro, _set_progress, _set_status

log = logging.getLogger("robots.bot_rotina")''',
        rb("_selenium_body.txt"),
    )

    w(
        PKG / "tasks" / "detalhado.py",
        '''"""Tarefa BRBR-4467 detalhado (rotinas 1d/2d/3d)."""
from __future__ import annotations

import glob
import logging
import os
import shutil
import time
from pathlib import Path

from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA
from app.bots.rotina.io import (
    _combinar_arquivos_em_um,
    _extrair_dias_atras_da_descricao,
    _gerar_nome_arquivo_com_data,
    _limpar_downloads_temp_inicio,
    _mover_arquivos_para_pasta_final,
)
from app.bots.rotina.notifications import _task_id_por_descricao_rotina
from app.bots.rotina.selenium_brflow import _baixar_arquivos_rotina
from app.bots.rotina.state import (
    _registrar_erro,
    _registrar_status_tarefa,
    _set_progress,
    _set_status,
    parar_event,
)

log = logging.getLogger("robots.bot_rotina")''',
        rb("tasks/_detalhado_body.txt"),
    )

    w(
        PKG / "tasks" / "produtividade.py",
        '''"""Tarefa produtividade D-1."""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd

from app.config import *
from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA
from app.bots.rotina.io import (
    _corrigir_colunas_dataframe,
    _ler_arquivo_csv_robusto,
    _ler_csv_tratamento,
    _listar_arquivos_baixados_validos,
    _limpar_downloads_temp_inicio,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
)
from app.bots.rotina.selenium_brflow import _baixar_arquivos_rotina
from app.bots.rotina.state import (
    _registrar_arquivo_resumo,
    _registrar_erro,
    _set_progress,
    _set_status,
)

log = logging.getLogger("robots.bot_rotina")''',
        rb("tasks/_prod_tratar_body.txt"),
        rb("tasks/_prod_download_body.txt"),
    )

    w(
        PKG / "tasks" / "auditoria.py",
        '''"""Tarefas auditoria replicados D1 e etapas."""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd

from app.config import *
from app.bots.rotina.constants import (
    COLUNAS_AUDITORIA_REPLICADOS_D1,
    DOWNLOADS_TEMP_ROTINA,
    TASK_AUDITORIA_ETAPAS,
    TASK_AUDITORIA_REPLICADOS_D1,
)
from app.bots.rotina.io import (
    _ler_arquivo_csv_robusto,
    _limpar_downloads_temp_inicio,
    _normalizar_nome_coluna,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
    _salvar_arquivo_consolidado,
)
from app.bots.rotina.selenium_brflow import _baixar_arquivos_rotina
from app.bots.rotina.state import (
    _registrar_erro,
    _registrar_status_tarefa,
    _set_progress,
    _set_status,
)

log = logging.getLogger("robots.bot_rotina")''',
        rb("tasks/_auditoria_body.txt"),
    )

    w(
        PKG / "tasks" / "monitor.py",
        '''"""Tarefa monitor de eventos e ETL offline."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

import pandas as pd

from app.config import *
from app.bots.rotina.io import (
    _ler_csv_tratamento,
    _limpar_downloads_temp_inicio,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
    parse_datetime,
    verifica_usuario,
)
from app.bots.rotina.state import _registrar_erro, _set_progress, _set_status

log = logging.getLogger("robots.bot_rotina")''',
        rb("tasks/_monitor_tratar_body.txt"),
        rb("tasks/_normalizar_monitor_body.txt"),
        rb("tasks/_monitor_download_body.txt"),
    )

    w(
        PKG / "tasks" / "unificados.py",
        '''"""Joins prod-unificado e monitor-unificado."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import *
from app.bots.rotina.constants import COLUNAS_MONITOR_UNIFICADO
from app.bots.rotina.io import _preparar_dataframe_para_parquet

log = logging.getLogger("robots.bot_rotina")''',
        rb("tasks/_unificados_prod_body.txt"),
        rb("tasks/_unificados_monitor_body.txt"),
    )

    confer_body = rb("tasks/_confer_producao_body.txt") + "\n\n" + rb("tasks/_confer_log_body.txt")
    # Remove duplicate local confer helpers replaced by confer_helpers
    for fn in (
        "def _fechar_calendario_confer(",
        "def _clique_xpath_js(",
        "def _focar_aba_confer(",
        "def _fechar_calendario_confer_rotina(",
        "def _click_com_js(",
    ):
        while fn in confer_body:
            start = confer_body.index(fn)
            # find next def at column 0 or end
            rest = confer_body[start + len(fn):]
            next_def = rest.find("\ndef ")
            if next_def == -1:
                confer_body = confer_body[:start]
            else:
                confer_body = confer_body[:start] + confer_body[start + len(fn) + next_def + 1:]

    w(
        PKG / "tasks" / "confer.py",
        '''"""Tarefas Confer: produção, log eventos e sessões."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException, WebDriverException

from app.config import *
from app.infrastructure.confer_helpers import (
    CONFER_HOME_XPATH,
    click_xpath_com_fallback_js as _click_com_js,
    fechar_calendario_se_aberto as _fechar_calendario_confer,
    fechar_calendario_se_aberto as _fechar_calendario_confer_rotina,
    focar_aba_confer as _focar_aba_confer,
    recuperar_menu_confer_se_tela_login as _recuperar_menu_confer_se_tela_login_rotina,
    renomear_download_com_sufixo as _renomear_download_com_sufixo_confer,
)
from app.infrastructure.csv_processing import read_csv as cp_read_csv
from app.infrastructure.selenium_helpers import click_element, send_keys_to_element
from app.bots.rotina.io import (
    _corrigir_texto_mojibake,
    _ler_csv_tratamento,
    _normalizar_nome_coluna,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
)
from app.bots.rotina.selenium_brflow import _baixar_relatorio_retry_rotina
from app.bots.rotina.state import _set_progress, _set_status
from app.bots.rotina.tasks.monitor import _normalizar_df_monitor_sessoes

log = logging.getLogger("robots.bot_rotina")

_CONFER_LOGIN_BUTTON_XPATH = "/html/body/app-root/app-login/main/div/div/div/div/div/div/div/button"
_CONFER_MENU_ROOT_XPATH = "/html/body/app-root/app-home/main/app-menu"
MAX_RETRY_DOWNLOAD_CONFER_ROTINA = max(1, int(os.getenv("CONFER_DOWNLOAD_RETRY", "4") or "4"))''',
        confer_body,
    )

    print("Modules assembled. Building registry and orchestration...")


if __name__ == "__main__":
    main()
