"""Assemble extracted body files into rotina package modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "app" / "bots" / "rotina"


def read_body(name: str) -> str:
    path = PKG / name
    if not path.exists():
        path = PKG / "tasks" / name
    text = path.read_text(encoding="utf-8")
    return apply_renames(text)


def apply_renames(text: str) -> str:
    renames = {
        "_exec_summary": "exec_summary",
        "_timing_start": "timing_start",
        "_TAREFAS_DEMANDAS": "TAREFAS_DEMANDAS",
        "_DEMANDAS_TABELA": "DEMANDAS_TABELA",
        "_OBS_HUMANIZADA": "OBS_HUMANIZADA",
    }
    for old, new in renames.items():
        text = text.replace(old, new)
    return text


def write_module(path: Path, header: str, *bodies: str) -> None:
    content = header + "\n\n" + "\n\n".join(bodies).strip() + "\n"
    path.write_text(content, encoding="utf-8")
    print(f"Assembled {path.relative_to(ROOT)} ({len(content.splitlines())} lines)")


STATE_HEADER = '''"""Estado de execução e callbacks BotRuntime."""
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
}
'''

NOTIF_HEADER = '''"""Notificações Teams e resumo de execução."""
from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd

from app.infrastructure.teams_adaptive_card import build_status_card, humanizar_observacao
from app.bots.rotina.constants import DEMANDAS_TABELA, EXPECTED_COLUMNS, OBS_HUMANIZADA, TASK_TO_ROTINA_DESCRICAO
from app.bots.rotina.state import exec_summary
from app.bots.rotina.csv_merge import CSVReader

log = logging.getLogger("robots.bot_rotina")
'''

CSV_HEADER = '''"""Leitura CSV e merge inteligente BRFlow."""
from __future__ import annotations

import logging
import os

import pandas as pd

from app.bots.rotina.constants import CSV_CHUNK_SIZE, CSV_ENCODINGS, CSV_SEPARATORS

log = logging.getLogger("robots.bot_rotina")
'''

IO_HEADER = '''"""I/O de arquivos, parquet, downloads e utilitários."""
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

log = logging.getLogger("robots.bot_rotina")
'''

SELENIUM_HEADER = '''"""Selenium BRFlow: login, navegação e download."""
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

log = logging.getLogger("robots.bot_rotina")
'''

DETALHADO_HEADER = '''"""Tarefa BRBR-4467 detalhado (rotinas 1d/2d/3d)."""
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

log = logging.getLogger("robots.bot_rotina")
'''

MONITOR_HEADER = '''"""Tarefa monitor de eventos e ETL offline."""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime

import pandas as pd

from app.config import *
from app.bots.rotina.io import (
    _ler_csv_tratamento,
    _preparar_dataframe_para_parquet,
    parse_datetime,
    verifica_usuario,
)
from app.bots.rotina.state import _set_status

log = logging.getLogger("robots.bot_rotina")
'''

PROD_AUD_HEADER = '''"""Tarefas produtividade D-1 e auditoria."""
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
    _corrigir_colunas_dataframe,
    _ler_arquivo_csv_robusto,
    _ler_csv_tratamento,
    _listar_arquivos_baixados_validos,
    _normalizar_nome_coluna,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
    _salvar_arquivo_consolidado,
    verifica_usuario,
)
from app.bots.rotina.selenium_brflow import _baixar_arquivos_rotina
from app.bots.rotina.state import (
    _registrar_arquivo_resumo,
    _registrar_erro,
    _registrar_status_tarefa,
    _set_progress,
    _set_status,
)
from app.bots.rotina.tasks.produtividade import tratar_produtividade

log = logging.getLogger("robots.bot_rotina")
'''

CONFER_PROD_HEADER = '''"""Tarefa produção Confer (parte Selenium inicial)."""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from app.config import *
from app.infrastructure.confer_helpers import (
    CONFER_HOME_XPATH,
    click_xpath_com_fallback_js as _click_com_js,
    fechar_calendario_se_aberto as _fechar_calendario_confer,
    focar_aba_confer as _focar_aba_confer,
)
from app.bots.rotina.io import (
    _ler_csv_tratamento,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
)
from app.bots.rotina.state import _set_progress, _set_status

log = logging.getLogger("robots.bot_rotina")
'''

UNIFICADOS_HEADER = '''"""Joins prod-unificado e monitor-unificado."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import *
from app.bots.rotina.constants import COLUNAS_MONITOR_UNIFICADO
from app.bots.rotina.io import _preparar_dataframe_para_parquet

