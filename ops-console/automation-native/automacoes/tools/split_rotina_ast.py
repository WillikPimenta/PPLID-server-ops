"""Split bot_rotina.py into app/bots/rotina/ package using AST extraction."""
from __future__ import annotations

import ast
import shutil
import textwrap
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "bots" / "bot_rotina.py.bak"
PKG = ROOT / "app" / "bots" / "rotina"

# Map module -> names to extract (functions, classes, or module-level assigns)
MODULE_MEMBERS: Dict[str, List[str]] = {
    "constants": [
        "ROTINAS_PARA_BAIXAR",
        "TASK_ROTINAS_1d", "TASK_ROTINAS_2d", "TASK_ROTINAS_3d",
        "TASK_PRODUTIVIDADE_D1", "TASK_AUDITORIA_REPLICADOS_D1", "TASK_AUDITORIA_ETAPAS",
        "TASK_MONITOR_EVENTOS", "TASK_CONFER_PRODUCAO", "TASK_LOG_EVENTOS",
        "TASK_PROD_UNIFICADO", "TASK_MONITOR_UNIFICADO",
        "COLUNAS_MONITOR_UNIFICADO", "COLUNAS_AUDITORIA_REPLICADOS_D1",
        "TASK_TO_ROTINA_DESCRICAO", "TASK_DEPENDENCIES", "TASK_DEFAULT_ALL",
        "HORA_EXECUCAO_ROTINA", "MINUTO_EXECUCAO_ROTINA",
        "DOWNLOADS_TEMP_BASE", "DOWNLOADS_TEMP_ROTINA",
        "PRODUTIVIDADE_D1_DESCRICAO", "AUDITORIA_ETAPAS_DESCRICAO",
        "DEMANDAS_TABELA", "TAREFAS_DEMANDAS", "OBS_HUMANIZADA",
        "EXPECTED_COLUMNS", "CSV_ENCODINGS", "CSV_SEPARATORS", "CSV_CHUNK_SIZE",
    ],
    "state": [
        "log", "_runtime", "parar_event", "set_status_callback", "set_progress_callback",
        "_set_status", "_set_progress", "_reset_progress_state",
        "timing_start", "exec_summary",
        "_reset_exec_summary", "_inicializar_status_tarefas", "_registrar_status_tarefa",
        "_registrar_erro", "_registrar_arquivo_resumo", "_registrar_duplicatas_internas",
        "_timing_start_step", "_timing_end_step",
    ],
    "notifications": [
        "_task_id_por_descricao_rotina", "_extra_obs_rules_rotina", "_humanizar_observacao",
        "_teams_notificacao_habilitada", "_build_adaptive_card_demandas",
        "_contar_linhas_arquivo", "_gerar_resumo_notificacao_sucesso", "_gerar_resumo_notificacao_erro",
    ],
    "csv_merge": ["CSVReader", "MergeInteligente"],
    "io": [
        "_get_tz_br", "_criar_pastas_necessarias", "_limpar_pasta", "_limpar_downloads_temp_inicio",
        "_fix_single_column_dataframe", "_aguardar_download_completo",
        "_obter_data_base_execucao", "_listar_arquivos_baixados_validos",
        "_extrair_dias_atras_da_descricao", "_gerar_nome_arquivo_com_data",
        "_merge_com_arquivo_existente", "_preparar_dataframe_para_parquet",
        "_salvar_detalhado_bruto_parquet", "_salvar_arquivo_final_parquet",
        "_mover_arquivos_para_pasta_final", "_combinar_arquivos_em_um",
        "_agrupar_arquivos_por_data", "_processar_arquivos_do_dia",
        "_ler_arquivo_csv_robusto", "_corrigir_colunas_dataframe",
        "_extrair_celula_matricula", "_coluna_matricula_series",
        "_matricula_ja_e_flag", "_flag_matricula_para_int", "_classificar_matricula_flag",
        "_log_contagem_matricula_flag", "_aplicar_limpeza_bi", "_remover_duplicatas",
        "_salvar_arquivo_consolidado", "_normalizar_data_para_iso", "_resolver_datas_execucao",
        "_corrigir_texto_mojibake", "_normalizar_nome_coluna", "_ler_csv_tratamento",
        "verifica_usuario", "parse_datetime",
    ],
    "selenium_brflow": [
        "_baixar_arquivos_rotina", "_fazer_login", "_navegar_para_brflow",
        "_baixar_relatorio_retry_rotina", "_click_com_js",
    ],
    "tasks/detalhado": ["_baixar_e_combinar_rotinas"],
    "tasks/produtividade": ["tratar_produtividade", "_baixar_produtividade_d1"],
    "tasks/auditoria": [
        "_resolver_relatorio_replicacao", "_normalizar_protocolo_conferencia",
        "_gerar_conferencia_replicados_d1", "_baixar_auditoria_replicados_d1", "_baixar_auditoria_etapas",
    ],
    "tasks/monitor": [
        "_pretratar_monitor_verifica_usuario", "tratar_arquivo", "_baixar_monitor_eventos_d1",
        "_normalizar_df_monitor_sessoes",
    ],
    "tasks/unificados": [
        "_juntar_confer_prod_tratados_dia", "_juntar_monitor_tratado_com_monitor_confer_dia",
    ],
    "tasks/confer": [
        "_fechar_calendario_confer", "_clique_xpath_js", "_focar_aba_confer",
        "_tratar_producao_confer_df", "_baixar_relatorio_producao_confer",
        "_diagnosticar_tela_confer_rotina", "_esta_na_tela_login_confer_rotina",
        "_recuperar_menu_confer_se_tela_login_rotina", "_fechar_calendario_confer_rotina",
        "_extrair_matriculas_confer_d1", "limpar_pasta_download_confer",
        "obter_arquivo_recente_confer", "validar_arquivo_confer", "baixar_com_retry_seguro_confer",
        "_carregar_janelas_confer_prod_bruto_d1", "_selecionar_matricula_ngx_select_rotina",
        "_gerar_parquet_sessoes_monitor_confer", "_normalizar_sufixo_nome_confer",
        "_renomear_download_com_sufixo_confer", "_remover_arquivo_se_existir_confer",
        "_limpar_arquivos_temporarios_confer", "_identificar_coluna",
        "_formatar_datetime_tabela", "_formatar_data_base_tabela",
        "_gerar_csv_sessoes_por_evento", "_consolidar_csv_sessoes_confer_d1",
        "log_eventos",
        "_CONFER_LOGIN_BUTTON_XPATH", "_CONFER_MENU_ROOT_XPATH",
    ],
    "orchestration": [
        "start", "stop", "executar_novo_bot", "_executar_iteracao_unica",
        "_parse_tarefas", "_resolve_deps", "_executar_ciclo", "_aguardar_horario_execucao",
    ],
}

