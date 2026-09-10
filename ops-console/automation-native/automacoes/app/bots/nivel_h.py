"""Robô de Nível Hierárquico com automação web e processamento de CSV."""

import sys
from pathlib import Path


import os
import time
import threading
import logging
import json
import subprocess
import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from app.infrastructure.selenium_helpers import click_element, send_keys_to_element, create_driver, take_error_screenshot, wait_for_element, login_okta_resiliente
from app.config.paths import (
    DEFAULT_DOWNLOAD,
    DEFAULT_INTERVAL,
    DEFAULT_MODEL,
    DEFAULT_SHAREPOINT,
    DEFAULT_TIMEOUT,
    dim_path,
)
from app.config.selectors import brflow, okta
from app.infrastructure.csv_processing import read_csv as cp_read_csv, write_xlsx_from_df as cp_write_xlsx, wait_for_new_file as cp_wait_for_new_file
from app.core.credentials import get_credentials
from app.core.common import ErrorRecoveryHandler, safe_close_driver, MetricsContext

# Logger centralizado - usa o sistema configurado via configure_logging()
log = logging.getLogger("robots.nivel_h")

from app.core.bot_runtime import BotRuntime

_runtime = BotRuntime(mode="nivel")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state
_begin_cycle = _runtime.begin_cycle



def salvar_json_nivel(df, caminho_saida):
    """
    Salva o DataFrame do Nível Hierárquico em formato JSON.
    
    Args:
        df: DataFrame com os dados processados
        caminho_saida: Caminho do arquivo JSON de saída
        
    Returns:
        bool: True se salvo com sucesso, False caso contrário
    """
    try:
        # Converter DataFrame para lista de dicionários
        dados = df.to_dict(orient='records')
        
        # Converter valores numéricos float para int (remover .0)
        for item in dados:
            if 'NHID' in item and isinstance(item['NHID'], float):
                # Converter float para int, exceto se for vazio ou NaN
                if pd.notna(item['NHID']) and item['NHID'] != '':
                    item['NHID'] = int(item['NHID'])
        
        # Salvar em JSON
        with open(caminho_saida, 'w', encoding='utf-8') as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        
        log.info(f"JSON salvo com sucesso: {caminho_saida} ({len(dados)} registros)")
        return True
    
    except Exception as e:
        log.error(f"Erro ao salvar JSON: {e}")
        return False