log = logging.getLogger("robots.bot_rotina")
'''

CONFER_LOG_HEADER = '''"""Tarefas Confer: log eventos, sessões e helpers."""
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
    fechar_calendario_se_aberto as _fechar_calendario_confer_rotina,
    focar_aba_confer,
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
'''

ORCH_HEADER = '''"""Orquestração: start, stop, ciclo principal."""
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
    TASK_AUDITORIA_ETAPAS,
    TASK_AUDITORIA_REPLICADOS_D1,
    TASK_CONFER_PRODUCAO,
    TASK_DEFAULT_ALL,
    TASK_DEPENDENCIES,
    TASK_LOG_EVENTOS,
    TASK_MONITOR_EVENTOS,
    TASK_MONITOR_UNIFICADO,
    TASK_PROD_UNIFICADO,
    TASK_PRODUTIVIDADE_D1,
    TASK_ROTINAS_1d,
    TASK_ROTINAS_2d,
    TASK_ROTINAS_3d,
    TASK_TO_ROTINA_DESCRICAO,
)
from app.bots.rotina.io import (
    _get_tz_br,
    _limpar_downloads_temp_inicio,
    _limpar_pasta,
    _obter_data_base_execucao,
    _resolver_datas_execucao,
)
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


def main() -> None:
    write_module(PKG / "state.py", STATE_HEADER, read_body("_state_body.txt"), read_body("_timing_body.txt"))
    write_module(PKG / "csv_merge.py", CSV_HEADER, read_body("csv_merge_body.txt"))
    write_module(
        PKG / "notifications.py",
        NOTIF_HEADER,
        read_body("_notif_body.txt"),
        read_body("_contar_body.txt"),
    )
    write_module(
        PKG / "io.py",
        IO_HEADER,
        read_body("_io_utils_body.txt"),
        read_body("_io_download_body.txt"),
        read_body("_io_parquet_body.txt"),
        read_body("_io_dates_body.txt"),
    )
    write_module(PKG / "selenium_brflow.py", SELENIUM_HEADER, read_body("_selenium_body.txt"))

    # Split prod_aud body: produtividade (1914-2010) + auditoria (2012-2327)
    prod_aud = read_body("tasks/_prod_aud_body.txt")
    # tratar_produtividade starts at line 1222 in original - it's in monitor body
    write_module(PKG / "tasks" / "detalhado.py", DETALHADO_HEADER, read_body("tasks/_detalhado_body.txt"))

    monitor_body = read_body("tasks/_monitor_body.txt")
    # Split monitor: produtividade function is in first part
    write_module(PKG / "tasks" / "monitor.py", MONITOR_HEADER, monitor_body)

    write_module(PKG / "tasks" / "produtividade.py", PROD_AUD_HEADER.replace("from app.bots.rotina.tasks.produtividade import tratar_produtividade", ""), prod_aud)

    write_module(PKG / "tasks" / "auditoria.py", PROD_AUD_HEADER.replace("from app.bots.rotina.tasks.produtividade import tratar_produtividade", ""), "")

    write_module(PKG / "tasks" / "confer.py", CONFER_LOG_HEADER, read_body("tasks/_confer_log_body.txt"))
    write_module(PKG / "tasks" / "unificados.py", UNIFICADOS_HEADER, read_body("tasks/_unificados_body.txt"))

    # Fix produtividade - extract tratar_produtividade from monitor body
    write_module(PKG / "orchestration.py", ORCH_HEADER, read_body("_orch_body.txt"))

    print("Assembly done.")


if __name__ == "__main__":
    main()