# Rename private module-level names in constants
CONST_RENAMES = {
    "_DEMANDAS_TABELA": "DEMANDAS_TABELA",
    "_TAREFAS_DEMANDAS": "TAREFAS_DEMANDAS",
    "_OBS_HUMANIZADA": "OBS_HUMANIZADA",
    "_timing_start": "timing_start",
    "_exec_summary": "exec_summary",
}

MODULE_HEADERS = {
    "constants": '''"""Constantes e identificadores de tarefas da rotina."""
from __future__ import annotations

from app.config import PLAN_IDF_SERASA_BOTS
''',
    "state": '''"""Estado de execução e callbacks BotRuntime."""
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
''',
    "notifications": '''"""Notificações Teams e resumo de execução."""
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
''',
    "csv_merge": '''"""Leitura CSV e merge inteligente BRFlow."""
from __future__ import annotations

import logging
import os

import pandas as pd

from app.bots.rotina.constants import CSV_CHUNK_SIZE, CSV_ENCODINGS, CSV_SEPARATORS

log = logging.getLogger("robots.bot_rotina")
''',
    "io": '''"""I/O de arquivos, parquet, downloads e utilitários de data."""
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
from app.bots.rotina.notifications import _contar_linhas_arquivo

log = logging.getLogger("robots.bot_rotina")
''',
    "selenium_brflow": '''"""Selenium BRFlow: login, navegação e download genérico."""
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
    _obter_data_base_execucao,
)
from app.bots.rotina.state import _registrar_erro, _set_progress, _set_status

log = logging.getLogger("robots.bot_rotina")
''',
}


def load_source() -> tuple[list[str], ast.Module]:
    text = SRC.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    return lines, ast.parse(text)


def extract_nodes(lines: list[str], tree: ast.Module, names: Set[str]) -> str:
    chunks: List[str] = []
    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    name = t.id
        if name and name in names:
            chunk = "".join(lines[node.lineno - 1 : node.end_lineno])
            for old, new in CONST_RENAMES.items():
                chunk = chunk.replace(old, new)
            chunks.append(chunk)
    return "\n\n".join(chunks)


def build_all() -> None:
    lines, tree = load_source()
    all_assigned = set()
    for members in MODULE_MEMBERS.values():
        all_assigned.update(members)

    for mod, members in MODULE_MEMBERS.items():
        rel = mod.replace("/", os_sep := "/")
        path = PKG / f"{mod}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = MODULE_HEADERS.get(mod.replace("tasks/", ""), MODULE_HEADERS.get("io", ""))
        if mod.startswith("tasks/"):
            task_name = mod.split("/")[1]
            header = f'"""Tarefa rotina: {task_name}."""\nfrom __future__ import annotations\n\n# imports filled by post-process\n'
        body = extract_nodes(lines, tree, set(members))
        if not body.strip():
            print(f"WARN: empty module {mod}")
        path.write_text(header + "\n" + body + "\n", encoding="utf-8")
        print(f"Wrote {path} ({path.stat().st_size} bytes)")

    # Fix constants renames in extracted body
    const_path = PKG / "constants.py"
    const_text = const_path.read_text(encoding="utf-8")
    for old, new in CONST_RENAMES.items():
        const_text = const_text.replace(old, new)
    const_path.write_text(const_text, encoding="utf-8")

    # Fix state renames
    state_path = PKG / "state.py"
    state_text = state_path.read_text(encoding="utf-8")
    for old, new in CONST_RENAMES.items():
        state_text = state_text.replace(old, new)
    state_path.write_text(state_text, encoding="utf-8")

    print("AST extraction complete.")


import os as _os

if __name__ == "__main__":
    build_all()