def processar_nivel_com_id(df):
    """
    Processa o dataframe do Nível Hierárquico:
    - Mantém apenas as colunas 'Nivel hierarquico' e 'Matricula'
    - Adiciona coluna 'NHID' fazendo lookup no arquivo dimNivelHierarquivo.csv
    - O lookup compara 'Nivel hierarquico' das duas planilhas e traz o ID
    
    Args:
        df: DataFrame com os dados baixados
        
    Returns:
        DataFrame processado com as colunas selecionadas e NHID
    """
    try:
        # Normalizar nomes de colunas (remover espaços)
        df.columns = df.columns.str.strip()
        
        # Encontrar a coluna de Nível Hierárquico (pode ter variações)
        nivel_col = None
        for col in df.columns:
            if 'nivel' in col.lower() and 'hierarquico' in col.lower():
                nivel_col = col
                break
        
        if nivel_col is None:
            log.error(f"Coluna 'Nivel hierarquico' não encontrada. Colunas disponíveis: {df.columns.tolist()}")
            return df
        
        # Encontrar a coluna de Matrícula (pode ter variações)
        matricula_col = None
        for col in df.columns:
            if 'matricula' in col.lower():
                matricula_col = col
                break
        
        if matricula_col is None:
            log.warning(f"Coluna 'Matricula' não encontrada. Colunas disponíveis: {df.columns.tolist()}")
            matricula_col = df.columns[1] if len(df.columns) > 1 else None
        
        # Manter apenas as colunas necessárias
        colunas_necessarias = [nivel_col]
        if matricula_col:
            colunas_necessarias.append(matricula_col)
        
        df_processado = df[colunas_necessarias].copy()
        
        # Renomear para nomes padrão
        df_processado.rename(columns={nivel_col: "Nivel hierarquico"}, inplace=True)
        if matricula_col:
            df_processado.rename(columns={matricula_col: "Matricula"}, inplace=True)
        
        # Carregar o arquivo de dimensão de Nível Hierárquico

        
        if not os.path.exists(dim_path):
            log.warning(f"Arquivo de dimensão não encontrado: {dim_path}")
            df_processado["NHID"] = 1
            return df_processado
        
        # Ler a dimensão com detecção de separador
        dim_df = cp_read_csv(dim_path, detect_sep=True)
        
        # Normalizar nomes de colunas da dimensão
        dim_df.columns = dim_df.columns.str.strip()
        
        # Encontrar a coluna de Nível Hierárquico na dimensão
        dim_nivel_col = None
        for col in dim_df.columns:
            if 'nivel' in col.lower() and 'hierarquico' in col.lower():
                dim_nivel_col = col
                break
        
        if dim_nivel_col is None:
            log.error(f"Coluna 'Nivel hierarquico' não encontrada na dimensão. Colunas: {dim_df.columns.tolist()}")
            df_processado["NHID"] = 1
            return df_processado
        
        # Identificar a coluna de ID na dimensão
        id_col = None
        for col in dim_df.columns:
            if col.lower() in ['id', 'nhid', 'nivel_id', 'hierarquia_id']:
                id_col = col
                break
        
        if id_col is None:
            # Se não encontrar, usar a primeira coluna que não seja a de Nível
            for col in dim_df.columns:
                if col != dim_nivel_col:
                    id_col = col
                    break
        
        if id_col is None:
            log.warning("Não foi possível identificar a coluna de ID na dimensão")
            df_processado["NHID"] = 1
            return df_processado
        
        # Normalizar valores para o lookup
        dim_df[dim_nivel_col] = dim_df[dim_nivel_col].astype(str).str.strip()
        df_processado["Nivel hierarquico"] = df_processado["Nivel hierarquico"].astype(str).str.strip()
        
        # Criar dicionário de lookup: Nível Hierárquico -> ID
        nivel_id_map = dict(zip(dim_df[dim_nivel_col], dim_df[id_col]))
        
        # Fazer o merge/lookup: comparar 'Nivel hierarquico' e trazer o ID
        df_processado["NHID"] = df_processado["Nivel hierarquico"].map(nivel_id_map).fillna(1)
        
        # Contar quantos registros conseguimos relacionar
        registros_relacionados = len(df_processado[df_processado['NHID'] != 1])
        log.info(f"Processamento concluído: {len(df_processado)} registros, {registros_relacionados} com NHID encontrado")
        
        return df_processado
    
    except Exception as e:
        log.error(f"Erro ao processar nível com ID: {e}")
        log.debug(f"Colunas do dataframe: {df.columns.tolist()}")
        # Retornar o dataframe original em caso de erro
        return df


def _get_onedrive_roots():
    """Retorna pastas candidatas gerenciadas pelo OneDrive."""
    roots = []
    for env_name in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
        value = os.getenv(env_name)
        if value:
            roots.append(Path(value).expanduser())

    roots.append(Path.home() / "EXPERIAN SERVICES CORP")

    unique_roots = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve(strict=False)
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key not in seen:
            unique_roots.append(resolved)
            seen.add(key)
    return unique_roots


def _is_onedrive_path(path: Path) -> bool:
    """Verifica se o arquivo/pasta parece estar dentro de uma pasta sincronizada."""
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        resolved = path

    for root in _get_onedrive_roots():
        try:
            if resolved == root or root in resolved.parents:
                return True
        except Exception:
            continue

    return "EXPERIAN SERVICES CORP" in {part.upper() for part in resolved.parts}


def _is_onedrive_running() -> bool:
    """Verifica se o processo do OneDrive está ativo."""
    if os.name != "nt":
        return False

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OneDrive.exe", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return "OneDrive.exe" in result.stdout
    except Exception as exc:
        log.debug(f"Não foi possível verificar processo do OneDrive: {exc}")
        return False


