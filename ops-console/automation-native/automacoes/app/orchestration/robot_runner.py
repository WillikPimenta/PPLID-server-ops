"""Runner script executed in a subprocess to start robots tasks."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.config.env import load_local_env, reload_prefixed_env

load_local_env()
reload_prefixed_env("DOCDB_")

try:
    from app.config import LOG_LEVEL_NUM, configure_logging

    configure_logging(level=LOG_LEVEL_NUM)
except Exception:
    logging.basicConfig(level=logging.INFO)

MODE_MODULES = {
    "nivel": "app.bots.nivel_h",
    "monitor": "app.bots.monitor",
    "excel": "app.bots.monitor_excel",
    "production": "app.bots.bot_production",
    "tray_ui": "app.bots.bot_tray_ui",
    "rotina": "app.bots.bot_rotina",
    "confer": "app.bots.bot_confer_monitor",
    "ged": "app.bots.bot_ged",
    "replicacao_auditoria": "app.bots.bot_replicacao_aud",
    "replicacao_auditoria_d1": "app.bots.bot_replicacao_aud_d1",
    "falhas_criticas": "app.bots.bot_falhas_criticas",
    "produtividade_case": "app.bots.bot_produtividade_case",
    "prioridades_nh": "app.bots.bot_prioridades_nh",
}

ACTIVE_MODES = tuple(MODE_MODULES.keys())


def _clear_module_cache() -> None:
    prefixes = (
        "app.bots",
        "bots",
        "robots",
        "nivel_h",
        "monitor",
        "monitor_excel",
        "bot_production",
        "bot_tray_ui",
        "bot_confer_monitor",
        "bot_ged",
    )
    for name in list(sys.modules.keys()):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            del sys.modules[name]


def _import_mode_module(mode: str):
    import importlib

    module_name = MODE_MODULES[mode]
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        logging.error("Falha ao importar %s: %s: %s", module_name, type(exc).__name__, exc)
        logging.exception(exc)
        try:
            print(
                f"STATUS|Erro ao carregar robô: {type(exc).__name__}: {exc}",
                flush=True,
            )
        except Exception:
            pass
        return None


def _build_settings(mode: str) -> dict:
    if mode == "nivel":
        return {"matricula": os.environ.get("NIVEL_USER"), "senha": os.environ.get("NIVEL_PASS")}
    if mode in ("monitor", "excel"):
        return {"matricula": os.environ.get("MONITOR_USER"), "senha": os.environ.get("MONITOR_PASS")}
    if mode == "production":
        settings = {
            "matricula": os.environ.get("NIVEL_USER"),
            "senha": os.environ.get("NIVEL_PASS"),
        }
        raw_espera = (os.environ.get("PRODUCTION_TEMPO_ESPERA_MINUTOS") or "").strip()
        if raw_espera:
            try:
                settings["tempo_espera_minutos"] = int(raw_espera)
            except ValueError:
                logging.warning("PRODUCTION_TEMPO_ESPERA_MINUTOS inválido; usando padrão do bot")
        raw_dias = (os.environ.get("PRODUCTION_DIAS_DOWNLOAD_BRFLOW") or "").strip()
        if raw_dias:
            try:
                settings["dias_download_brflow"] = int(raw_dias)
            except ValueError:
                logging.warning("PRODUCTION_DIAS_DOWNLOAD_BRFLOW inválido; usando padrão do bot")
        settings["executar_produtividade_case"] = (
            os.environ.get("PRODUCTION_EXECUTAR_PRODUTIVIDADE_CASE", "").strip()
            in ("1", "true", "True", "yes")
        )
        for cfg_key, env_key in (
            ("executar_confer", "PRODUCTION_EXECUTAR_CONFER"),
            ("executar_brflow", "PRODUCTION_EXECUTAR_BRFLOW"),
            ("baixar_monitor_com_producao", "PRODUCTION_BAIXAR_MONITOR"),
            ("baixar_log_eventos_com_producao", "PRODUCTION_BAIXAR_LOG_EVENTOS"),
            ("executar_ged_irregularidade", "PRODUCTION_EXECUTAR_GED_IRREGULARIDADE"),
        ):
            raw_flag = os.environ.get(env_key, "").strip().lower()
            if raw_flag in ("1", "true", "yes", "on"):
                settings[cfg_key] = True
            elif raw_flag in ("0", "false", "no", "off"):
                settings[cfg_key] = False
        raw_case = (os.environ.get("PRODUTIVIDADE_CASE_SETTINGS") or "").strip()
        if raw_case:
            try:
                parsed = json.loads(raw_case)
                if isinstance(parsed, dict):
                    settings["produtividade_case_settings"] = parsed
            except json.JSONDecodeError:
                logging.warning("PRODUTIVIDADE_CASE_SETTINGS inválido no start production; ignorando")
        return settings
    if mode == "rotina":
        settings = {
            "matricula": os.environ.get("NIVEL_USER"),
            "senha": os.environ.get("NIVEL_PASS"),
            "executar_imediatamente": os.environ.get("ROTINA_EXECUTAR_IMEDIATAMENTE") == "1",
            "rotina_data_inicio": os.environ.get("ROTINA_DATA_INICIO", ""),
            "rotina_data_fim": os.environ.get("ROTINA_DATA_FIM", ""),
        }
        if "ROBOT_TAREFAS" in os.environ:
            raw_tarefas = os.environ.get("ROBOT_TAREFAS", "")
            settings["tarefas"] = [t for t in raw_tarefas.split(",") if t.strip()] if raw_tarefas else []
        return settings
    if mode == "confer":
        return {"matricula": os.environ.get("NIVEL_USER"), "senha": os.environ.get("NIVEL_PASS")}
    if mode == "ged":
        return {"matricula": os.environ.get("NIVEL_USER"), "senha": os.environ.get("NIVEL_PASS")}
    if mode == "replicacao_auditoria":
        replicacao_extra = {}
        raw_settings = os.environ.get("REPLICACAO_AUD_SETTINGS", "").strip()
        if raw_settings:
            try:
                parsed = json.loads(raw_settings)
                if isinstance(parsed, dict):
                    replicacao_extra = parsed
            except json.JSONDecodeError:
                logging.warning("REPLICACAO_AUD_SETTINGS inválido; ignorando JSON")
        if os.environ.get("REPLICACAO_APENAS_PLANEJAMENTO", "").strip() in ("1", "true", "True", "yes"):
            replicacao_extra["apenas_planejamento"] = True
        run_id_env = os.environ.get("REPLICACAO_RUN_ID", "").strip()
        if run_id_env:
            replicacao_extra["run_id"] = run_id_env
        headless_env = os.environ.get("ROBOT_HEADLESS", "").strip()
        if headless_env in ("1", "true", "True", "yes"):
            replicacao_extra["headless"] = True
        elif headless_env in ("0", "false", "False", "no"):
            replicacao_extra["headless"] = False
        return {
            "matricula": os.environ.get("NIVEL_USER"),
            "senha": os.environ.get("NIVEL_PASS"),
            **replicacao_extra,
        }
    if mode == "replicacao_auditoria_d1":
        replicacao_extra = {}
        raw_settings = os.environ.get("REPLICACAO_AUD_D1_SETTINGS", "").strip()
        if raw_settings:
            try:
                parsed = json.loads(raw_settings)
                if isinstance(parsed, dict):
                    replicacao_extra = parsed
            except json.JSONDecodeError:
                logging.warning("REPLICACAO_AUD_D1_SETTINGS inválido; ignorando JSON")
        if os.environ.get("REPLICACAO_D1_APENAS_PLANEJAMENTO", "").strip() in ("1", "true", "True", "yes"):
            replicacao_extra["apenas_planejamento"] = True
        run_id_env = os.environ.get("REPLICACAO_D1_RUN_ID", "").strip()
        if run_id_env:
            replicacao_extra["run_id"] = run_id_env
        headless_env = os.environ.get("ROBOT_HEADLESS", "").strip()
        if headless_env in ("1", "true", "True", "yes"):
            replicacao_extra["headless"] = True
        elif headless_env in ("0", "false", "False", "no"):
            replicacao_extra["headless"] = False
        try:
            from app.bots.replicacao_d1_db_bridge import try_inject_execution_snapshot

            replicacao_extra = try_inject_execution_snapshot(replicacao_extra)
        except Exception:
            logging.debug("Snapshot D-1 indisponível no subprocesso", exc_info=True)
        return {
            "matricula": os.environ.get("NIVEL_USER"),
            "senha": os.environ.get("NIVEL_PASS"),
            **replicacao_extra,
        }
    if mode == "falhas_criticas":
        settings: dict = {
            "executar_imediatamente": os.environ.get("FALHAS_CRITICAS_EXECUTAR_IMEDIATAMENTE") == "1",
        }
        raw_settings = os.environ.get("FALHAS_CRITICAS_SETTINGS", "").strip()
        if raw_settings:
            try:
                parsed = json.loads(raw_settings)
                if isinstance(parsed, dict):
                    settings.update(parsed)
            except json.JSONDecodeError:
                logging.warning("FALHAS_CRITICAS_SETTINGS inválido; ignorando JSON")
        headless_env = os.environ.get("ROBOT_HEADLESS", "").strip()
        if headless_env in ("1", "true", "True", "yes"):
            settings["headless"] = True
        elif headless_env in ("0", "false", "False", "no"):
            settings["headless"] = False
        excel_env = os.environ.get("FALHAS_CRITICAS_EXCEL_PATH", "").strip()
        if excel_env:
            settings["falhas_excel_path"] = excel_env
        settings["executar_imediatamente"] = (
            os.environ.get("FALHAS_CRITICAS_EXECUTAR_IMEDIATAMENTE") == "1"
        )
        return settings
    if mode == "prioridades_nh":
        settings = {
            "executar_imediatamente": os.environ.get("PRIORIDADES_NH_EXECUTAR_IMEDIATAMENTE") == "1",
        }
        raw_settings = os.environ.get("PRIORIDADES_NH_SETTINGS", "").strip()
        if raw_settings:
            try:
                parsed = json.loads(raw_settings)
                if isinstance(parsed, dict):
                    settings.update(parsed)
            except json.JSONDecodeError:
                logging.warning("PRIORIDADES_NH_SETTINGS inválido; ignorando JSON")
        nh_list = os.environ.get("PRIORIDADES_NH_LIST_PATH", "").strip()
        if nh_list:
            settings["nh_list_path"] = nh_list
        headless_env = os.environ.get("ROBOT_HEADLESS", "").strip()
        if headless_env in ("1", "true", "True", "yes"):
            settings["headless"] = True
        elif headless_env in ("0", "false", "False", "no"):
            settings["headless"] = False
        settings["matricula"] = os.environ.get("NIVEL_USER")
        settings["senha"] = os.environ.get("NIVEL_PASS")
        settings["executar_imediatamente"] = (
            os.environ.get("PRIORIDADES_NH_EXECUTAR_IMEDIATAMENTE") == "1"
        )
        return settings
    if mode == "produtividade_case":
        settings = {}
        raw_settings = os.environ.get("PRODUTIVIDADE_CASE_SETTINGS", "").strip()
        if raw_settings:
            try:
                parsed = json.loads(raw_settings)
                if isinstance(parsed, dict):
                    settings.update(parsed)
            except json.JSONDecodeError:
                logging.warning("PRODUTIVIDADE_CASE_SETTINGS inválido; ignorando JSON")
        if "ROBOT_TAREFAS" in os.environ and "tarefas" not in settings:
            raw_tarefas = os.environ.get("ROBOT_TAREFAS", "")
            settings["tarefas"] = [t for t in raw_tarefas.split(",") if t.strip()] if raw_tarefas else []
        return settings
    return {}


def _resolve_start_stop(mod):
    start_fn = getattr(mod, "start", None)
    stop_fn = getattr(mod, "stop", None)
    return start_fn, stop_fn


def _raise_thread_error(thread) -> None:
    error = getattr(thread, "execution_error", None) if thread is not None else None
    if error is not None:
        raise RuntimeError(f"Execucao do robo falhou: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=ACTIVE_MODES, required=True)
    args = parser.parse_args()
    mode = args.mode

    _clear_module_cache()
    mod = _import_mode_module(mode)
    if mod is None:
        logging.error("Nenhum módulo de robô disponível para mode=%s", mode)
        try:
            print(f"STATUS|Robô indisponível para mode={mode}. Verifique dependências (ex.: playwright).", flush=True)
        except Exception:
            pass
        sys.exit(2)

    settings = _build_settings(mode)
    start_fn, stop_fn = _resolve_start_stop(mod)
    if not callable(start_fn):
        logging.error("Função de start não encontrada no módulo selecionado")
        sys.exit(3)

    stop_requested = False

    def _on_signal(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        try:
            if callable(stop_fn):
                stop_fn()
        except Exception:
            logging.exception("Erro ao chamar stop_fn")

    signal.signal(signal.SIGINT, _on_signal)
    try:
        signal.signal(signal.SIGTERM, _on_signal)
    except Exception:
        pass

    def _print_status(msg):
        try:
            print(f"STATUS|{msg}", flush=True)
        except Exception:
            pass

    def _print_progress(pct, msg=""):
        try:
            print(f"PROGRESS|{int(pct)}|{msg}", flush=True)
        except Exception:
            pass

    try:
        if hasattr(mod, "set_status_callback"):
            try:
                mod.set_status_callback(_print_status)
            except Exception:
                pass
        if hasattr(mod, "set_progress_callback"):
            try:
                mod.set_progress_callback(_print_progress)
            except Exception:
                pass

        thread = None
        try:
            thread = start_fn(settings=settings)
        except TypeError:
            thread = start_fn()

        parar_event = getattr(mod, "parar_event", None)
        if parar_event is not None:
            while (
                not parar_event.is_set()
                and not stop_requested
                and (thread is None or not hasattr(thread, "is_alive") or thread.is_alive())
            ):
                try:
                    time.sleep(0.5)
                except Exception:
                    break
        elif thread is not None and hasattr(thread, "join"):
            try:
                thread.join()
            except Exception:
                pass
        else:
            while not stop_requested:
                try:
                    time.sleep(0.5)
                except Exception:
                    break

        if thread is not None and hasattr(thread, "join"):
            thread.join()
        _raise_thread_error(thread)

        sys.exit(0)
    except Exception:
        logging.exception("Erro no runner")
        try:
            if callable(stop_fn):
                stop_fn()
        except Exception:
            pass
        sys.exit(4)


if __name__ == "__main__":
    main()