def restart_onedrive() -> bool:
    """Reinicia o cliente do OneDrive em ambientes Windows."""
    if os.name != "nt":
        return False

    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "OneDrive.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except Exception as exc:
        log.debug(f"Falha ao encerrar OneDrive.exe: {exc}")

    time.sleep(5)

    candidates = [
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "OneDrive" / "OneDrive.exe",
        Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "Microsoft OneDrive" / "OneDrive.exe",
        Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft OneDrive" / "OneDrive.exe",
    ]

    for candidate in candidates:
        if not candidate or not candidate.exists():
            continue
        try:
            subprocess.Popen(
                [str(candidate)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.warning(f"OneDrive reiniciado via {candidate}")
            return True
        except Exception as exc:
            log.warning(f"Falha ao iniciar OneDrive em {candidate}: {exc}")

    log.error("Não foi possível localizar o executável do OneDrive para reinício")
    return False


def _read_onedrive_sync_status(path: Path):
    """Lê propriedades de sincronização do Windows Shell para um arquivo."""
    if os.name != "nt" or not path.exists():
        return {}

    escaped = str(path).replace("'", "''")
    ps_script = f"""
$path = '{escaped}'
$item = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
if ($null -eq $item) {{ return }}
$shell = New-Object -ComObject Shell.Application
$folder = $shell.Namespace($item.DirectoryName)
if ($null -eq $folder) {{ return }}
$file = $folder.ParseName($item.Name)
if ($null -eq $file) {{ return }}
[pscustomobject]@{{
    SyncTransferStatus = $file.ExtendedProperty('System.SyncTransferStatus')
    StorageProviderStatus = $file.ExtendedProperty('System.StorageProviderStatus')
    FileOfflineAvailabilityStatus = $file.ExtendedProperty('System.FileOfflineAvailabilityStatus')
    Length = $item.Length
    LastWriteTimeUtc = [DateTime]::SpecifyKind($item.LastWriteTimeUtc, [DateTimeKind]::Utc).ToString('o')
}} | ConvertTo-Json -Compress
"""

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        output = (result.stdout or "").strip()
        if not output:
            return {}
        return json.loads(output)
    except Exception as exc:
        log.debug(f"Falha lendo status de sync do OneDrive para {path}: {exc}")
        return {}


def _is_pending_sync_status(status: dict) -> bool:
    """Heurística para identificar upload/download em andamento."""
    sync_value = status.get("SyncTransferStatus")
    if sync_value in (None, "", 0, "0"):
        return False
    return True


def ensure_onedrive_sync(file_paths, timeout=180, poll_interval=5, stall_limit=3):
    """Monitora sincronização do OneDrive e reinicia o cliente se detectar travamento."""
    if os.name != "nt":
        return True

    monitored_files = []
    for file_path in file_paths:
        path = Path(file_path)
        if path.exists() and _is_onedrive_path(path):
            monitored_files.append(path)

    if not monitored_files:
        return True

    if not _is_onedrive_running():
        log.warning("OneDrive não estava em execução; tentando reiniciar antes do monitoramento")
        restart_onedrive()
        time.sleep(8)

    deadline = time.time() + max(timeout, 10)
    last_snapshot = {}
    stalled_counts = {str(path): 0 for path in monitored_files}
    saw_sync_signal = False

    while time.time() < deadline and not parar_event.is_set():
        pending_files = []

        for path in monitored_files:
            status = _read_onedrive_sync_status(path)
            snapshot = (
                status.get("SyncTransferStatus"),
                status.get("StorageProviderStatus"),
                status.get("FileOfflineAvailabilityStatus"),
                status.get("Length"),
                status.get("LastWriteTimeUtc"),
            )
            if any(status.get(key) not in (None, "") for key in (
                "SyncTransferStatus",
                "StorageProviderStatus",
                "FileOfflineAvailabilityStatus",
            )):
                saw_sync_signal = True

            if _is_pending_sync_status(status):
                pending_files.append(path)
                key = str(path)
                stalled_counts[key] = stalled_counts[key] + 1 if last_snapshot.get(key) == snapshot else 0
                last_snapshot[key] = snapshot
            else:
                stalled_counts[str(path)] = 0
                last_snapshot[str(path)] = snapshot

        if not pending_files:
            if saw_sync_signal:
                log.info("OneDrive confirmou ausência de transferências pendentes para os arquivos de nível hierárquico")
            else:
                log.info("Monitoramento do OneDrive sem propriedades de sync disponíveis; seguindo com heurística local")
            return True

        stalled = [path for path in pending_files if stalled_counts.get(str(path), 0) >= stall_limit]
        if stalled or not _is_onedrive_running():
            stalled_names = ", ".join(path.name for path in stalled) if stalled else "processo OneDrive indisponível"
            log.warning(f"Sincronização do OneDrive aparenta travada ({stalled_names}); reiniciando cliente")
            restart_onedrive()
            time.sleep(8)
            last_snapshot.clear()
            stalled_counts = {str(path): 0 for path in monitored_files}

        time.sleep(max(poll_interval, 1))

    log.warning("Timeout aguardando sincronização do OneDrive; executando reinício preventivo")
    restart_onedrive()
    return False


def baixar_nivel_loop(intervalo=DEFAULT_INTERVAL, tempo_max=None, settings=None):
    _reset_progress_state()
    _set_status("Nivel: iniciando loop")
    _set_progress(0, "Nivel: iniciando loop")
    
    # Iniciar contexto de execução
    with MetricsContext("nivel_h") as exec_ctx:
        inicio = time.time()
        ciclo = 1
        error_handler = ErrorRecoveryHandler(max_errors=5, retry_delay=2)
        
        while not parar_event.is_set():
            if tempo_max and (time.time() - inicio) > tempo_max:
                _set_status("Tempo máximo atingido, reiniciando ciclo")
                inicio = time.time()
                ciclo += 1
            
            s = settings or {}
            local_download = Path(s.get("local_download") or DEFAULT_DOWNLOAD)
            output_cfg = (os.getenv("NIVEL_OUTPUT_DIR") or os.getenv("ROBOT_OUTPUT_DIR") or "").strip()
            local_sharepoint = Path(s.get("local_sharepoint_usuario") or output_cfg or DEFAULT_SHAREPOINT)
            modelo = s.get("modelo_xlsm") or DEFAULT_MODEL
            prefixo = s.get("prefixo") or "historico_de_alteracoes"
            extensao = s.get("extensao") or ".csv"
            matricula = s.get("matricula") or os.getenv("NIVEL_USER")
            senha = s.get("senha") or os.getenv("NIVEL_PASS")
            drv = None
            
            try:
                _begin_cycle()
                headless_raw = (os.getenv("NIVEL_HEADLESS") or os.getenv("ROBOT_HEADLESS") or os.getenv("HEADLESS") or "0").strip().lower()
                drv = create_driver(headless=headless_raw in ("1", "true", "yes", "on"), download_dir=str(local_download))
                exec_ctx.record_step("driver_creation", success=True)
                _set_progress(10, "Infra: driver iniciado")
                wait = WebDriverWait(drv, 20)
                
                if okta is None or brflow is None:
                    _set_status("Seletores (path.py) não configurados")
                    exec_ctx.record_step("config_validation", success=False, error_msg="Seletores não configurados")
                    break
                
                drv.get(okta.O_LINK)
                _set_progress(20, "Autenticacao: acessando Okta")
                try:
                        user, pwd = matricula, senha
                        if not user or not pwd:
                            creds = get_credentials()
                            user = user or creds[0]
                            pwd = pwd or creds[1]

                        if not user or not pwd:
                            _set_status("Credenciais não fornecidas")
                            safe_close_driver(drv)
                            return
                        login_okta_resiliente(drv, user, pwd, timeout=20)
                except TimeoutException as e:
                    _set_status(f"Erro no login Okta: {e}")
                    safe_close_driver(drv)

                _set_progress(35, "Autenticacao: Okta concluido")
                send_keys_to_element(drv, By.ID, okta.O_pesquisar, "brflow" + Keys.ENTER)
                WebDriverWait(drv, 20).until(lambda d: len(d.window_handles)>1)
                drv.switch_to.window(drv.window_handles[-1])
                _set_progress(45, "BRFlow: navegando via Okta")
                wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_usuario)))
                click_element(drv, By.XPATH, brflow.B_usuario)
                click_element(drv, By.XPATH, brflow.B_U_perfil)
                click_element(drv, By.XPATH, brflow.B_U_status)
                click_element(drv, By.XPATH, brflow.B_U_csv)
                click_element(drv, By.XPATH, brflow.B_U_pesquisar)
                
                before = [p.name for p in local_download.iterdir()] if local_download.exists() else []
                _set_progress(60, "Nivel: aguardando download")
                novo = cp_wait_for_new_file(before, local_download, prefixo, extensao, timeout=DEFAULT_TIMEOUT)
                
                if not novo:
                    _set_status("Timeout aguardando download")
                    if not error_handler.handle_error(drv, "Timeout download", _set_status, log):
                        break
                    ciclo += 1
                    continue
                
                caminho_csv = str(novo)
                destino = str(local_sharepoint / "NH" / "Nivel Hierarquico.xlsx")
                # usa csv_processing.read_csv (tenta vários encodings e detecta separador)
                _set_progress(75, "Nivel: arquivo baixado")
                _set_progress(80, "Nivel: convertendo CSV")
                df = cp_read_csv(caminho_csv, detect_sep=True)
                exec_ctx.record_step("csv_conversion", success=True)
                
                # Processar dataframe com tratamento de colunas e lookup de NHID
                _set_progress(85, "Nivel: processando dados NHID")
                df = processar_nivel_com_id(df)
                
                ok = False
                if not df.empty:
                    cp_write_xlsx(destino, df, model_path=str(modelo), table_name="TabelaNivel")
                    _set_progress(90, "Nivel: escrevendo XLSX")
                    exec_ctx.record_step("xlsx_write", success=True)
                    
                    # Salvar em JSON
                    json_path = str(local_sharepoint / "Nivel Hierarquico.json")
                    salvar_json_nivel(df, json_path)
                    _set_progress(95, "Nivel: escrevendo JSON")

                    _set_progress(97, "Nivel: validando OneDrive")
                    onedrive_ok = ensure_onedrive_sync(
                        [destino, json_path],
                        timeout=int(s.get("onedrive_sync_timeout") or os.getenv("ONEDRIVE_SYNC_TIMEOUT", "180")),
                        poll_interval=float(s.get("onedrive_sync_poll") or os.getenv("ONEDRIVE_SYNC_POLL", "5")),
                        stall_limit=int(s.get("onedrive_sync_stall_limit") or os.getenv("ONEDRIVE_SYNC_STALL_LIMIT", "3")),
                    )
                    exec_ctx.record_step("onedrive_sync", success=onedrive_ok)
                    if not onedrive_ok:
                        _set_status("OneDrive reiniciado após indício de sincronização travada")
                    
                    exec_ctx.increment_tasks(1, success=True)
                    ok = True
                    _set_progress(100, "Nivel: ciclo concluido")
                
                try:
                    os.remove(caminho_csv)
                except Exception:
                    pass
                
                _set_status(f"[Nivel] Ciclo {ciclo} finalizado ({'ok' if ok else 'erro'})")
                exec_ctx.increment_cycles(1)
                error_handler.reset()
                safe_close_driver(drv, log)
                
                waited = 0.0
                while waited < intervalo and not parar_event.is_set():
                    time.sleep(0.5); waited += 0.5
                ciclo += 1
            
            except (TimeoutException, WebDriverException) as e:
                take_error_screenshot(drv, str(type(e).__name__)[:30], "nivel_h")
                if not error_handler.handle_error(drv, "Selenium error", _set_status, log):
                    break
                ciclo += 1
                continue
            
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)[:100]}"
                take_error_screenshot(drv, str(type(e).__name__)[:30], "nivel_h")
                exec_ctx.record_step("error", success=False, error_msg=error_msg)
                if not error_handler.handle_error(drv, "Erro inesperado", _set_status, log):
                    break
                ciclo += 1
                continue
        
        _set_status("Nivel: loop encerrado")


# start/stop API
_thread = None
def start(settings=None, intervalo=DEFAULT_INTERVAL, tempo_max=None):
    global _thread
    parar_event.clear()
    if _thread and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=baixar_nivel_loop, kwargs={"intervalo":intervalo, "tempo_max":tempo_max, "settings":settings}, daemon=True)
    _thread.start()
    return _thread

def stop(timeout=5):
    parar_event.set()
    global _thread
    if _thread:
        _thread.join(timeout=timeout)
    _thread = None

if __name__ == "__main__":
    start()
    try:
        while not parar_event.is_set(): time.sleep(0.5)
    except KeyboardInterrupt:
        stop()


