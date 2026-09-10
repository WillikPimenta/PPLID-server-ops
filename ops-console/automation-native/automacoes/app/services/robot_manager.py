import json
import logging
import os
import hashlib
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional


from app.config.constants import MODE_LABELS, ROBOT_MODES
from app.config.paths import PASTA_CONFIG
from app.config.paths import DEFAULT_FALHAS_CRITICAS_EXCEL
from app.config.sharepoint_folders import default_sharepoint_folders, default_sharepoint_url


MODE_SHAREPOINT_LINKS = {
    mode: default_sharepoint_url(mode)
    for mode in ROBOT_MODES
}


ROBOT_CONFIG_DEFAULT = {
    "output_dir": "",
    "headless": False,
    "max_workers": None,
    "executar_imediatamente": False,
    "rotina_data_inicio": "",
    "rotina_data_fim": "",
    "ged_execucao_imediata": "",
    "sharepoint_url": "",
    "sharepoint_folders": [],
    "tarefas": [],
}

FALHAS_CRITICAS_CONFIG_DEFAULT = {
    "falhas_excel_path": str(DEFAULT_FALHAS_CRITICAS_EXCEL),
    "falhas_criticas_hora": 8,
    "falhas_criticas_minuto": 0,
    "mes_referencia": "",
    "gerar_consolidado": True,
    "gerar_executivo": False,
    "preview_email": True,
    "email_from": "",
    "falhas_refresh_queries": True,
    "modo_segundo_plano": False,
    "salvar_html_individuais": False,
}

PRODUTIVIDADE_CASE_TAREFAS_DEFAULT = [
    "prod_hora",
    "tempo_logado_dia",
    "tempo_logado_mes",
    "consolidado",
    "fechamento_mes_anterior",
    "fila_aberta",
]

PRODUTIVIDADE_CASE_CONFIG_DEFAULT = {
    "tarefas": list(PRODUTIVIDADE_CASE_TAREFAS_DEFAULT),
    "dir_hora": "",
    "dir_tempo_logado": "",
    "dir_consolidado": "",
    "dir_temp": "",
}

# Produção (H/H): espera entre ciclos (minutos). Mínimo 5.
PRODUCTION_TEMPO_ESPERA_MIN_MINUTOS = 5
PRODUCTION_TEMPO_ESPERA_MAX_MINUTOS = 180
PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MIN = 1
PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MAX = 31
PRODUCTION_CONFIG_DEFAULT = {
    "tempo_espera_minutos": 60,
    "dias_download_brflow": 3,
    "executar_confer": True,
    "executar_brflow": True,
    "baixar_monitor_com_producao": True,
    "baixar_log_eventos_com_producao": True,
    "executar_ged_irregularidade": True,
    "executar_produtividade_case": False,
}

CONTROLE_SLA_CONFIG_DEFAULT = {
    "poll_seconds": 60,
    "gap_seconds": 3600,
    "headless": False,
    "sla_alerta_pct": 80,
    "sla_medio_pct": 90,
    "sla_alto_pct": 95,
    "sla_critico_pct": 100,
    # Protocolos atuais = data mais antiga entre hoje e D-N (inclusive).
    "protocolos_dias": 5,
}

PRIORIDADES_NH_CONFIG_DEFAULT = {
    "prioridades_nh_hora": 5,
    "prioridades_nh_minuto": 0,
    "executar_imediatamente": False,
    "headless": False,
}

log = logging.getLogger(__name__)

OKTA_VALIDATE_TIMEOUT_SECONDS = 180
OKTA_VALIDATE_PID_NAME = "okta_validate.pid"
OKTA_VALIDATE_SELENIUM_PIDS_NAME = "okta_validate_selenium.pids.json"
OKTA_VALIDATE_PIDS_ENV = "OKTA_VALIDATE_PIDS_FILE"


def _okta_env_tag() -> str:
    """Sufixo por ambiente para PIDs Okta (MAIN/DEV/HOM no mesmo PC)."""
    raw = (
        os.getenv("PPLID_ENV_PROFILE")
        or os.getenv("PPLID_ENVIRONMENT")
        or os.getenv("SESSION_COOKIE_NAME")
        or "local"
    ).strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if cleaned.endswith("_sessionid"):
        cleaned = cleaned[: -len("_sessionid")]
    if cleaned.startswith("pplid_"):
        cleaned = cleaned[len("pplid_") :]
    return (cleaned or "local")[:32]


PRODUCTION_DETALHADO_SAVED_PREFIX = "PRODUCTION_DETALHADO_SAVED|"
PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX = "PRODUCTION_GED_IRREGULARIDADE_SAVED|"
FALHAS_OUTPUT_PREFIX = "FALHAS_OUTPUT|"
ROTINA_BRUTO_SAVED_PREFIX = "ROTINA_BRUTO_SAVED|"
MONITOR_EVENTOS_SAVED_PREFIX = "MONITOR_EVENTOS_SAVED|"
REPLICACAO_D1_SAVED_PREFIX = "REPLICACAO_D1_SAVED|"
PRODUTIVIDADE_CASE_SAVED_PREFIX = "PRODUTIVIDADE_CASE_SAVED|"
CASE_FILA_SAVED_PREFIX = "CASE_FILA_SAVED|"
REPLICACAO_D1_REPLICADOS_SAVED_PREFIX = "REPLICACAO_D1_REPLICADOS_SAVED|"
PRIORIDADES_NH_SAVED_PREFIX = "PRIORIDADES_NH_SAVED|"
PLAN_EVENT_PREFIX = "PLAN_EVENT|"
PLAN_EVENT_VERSION = 1
EXECUTION_RUNS_MAX = 20
EXECUTION_RUNS_MAX_API = 30
CYCLE_RUNS_DEDUP_SECONDS = 3
# Prefixo gravado em disco por `_append_log`: "[YYYY-MM-DD HH:MM:SS] mensagem"
_LOG_FILE_TS_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s?")
# Evita re-notificar o mesmo detalhado via sync_drop + stdout no mesmo processo.
_RECENT_PROD_SYNC_PATHS: dict[str, float] = {}
_RECENT_PROD_SYNC_TTL_S = 120.0

REPLICACAO_AUD_CONFIG_DEFAULT = {
    "apenas_planejamento": False,
    "run_id": "",
    "gerar_novo_plano": False,
    "forcar_reexecucao": False,
    "apenas_pendentes": True,
    "excluir_historico": True,
    "dias_historico": 30,
    "sobrescrever": False,
    "fallback_ultimo_parquet": False,
    "replicacao_aud_seed": 42,
    "replicacao_aud_data_ref": "",
    "usar_escala_auditores": True,
    "auditores_ativos": "",
    "meta_produ": "300",
    "meta_produ_case": "300",
    "escala_auditores_csv": "",
    "replicacao_config_base": "",
    "replicacao_config_default": "",
    "replicacao_categoria_xlsx": "",
    "replicacao_volumetria_raiz": "",
    "replicacao_volumetria_pasta": "",
    "replicacao_sincronizar_workflow_d1": True,
    "replicacao_apenas_ativos": True,
    "replicacao_cliente_destino": "GAQ",
    "replicacao_cliente_cod": "751",
    "replicacao_workflow_destino": "G Auditoria - G Auditoria",
    "replicacao_workflow_cod": "17047",
    "replicacao_workflow_destino_31": "Documentoscopia 3.1 - Documentoscopia 3.1",
    "replicacao_workflow_cod_31": "17426",
    "replicacao_workflow_destino_bio": "Auditoria Biometria - Auditoria Biometria",
    "replicacao_workflow_cod_bio": "",
    "replicacao_workflow_destino_redoc": "Auditoria Redoc - Auditoria Redoc",
    "replicacao_workflow_cod_redoc": "",
    "replicacao_workflows_amostra_100": [],
    "replicacao_workflows_amostra_pct": {},
}

REPLICACAO_AUD_D1_CONFIG_DEFAULT = {
    "apenas_planejamento": False,
    "run_id": "",
    "gerar_novo_plano": False,
    "forcar_reexecucao": False,
    "apenas_pendentes": True,
    "excluir_historico": True,
    "dias_historico": 30,
    "sobrescrever": False,
    "fallback_ultimo_parquet": True,
    "replicacao_aud_seed": 42,
    "replicacao_aud_data_ref": "",
    "usar_escala_auditores": True,
    "auditores_ativos": "",
    "meta_produ": "300",
    "meta_produ_case": "300",
    "escala_auditores_csv": "",
    "replicacao_config_base": "",
    "replicacao_config_default": "",
    "replicacao_categoria_xlsx": "",
    "replicacao_sincronizar_workflow_d1": True,
    "replicacao_apenas_ativos": True,
    "replicacao_cliente_destino": "GAQ",
    "replicacao_cliente_cod": "751",
    "replicacao_workflow_destino": "G Auditoria - G Auditoria",
    "replicacao_workflow_cod": "17047",
    "replicacao_workflow_destino_31": "Documentoscopia 3.1 - Documentoscopia 3.1",
    "replicacao_workflow_cod_31": "17426",
    "replicacao_workflow_destino_bio": "Auditoria Biometria - Auditoria Biometria",
    "replicacao_workflow_cod_bio": "",
    "replicacao_workflow_destino_redoc": "Auditoria Redoc - Auditoria Redoc",
    "replicacao_workflow_cod_redoc": "",
    "replicacao_workflows_amostra_100": [],
    "replicacao_workflows_amostra_pct": {},
    "usar_amostra_mix_manual_automatico": False,
    "amostra_pct_manual": 70,
    "amostra_pct_automatico": 30,
    "limpar_planos_automatico": False,
    "manter_planos_ultimos_n": 5,
    "dias_retencao_planos": 0,
    "limpar_planos_ao_gerar": False,
    "limpar_planos_apos_conclusao": False,
    "agendamento_ativo": False,
    "agendamento_hora_planejamento": "07:00",
    "agendamento_hora_execucao": "12:00",
}

TRAY_UI_CONFIG_DEFAULT = {
    "tray_scan_region": "55,92,45,8",
    "tray_icon_target_height": 48,
    "tray_bg_tolerance": 25,
    "tray_match_scales": "",
    "tray_ui_check_interval_seconds": 60,
    "onedrive_ui_check_interval_seconds": 0,
    "cisco_ui_check_interval_seconds": 0,
    "onedrive_ui_post_recover_wait_seconds": 5,
    "cisco_ui_post_recover_wait_seconds": 5,
    "onedrive_ui_sync_stuck_poll_seconds": 5,
    "onedrive_ui_sync_stuck_window_seconds": 180,
    "onedrive_ui_sync_restart_cooldown_seconds": 1800,
    "onedrive_ui_sync_restart_wait_seconds": 15,
    "tray_ui_recovery_failure_limit": 3,
    "tray_ui_recovery_backoff_seconds": 14400,
    "cisco_ui_res_dir": "",
}


class RobotProcessManager:
    def __init__(self):
        self.project_root = Path(__file__).resolve().parents[2]
        self.bundled_runner_file = self.project_root / "app" / "orchestration" / "robot_runner.py"
        env_external = os.getenv("SERASA_ROBOS_SRC", "").strip()
        self.external_robots_dir = Path(env_external) if env_external else None
        self.runner_cwd, self.runner_file, self.runner_module = self._resolve_runner_path()
        self.okta_validate_script = self._resolve_okta_validate_script()
        self.suporte_claro_jira_script = self._resolve_suporte_claro_jira_script()
        self.python_bin = Path(os.getenv("PYTHON_BIN", sys.executable))
        appdata_dir = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        appdata_base_dir = appdata_dir / "PLAN_IDF_SERASA_BOTS"
        default_logs_dir = appdata_base_dir / "logs"
        self.logs_dir = Path(os.getenv("ROBOT_LOGS_DIR", str(default_logs_dir)))
        self.config_dir = Path(os.getenv("ROBOT_CONFIG_DIR", str(PASTA_CONFIG)))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.pids_dir = self.config_dir / "pids"
        self.pids_dir.mkdir(parents=True, exist_ok=True)

        self.execution_history_file = self.config_dir / "execution_history.json"
        self.cycle_runs_file = self.config_dir / "cycle_runs.json"
        self.robot_config_file = self.config_dir / "robot_config.json"
        self._lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, deque[str]] = {mode: deque(maxlen=400) for mode in ROBOT_MODES}
        self._log_files: dict[str, Path] = {mode: self.logs_dir / f"{mode}.log" for mode in ROBOT_MODES}
        self._pid_files: dict[str, Path] = {mode: self.pids_dir / f"{mode}.pid" for mode in ROBOT_MODES}
        self._execution: dict[str, dict] = {
            mode: {
                "started_at": None,
                "ended_at": None,
                "duration_seconds": None,
                "result": None,
                "output_paths": None,
            }
            for mode in ROBOT_MODES
        }
        self._cycle_runs: dict[str, list[dict]] = {mode: [] for mode in ROBOT_MODES}
        self._cycle_state: dict[str, dict] = {
            mode: {"started_at": None, "last_progress": 0, "last_cycle_recorded_at": None}
            for mode in ROBOT_MODES
        }
        self._runtime_ui: dict[str, dict] = {
            mode: {
                "progress": 0,
                "status": "Aguardando início",
                "updated_at": None,
                "cycle_started_at": None,
                "modo_segundo_plano": False,
            }
            for mode in ROBOT_MODES
        }
        self._stop_requested: dict[str, bool] = {mode: False for mode in ROBOT_MODES}
        self._production_saved_callbacks: list[Callable[[str], None]] = []
        self._production_ged_irregularidade_saved_callbacks: list[Callable[[str], None]] = []
        self._rotina_bruto_saved_callbacks: list[Callable[[str, str], None]] = []
        self._monitor_eventos_saved_callbacks: list[Callable[[str], None]] = []
        self._replicacao_d1_saved_callbacks: list[Callable[[str, str], None]] = []
        self._replicacao_d1_replicados_saved_callbacks: list[Callable[[str], None]] = []
        self._produtividade_case_saved_callbacks: list[Callable[[str, str], None]] = []
        self._prioridades_nh_saved_callbacks: list[Callable[[str], None]] = []
        self._credentials_validation = {
            "validated": False,
            "message": "Credenciais não verificadas",
            "last_checked_at": None,
            "checking": False,
            "fingerprint": None,
        }
        # Estado Okta por aba/navegador (client_id). O processo Selenium continua único na máquina.
        self._okta_sessions: dict[str, dict] = {}
        self._okta_active_session: str | None = None
        self._okta_validate_proc: subprocess.Popen | None = None
        self._okta_validate_cancel_requested = False
        self._okta_validate_thread: threading.Thread | None = None
        self._suporte_claro_jira_proc: subprocess.Popen | None = None
        self._suporte_claro_jira_thread: threading.Thread | None = None
        self._suporte_claro_jira_cancel = False
        self._suporte_claro_jira_status: dict = {
            "running": False,
            "message": "Aguardando",
            "step": None,
            "step_label": None,
            "detail": None,
            "result": None,
            "started_at": None,
            "ended_at": None,
        }
        self._robot_configs: dict[str, dict] = {
            mode: self._default_robot_config(mode) for mode in ROBOT_MODES
        }
        self._load_execution_history()
        self._load_cycle_runs()
        self._load_robot_configs()
        # Reentrada / outro worker: PID e histórico vêm do disco, mas logs/progresso
        # viviam só em memória — rehidratar e reconciliar estado órfão.
        self._hydrate_runtime_from_disk()

    @staticmethod
    def _default_robot_config(mode: str) -> dict:
        cfg = dict(ROBOT_CONFIG_DEFAULT)
        cfg["sharepoint_folders"] = default_sharepoint_folders(mode)
        cfg["sharepoint_url"] = default_sharepoint_url(mode)
        if mode == "replicacao_auditoria":
            cfg.update(REPLICACAO_AUD_CONFIG_DEFAULT)
        if mode == "replicacao_auditoria_d1":
            cfg.update(REPLICACAO_AUD_D1_CONFIG_DEFAULT)
        if mode == "tray_ui":
            cfg.update(TRAY_UI_CONFIG_DEFAULT)
        if mode == "falhas_criticas":
            cfg.update(FALHAS_CRITICAS_CONFIG_DEFAULT)
        if mode == "produtividade_case":
            cfg.update(PRODUTIVIDADE_CASE_CONFIG_DEFAULT)
        if mode == "production":
            cfg.update(PRODUCTION_CONFIG_DEFAULT)
        if mode == "controle_sla":
            cfg.update(CONTROLE_SLA_CONFIG_DEFAULT)
        if mode == "prioridades_nh":
            cfg.update(PRIORIDADES_NH_CONFIG_DEFAULT)
        return cfg

    @staticmethod
    def _parse_bool(value, default: bool = False) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if value is None:
            return default
        return bool(value)

    @staticmethod
    def _parse_workflows_amostra_100(raw_value) -> list[str]:
        nomes: list[str] = []
        if isinstance(raw_value, list):
            nomes = [str(item).strip() for item in raw_value if str(item).strip()]
        elif isinstance(raw_value, str):
            for parte in raw_value.replace("\r", "\n").replace(",", "\n").replace(";", "\n").split("\n"):
                nome = parte.strip()
                if nome:
                    nomes.append(nome)
        seen: set[str] = set()
        out: list[str] = []
        for nome in nomes:
            if nome in seen:
                continue
            seen.add(nome)
            out.append(nome)
        return out

    @staticmethod
    def _parse_workflows_amostra_pct(raw_value) -> dict[str, int]:
        out: dict[str, int] = {}
        if not isinstance(raw_value, dict):
            return out
        for wf, pct in raw_value.items():
            nome = str(wf).strip()
            if not nome:
                continue
            try:
                valor = int(pct)
            except (TypeError, ValueError):
                continue
            out[nome] = max(1, min(100, valor))
        return out

    @staticmethod
    def _normalize_replicacao_aud_config(
        raw: dict,
        cfg: dict,
        defaults: Optional[dict] = None,
    ) -> None:
        defaults = defaults or REPLICACAO_AUD_CONFIG_DEFAULT
        cfg["apenas_planejamento"] = RobotProcessManager._parse_bool(
            raw.get("apenas_planejamento"), defaults["apenas_planejamento"]
        )
        cfg["run_id"] = str(raw.get("run_id", "")).strip()
        cfg["gerar_novo_plano"] = RobotProcessManager._parse_bool(
            raw.get("gerar_novo_plano"), defaults["gerar_novo_plano"]
        )
        cfg["forcar_reexecucao"] = RobotProcessManager._parse_bool(
            raw.get("forcar_reexecucao"), defaults["forcar_reexecucao"]
        )
        apenas_pendentes_raw = raw.get("apenas_pendentes", defaults["apenas_pendentes"])
        if isinstance(apenas_pendentes_raw, str) and apenas_pendentes_raw.strip() == "":
            cfg["apenas_pendentes"] = defaults["apenas_pendentes"]
        else:
            cfg["apenas_pendentes"] = RobotProcessManager._parse_bool(
                apenas_pendentes_raw, defaults["apenas_pendentes"]
            )
        excluir_raw = raw.get("excluir_historico", defaults["excluir_historico"])
        if isinstance(excluir_raw, str) and excluir_raw.strip() == "":
            cfg["excluir_historico"] = defaults["excluir_historico"]
        else:
            cfg["excluir_historico"] = RobotProcessManager._parse_bool(
                excluir_raw, defaults["excluir_historico"]
            )
        try:
            dias = int(raw.get("dias_historico", defaults["dias_historico"]))
            cfg["dias_historico"] = max(1, min(dias, 365))
        except (TypeError, ValueError):
            cfg["dias_historico"] = defaults["dias_historico"]
        cfg["sobrescrever"] = RobotProcessManager._parse_bool(
            raw.get("sobrescrever"), defaults["sobrescrever"]
        )
        cfg["fallback_ultimo_parquet"] = RobotProcessManager._parse_bool(
            raw.get("fallback_ultimo_parquet"), defaults["fallback_ultimo_parquet"]
        )
        try:
            cfg["replicacao_aud_seed"] = int(
                raw.get("replicacao_aud_seed", defaults["replicacao_aud_seed"])
            )
        except (TypeError, ValueError):
            cfg["replicacao_aud_seed"] = defaults["replicacao_aud_seed"]
        data_ref = str(raw.get("replicacao_aud_data_ref", "")).strip()
        if data_ref and (len(data_ref) != 8 or not data_ref.isdigit()):
            data_ref = ""
        cfg["replicacao_aud_data_ref"] = data_ref
        cfg["usar_escala_auditores"] = RobotProcessManager._parse_bool(
            raw.get("usar_escala_auditores"), defaults["usar_escala_auditores"]
        )
        auditores_raw = raw.get("auditores_ativos", "")
        if auditores_raw not in (None, ""):
            if isinstance(auditores_raw, (int, float)):
                cfg["auditores_ativos"] = str(int(auditores_raw))
            else:
                cfg["auditores_ativos"] = str(auditores_raw).strip()
        else:
            cfg["auditores_ativos"] = ""
        cfg["escala_auditores_csv"] = str(raw.get("escala_auditores_csv", "")).strip()
        cfg["replicacao_config_base"] = str(raw.get("replicacao_config_base", "")).strip()
        cfg["replicacao_config_default"] = str(raw.get("replicacao_config_default", "")).strip()
        cfg["replicacao_categoria_xlsx"] = str(raw.get("replicacao_categoria_xlsx", "")).strip()
        cfg["replicacao_volumetria_raiz"] = str(raw.get("replicacao_volumetria_raiz", "")).strip()
        cfg["replicacao_volumetria_pasta"] = str(raw.get("replicacao_volumetria_pasta", "")).strip()
        cfg["replicacao_sincronizar_workflow_d1"] = RobotProcessManager._parse_bool(
            raw.get("replicacao_sincronizar_workflow_d1"),
            defaults["replicacao_sincronizar_workflow_d1"],
        )
        cfg["replicacao_apenas_ativos"] = RobotProcessManager._parse_bool(
            raw.get("replicacao_apenas_ativos"),
            defaults["replicacao_apenas_ativos"],
        )
        meta = str(raw.get("meta_produ", "")).strip()
        cfg["meta_produ"] = meta if meta else defaults["meta_produ"]
        meta_case = str(raw.get("meta_produ_case", "")).strip()
        cfg["meta_produ_case"] = meta_case if meta_case else defaults.get("meta_produ_case", cfg["meta_produ"])
        cfg["replicacao_cliente_destino"] = str(
            raw.get("replicacao_cliente_destino", defaults["replicacao_cliente_destino"])
        ).strip()
        cfg["replicacao_cliente_cod"] = str(
            raw.get("replicacao_cliente_cod", defaults["replicacao_cliente_cod"])
        ).strip()
        cfg["replicacao_workflow_destino"] = str(
            raw.get(
                "replicacao_workflow_destino",
                defaults["replicacao_workflow_destino"],
            )
        ).strip()
        cfg["replicacao_workflow_cod"] = str(
            raw.get("replicacao_workflow_cod", defaults["replicacao_workflow_cod"])
        ).strip()
        cfg["replicacao_workflow_destino_31"] = str(
            raw.get(
                "replicacao_workflow_destino_31",
                defaults.get("replicacao_workflow_destino_31", ""),
            )
        ).strip()
        cfg["replicacao_workflow_cod_31"] = str(
            raw.get("replicacao_workflow_cod_31", defaults.get("replicacao_workflow_cod_31", ""))
        ).strip()
        cfg["replicacao_workflow_destino_bio"] = str(
            raw.get(
                "replicacao_workflow_destino_bio",
                defaults.get("replicacao_workflow_destino_bio", ""),
            )
        ).strip()
        cfg["replicacao_workflow_cod_bio"] = str(
            raw.get("replicacao_workflow_cod_bio", defaults.get("replicacao_workflow_cod_bio", ""))
        ).strip()
        cfg["replicacao_workflow_destino_redoc"] = str(
            raw.get(
                "replicacao_workflow_destino_redoc",
                defaults.get("replicacao_workflow_destino_redoc", ""),
            )
        ).strip()
        cfg["replicacao_workflow_cod_redoc"] = str(
            raw.get("replicacao_workflow_cod_redoc", defaults.get("replicacao_workflow_cod_redoc", ""))
        ).strip()
        cfg["replicacao_workflows_amostra_100"] = RobotProcessManager._parse_workflows_amostra_100(
            raw.get(
                "replicacao_workflows_amostra_100",
                defaults.get("replicacao_workflows_amostra_100", []),
            )
        )
        cfg["replicacao_workflows_amostra_pct"] = RobotProcessManager._parse_workflows_amostra_pct(
            raw.get(
                "replicacao_workflows_amostra_pct",
                defaults.get("replicacao_workflows_amostra_pct", {}),
            )
        )
        cfg["usar_amostra_mix_manual_automatico"] = RobotProcessManager._parse_bool(
            raw.get("usar_amostra_mix_manual_automatico"),
            defaults.get("usar_amostra_mix_manual_automatico", False),
        )
        try:
            pct_m = int(raw.get("amostra_pct_manual", defaults.get("amostra_pct_manual", 70)))
            cfg["amostra_pct_manual"] = max(0, min(100, pct_m))
        except (TypeError, ValueError):
            cfg["amostra_pct_manual"] = defaults.get("amostra_pct_manual", 70)
        try:
            pct_a = int(raw.get("amostra_pct_automatico", defaults.get("amostra_pct_automatico", 30)))
            cfg["amostra_pct_automatico"] = max(0, min(100, pct_a))
        except (TypeError, ValueError):
            cfg["amostra_pct_automatico"] = defaults.get("amostra_pct_automatico", 30)
        cfg["limpar_planos_automatico"] = RobotProcessManager._parse_bool(
            raw.get("limpar_planos_automatico"), defaults.get("limpar_planos_automatico", False)
        )
        cfg["limpar_planos_ao_gerar"] = RobotProcessManager._parse_bool(
            raw.get("limpar_planos_ao_gerar"), defaults.get("limpar_planos_ao_gerar", False)
        )
        cfg["limpar_planos_apos_conclusao"] = RobotProcessManager._parse_bool(
            raw.get("limpar_planos_apos_conclusao"),
            defaults.get("limpar_planos_apos_conclusao", False),
        )
        try:
            manter_n = int(raw.get("manter_planos_ultimos_n", defaults.get("manter_planos_ultimos_n", 5)))
            cfg["manter_planos_ultimos_n"] = max(1, min(manter_n, 50))
        except (TypeError, ValueError):
            cfg["manter_planos_ultimos_n"] = defaults.get("manter_planos_ultimos_n", 5)
        try:
            dias_ret = int(raw.get("dias_retencao_planos", defaults.get("dias_retencao_planos", 0)))
            cfg["dias_retencao_planos"] = max(0, min(dias_ret, 365))
        except (TypeError, ValueError):
            cfg["dias_retencao_planos"] = defaults.get("dias_retencao_planos", 0)
        if "agendamento_ativo" in defaults:
            from app.bots.replicacao_aud_d1_orchestration import normalizar_hora_config

            cfg["agendamento_ativo"] = RobotProcessManager._parse_bool(
                raw.get("agendamento_ativo"),
                defaults.get("agendamento_ativo", False),
            )
            cfg["agendamento_hora_planejamento"] = normalizar_hora_config(
                str(raw.get("agendamento_hora_planejamento", "")),
                default=str(defaults.get("agendamento_hora_planejamento", "07:00")),
            )
            cfg["agendamento_hora_execucao"] = normalizar_hora_config(
                str(raw.get("agendamento_hora_execucao", "")),
                default=str(defaults.get("agendamento_hora_execucao", "12:00")),
            )

    @staticmethod
    def _parse_tray_scan_region(raw_value, default: str = "55,92,45,8") -> str:
        text = str(raw_value or "").strip()
        if not text:
            return default
        parts = [part.strip() for part in text.split(",")]
        if len(parts) != 4:
            return default
        try:
            left, top, width, height = (float(part) for part in parts)
        except ValueError:
            return default
        if not (0 <= left <= 100 and 0 <= top <= 100 and 0 < width <= 100 and 0 < height <= 100):
            return default
        return ",".join(str(value).rstrip("0").rstrip(".") if "." in str(value) else str(int(value) if value == int(value) else value) for value in (left, top, width, height))

    @staticmethod
    def _parse_bounded_int(raw_value, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(value, maximum))

    @staticmethod
    def _normalize_tray_ui_config(raw: dict, cfg: dict) -> None:
        defaults = TRAY_UI_CONFIG_DEFAULT
        cfg["tray_scan_region"] = RobotProcessManager._parse_tray_scan_region(
            raw.get("tray_scan_region"), defaults["tray_scan_region"]
        )
        cfg["tray_icon_target_height"] = RobotProcessManager._parse_bounded_int(
            raw.get("tray_icon_target_height"), defaults["tray_icon_target_height"], 16, 128
        )
        cfg["tray_bg_tolerance"] = RobotProcessManager._parse_bounded_int(
            raw.get("tray_bg_tolerance"), defaults["tray_bg_tolerance"], 1, 100
        )
        cfg["tray_match_scales"] = str(raw.get("tray_match_scales", defaults["tray_match_scales"])).strip()
        cfg["tray_ui_check_interval_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("tray_ui_check_interval_seconds"), defaults["tray_ui_check_interval_seconds"], 15, 3600
        )
        cfg["onedrive_ui_check_interval_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("onedrive_ui_check_interval_seconds"), defaults["onedrive_ui_check_interval_seconds"], 0, 3600
        )
        cfg["cisco_ui_check_interval_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("cisco_ui_check_interval_seconds"), defaults["cisco_ui_check_interval_seconds"], 0, 3600
        )
        cfg["onedrive_ui_post_recover_wait_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("onedrive_ui_post_recover_wait_seconds"),
            defaults["onedrive_ui_post_recover_wait_seconds"],
            1,
            300,
        )
        cfg["cisco_ui_post_recover_wait_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("cisco_ui_post_recover_wait_seconds"),
            defaults["cisco_ui_post_recover_wait_seconds"],
            1,
            300,
        )
        cfg["onedrive_ui_sync_stuck_poll_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("onedrive_ui_sync_stuck_poll_seconds"),
            defaults["onedrive_ui_sync_stuck_poll_seconds"],
            1,
            60,
        )
        cfg["onedrive_ui_sync_stuck_window_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("onedrive_ui_sync_stuck_window_seconds"),
            defaults["onedrive_ui_sync_stuck_window_seconds"],
            30,
            3600,
        )
        cfg["onedrive_ui_sync_restart_cooldown_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("onedrive_ui_sync_restart_cooldown_seconds"),
            defaults["onedrive_ui_sync_restart_cooldown_seconds"],
            60,
            86400,
        )
        cfg["onedrive_ui_sync_restart_wait_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("onedrive_ui_sync_restart_wait_seconds"),
            defaults["onedrive_ui_sync_restart_wait_seconds"],
            5,
            300,
        )
        cfg["tray_ui_recovery_failure_limit"] = RobotProcessManager._parse_bounded_int(
            raw.get("tray_ui_recovery_failure_limit"),
            defaults["tray_ui_recovery_failure_limit"],
            1,
            20,
        )
        cfg["tray_ui_recovery_backoff_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("tray_ui_recovery_backoff_seconds"),
            defaults["tray_ui_recovery_backoff_seconds"],
            60,
            86400,
        )
        cfg["cisco_ui_res_dir"] = str(raw.get("cisco_ui_res_dir", defaults["cisco_ui_res_dir"])).strip()

    @staticmethod
    def _apply_tray_ui_env(env: dict, cfg: dict) -> None:
        env["TRAY_SCAN_REGION"] = str(cfg.get("tray_scan_region", TRAY_UI_CONFIG_DEFAULT["tray_scan_region"]))
        env["TRAY_ICON_TARGET_HEIGHT"] = str(cfg.get("tray_icon_target_height", TRAY_UI_CONFIG_DEFAULT["tray_icon_target_height"]))
        env["TRAY_BG_TOLERANCE"] = str(cfg.get("tray_bg_tolerance", TRAY_UI_CONFIG_DEFAULT["tray_bg_tolerance"]))
        match_scales = str(cfg.get("tray_match_scales", "")).strip()
        if match_scales:
            env["TRAY_MATCH_SCALES"] = match_scales
        else:
            env.pop("TRAY_MATCH_SCALES", None)
        env["TRAY_UI_CHECK_INTERVAL_SECONDS"] = str(
            cfg.get("tray_ui_check_interval_seconds", TRAY_UI_CONFIG_DEFAULT["tray_ui_check_interval_seconds"])
        )
        for key, env_name in (
            ("onedrive_ui_check_interval_seconds", "ONEDRIVE_UI_CHECK_INTERVAL_SECONDS"),
            ("cisco_ui_check_interval_seconds", "CISCO_UI_CHECK_INTERVAL_SECONDS"),
        ):
            value = int(cfg.get(key, 0) or 0)
            if value > 0:
                env[env_name] = str(value)
            else:
                env.pop(env_name, None)
        env["ONEDRIVE_UI_POST_RECOVER_WAIT_SECONDS"] = str(
            cfg.get("onedrive_ui_post_recover_wait_seconds", TRAY_UI_CONFIG_DEFAULT["onedrive_ui_post_recover_wait_seconds"])
        )
        env["CISCO_UI_POST_RECOVER_WAIT_SECONDS"] = str(
            cfg.get("cisco_ui_post_recover_wait_seconds", TRAY_UI_CONFIG_DEFAULT["cisco_ui_post_recover_wait_seconds"])
        )
        env["ONEDRIVE_UI_SYNC_STUCK_POLL_SECONDS"] = str(
            cfg.get("onedrive_ui_sync_stuck_poll_seconds", TRAY_UI_CONFIG_DEFAULT["onedrive_ui_sync_stuck_poll_seconds"])
        )
        env["ONEDRIVE_UI_SYNC_STUCK_WINDOW_SECONDS"] = str(
            cfg.get("onedrive_ui_sync_stuck_window_seconds", TRAY_UI_CONFIG_DEFAULT["onedrive_ui_sync_stuck_window_seconds"])
        )
        env["ONEDRIVE_UI_SYNC_RESTART_COOLDOWN_SECONDS"] = str(
            cfg.get("onedrive_ui_sync_restart_cooldown_seconds", TRAY_UI_CONFIG_DEFAULT["onedrive_ui_sync_restart_cooldown_seconds"])
        )
        env["ONEDRIVE_UI_SYNC_RESTART_WAIT_SECONDS"] = str(
            cfg.get("onedrive_ui_sync_restart_wait_seconds", TRAY_UI_CONFIG_DEFAULT["onedrive_ui_sync_restart_wait_seconds"])
        )
        env["TRAY_UI_RECOVERY_FAILURE_LIMIT"] = str(
            cfg.get("tray_ui_recovery_failure_limit", TRAY_UI_CONFIG_DEFAULT["tray_ui_recovery_failure_limit"])
        )
        env["TRAY_UI_RECOVERY_BACKOFF_SECONDS"] = str(
            cfg.get("tray_ui_recovery_backoff_seconds", TRAY_UI_CONFIG_DEFAULT["tray_ui_recovery_backoff_seconds"])
        )
        cisco_res_dir = str(cfg.get("cisco_ui_res_dir", "")).strip()
        if cisco_res_dir:
            env["CISCO_UI_RES_DIR"] = cisco_res_dir
        else:
            env.pop("CISCO_UI_RES_DIR", None)

    @staticmethod
    def limpar_planos_replicacao_d1(
        robot_config: dict | None = None,
        *,
        run_ids: list | None = None,
        forcar: bool = False,
        remover_ledger: bool = False,
    ) -> tuple[bool, str, dict]:
        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots.replicacao_aud_d1_planning import (
                _ensure_d1_settings,
                apagar_plano_run_d1,
                aplicar_politica_retencao_planos_d1,
            )

            merged = RobotProcessManager._normalize_robot_config(
                robot_config, "replicacao_auditoria_d1"
            )
            replicacao_settings = _ensure_d1_settings(
                {
                    key: merged.get(key)
                    for key in REPLICACAO_AUD_D1_CONFIG_DEFAULT
                    if key in merged
                }
            )
            replicacao_settings["replicacao_config_base"] = merged.get("replicacao_config_base", "")
            if run_ids:
                out = {"removidos": [], "ignorados": [], "erros": [], "ledger_removidos": []}
                for run_id in run_ids:
                    res = apagar_plano_run_d1(
                        str(run_id).strip(),
                        forcar=forcar,
                        remover_ledger=remover_ledger,
                        settings=replicacao_settings,
                    )
                    if res.get("ignorado"):
                        out["ignorados"].append({"run_id": run_id, "motivo": res.get("motivo", "")})
                    elif res.get("removidos"):
                        out["removidos"].append(str(run_id))
                        if res.get("ledger_removido"):
                            out["ledger_removidos"].append(str(run_id))
                    elif res.get("motivo"):
                        out["erros"].append({"run_id": run_id, "erro": res.get("motivo", "")})
                msg = f"{len(out['removidos'])} plano(s) removido(s)"
                if out["ledger_removidos"]:
                    msg += f"; ledger: {len(out['ledger_removidos'])} run(s)"
                return True, msg, out

            out = aplicar_politica_retencao_planos_d1(merged, forcar=forcar, forcar_politica=True)
            msg = f"{len(out.get('removidos', []))} plano(s) removido(s)"
            return True, msg, out
        except Exception as exc:
            return False, str(exc), {"tipo": "erro"}

    @staticmethod
    def obter_meta_mensal_replicacao_d1(
        robot_config: dict | None = None,
        ano_mes: str | None = None,
    ) -> tuple[bool, str, dict]:
        try:
            from datetime import datetime

            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots.meta_cliente_mensal import (
                calcular_projecao_fim_mes,
                carregar_snapshot_mes,
                resolver_ano_mes,
            )

            merged = RobotProcessManager._normalize_robot_config(
                robot_config, "replicacao_auditoria_d1"
            )
            settings = {"replicacao_config_base": merged.get("replicacao_config_base", "")}
            mes = str(ano_mes or "").strip() or resolver_ano_mes(settings, data_exec=datetime.now())
            linhas = carregar_snapshot_mes(mes, settings=settings)
            projecao = calcular_projecao_fim_mes(mes, linhas, settings=settings)
            clientes = projecao.pop("clientes", linhas)
            return True, "", {"ano_mes": mes, "clientes": clientes, "projecao": projecao}
        except Exception as exc:
            return False, str(exc), {}

    @staticmethod
    def recalcular_meta_mensal_replicacao_d1(
        robot_config: dict | None = None,
        ano_mes: str | None = None,
    ) -> tuple[bool, str, dict]:
        try:
            from datetime import datetime
            from pathlib import Path

            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots.meta_cliente_mensal import (
                expandir_metas_cliente_para_workflows,
                recalcular_snapshot_mensal,
                resolver_ano_mes,
            )
            from app.bots.replicacao_aud_d1_planning import (
                carregar_categoria_clientes,
                carregar_mapa_workflow_d1,
            )
            from app.bots import replicacao_aud_planning as rap

            merged = RobotProcessManager._normalize_robot_config(
                robot_config, "replicacao_auditoria_d1"
            )
            settings = {"replicacao_config_base": merged.get("replicacao_config_base", "")}
            mes = str(ano_mes or "").strip() or resolver_ano_mes(settings, data_exec=datetime.now())
            paths = rap._resolver_paths_config(settings)
            categorias = carregar_categoria_clientes(paths["categoria"], settings=settings)
            mapa = carregar_mapa_workflow_d1(paths["default"], settings=settings)
            metas = expandir_metas_cliente_para_workflows(mapa, categorias)
            path = recalcular_snapshot_mensal(mes, metas, settings=settings)
            return True, "Snapshot recalculado", {"ano_mes": mes, "arquivo": str(path)}
        except Exception as exc:
            return False, str(exc), {}

    @staticmethod
    def ajustar_meta_mensal_replicacao_d1(
        robot_config: dict | None = None,
        *,
        ano_mes: str,
        workflow: str = "",
        cliente: str = "",
        consumo_acumulado: int,
    ) -> tuple[bool, str, dict]:
        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots.meta_cliente_mensal import aplicar_ajuste_manual_consumo

            merged = RobotProcessManager._normalize_robot_config(
                robot_config, "replicacao_auditoria_d1"
            )
            settings = {"replicacao_config_base": merged.get("replicacao_config_base", "")}
            wf = str(workflow or cliente or "").strip()
            if not wf:
                return False, "Workflow obrigatório", {}
            consumo = int(consumo_acumulado)
            if consumo < 0:
                return False, "Consumo não pode ser negativo", {}
            ok = aplicar_ajuste_manual_consumo(
                str(ano_mes), wf, consumo, settings=settings, cliente=str(cliente or "")
            )
            if not ok:
                return False, "Falha ao aplicar ajuste", {}
            return True, "Ajuste aplicado", {"ano_mes": ano_mes, "workflow": wf, "consumo": consumo}
        except Exception as exc:
            return False, str(exc), {}

    @staticmethod
    def _validar_replicacao_aud_escala(merged_config: dict) -> tuple[bool, str]:
        """Pré-valida CSV de escala para o dia da replicação."""
        if not bool(merged_config.get("usar_escala_auditores", True)):
            return True, ""
        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots.replicacao_aud_planning import validar_escala_replicacao_pre_exec

            replicacao_settings = {
                key: merged_config.get(key)
                for key in REPLICACAO_AUD_CONFIG_DEFAULT
            }
            validar_escala_replicacao_pre_exec(replicacao_settings)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def validar_plano_replicacao(robot_config: dict | None = None) -> tuple[bool, str, dict]:
        """Gera plano (sem BRFlow) e retorna resumo para pré-voo."""
        merged = RobotProcessManager._normalize_robot_config(robot_config, "replicacao_auditoria")

        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from datetime import datetime, timedelta

            from app.bots.replicacao_aud_planning import (
                EscalaAuditoresAusenteError,
                gerar_plano_replicacao,
                validar_escala_replicacao_pre_exec,
                _resolver_data_escala_replicacao,
            )

            settings = {key: merged.get(key) for key in REPLICACAO_AUD_CONFIG_DEFAULT}
            settings["apenas_planejamento"] = True

            if bool(settings.get("usar_escala_auditores", True)):
                try:
                    validar_escala_replicacao_pre_exec(settings)
                except EscalaAuditoresAusenteError as exc:
                    data_ref = datetime.now() - timedelta(days=1)
                    raw_ref = str(settings.get("replicacao_aud_data_ref", "") or "").strip()
                    if raw_ref:
                        data_ref = datetime.strptime(raw_ref, "%Y%m%d")
                    data_escala = _resolver_data_escala_replicacao(data_ref, settings=settings)
                    return False, str(exc), {
                        "tipo": "escala_ausente",
                        "data_escala": data_escala.strftime("%Y%m%d"),
                        "data_escala_fmt": data_escala.strftime("%d/%m/%Y"),
                    }

            plano = gerar_plano_replicacao(settings=settings)
            linhas = [r for r in plano.resumo if r.get("Workflow") != "TOTAL"]
            return True, "Plano validado com sucesso", {
                "run_id": plano.run_id,
                "warnings": plano.warnings,
                "pasta_volumetria": getattr(plano, "pasta_volumetria", "") or "",
                "workflows_pendentes_config": getattr(plano, "workflows_pendentes_config", []) or [],
                "workflows_pendentes_count": len(getattr(plano, "workflows_pendentes_config", []) or []),
                "workflows_pendentes_novos": getattr(plano, "workflows_pendentes_novos", []) or [],
                "workflows_pendentes_existentes": getattr(plano, "workflows_pendentes_existentes", []) or [],
                "workflows_pendentes_falha_sync": getattr(plano, "workflows_pendentes_falha_sync", []) or [],
                "default_xlsx_path": getattr(plano, "default_xlsx_path", "") or "",
                "workflows": len(plano.workflows),
                "workflows_sem_d1": len(plano.workflows_sem_registro),
                "workflows_ok": len([r for r in linhas if r.get("Status") == "OK"]),
                "relatorio_excel": str(plano.relatorio_excel_path or ""),
                "resumo_csv": str(plano.relatorio_excel_path or plano.resumo_csv_path or ""),
                "dashboard_csv": str(plano.relatorio_excel_path or plano.dashboard_csv_path or ""),
                "pasta_protocolos": str(plano.pasta_protocolos),
                "parciais": len([r for r in linhas if r.get("Status") == "PARCIAL"]),
            }
        except FileNotFoundError as exc:
            return False, str(exc), {"tipo": "arquivo_ausente"}
        except Exception as exc:
            return False, str(exc), {"tipo": "erro"}

    @staticmethod
    def listar_workflows_replicacao(
        robot_config: dict | None = None,
        *,
        d1: bool = False,
    ) -> tuple[bool, str, list[dict]]:
        """Lista workflows ativos do Default.xlsx (aba Workflow d1)."""
        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots import replicacao_aud_planning as rap
            from app.config import COLUNA_CONFIG_CLIENTE, COLUNA_CONFIG_WORKFLOW

            mode = "replicacao_auditoria_d1" if d1 else "replicacao_auditoria"
            merged = RobotProcessManager._normalize_robot_config(robot_config, mode)
            settings = {"replicacao_config_base": merged.get("replicacao_config_base", "")}
            paths = rap._resolver_paths_config(settings)
            mapa = rap.carregar_mapa_workflow_d1(paths["default"], settings=settings)
            linhas: list[dict] = []
            for _, row in mapa.iterrows():
                workflow = str(row.get(COLUNA_CONFIG_WORKFLOW, "") or "").strip()
                if not workflow:
                    continue
                linhas.append(
                    {
                        "workflow": workflow,
                        "cliente": str(row.get(COLUNA_CONFIG_CLIENTE, "") or "").strip(),
                    }
                )
            linhas.sort(key=lambda item: (item["cliente"].casefold(), item["workflow"].casefold()))
            return True, "OK", linhas
        except Exception as exc:
            return False, str(exc), []

    @staticmethod
    def listar_runs_replicacao(limite: int = 15) -> list[dict]:
        """Lista execuções recentes (JSON de estado no OneDrive resumo/)."""
        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.config import PASTA_REPLICACAO_AUD_RESUMO, REPLICACAO_AUD_EXECUCAO_PREFIXO
            from app.bots.replicacao_aud_planning import carregar_estado_execucao

            if not PASTA_REPLICACAO_AUD_RESUMO.exists():
                return []
            paths = sorted(
                PASTA_REPLICACAO_AUD_RESUMO.glob(f"{REPLICACAO_AUD_EXECUCAO_PREFIXO}*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limite]
            runs = []
            for path in paths:
                run_id = path.stem.replace(REPLICACAO_AUD_EXECUCAO_PREFIXO, "", 1)
                estado = carregar_estado_execucao(run_id) or {}
                wfs = estado.get("workflows", {})
                runs.append({
                    "run_id": run_id,
                    "atualizado": estado.get("atualizado_em", ""),
                    "total_workflows": len(wfs),
                    "upload_ok": sum(1 for w in wfs.values() if w.get("status") == "UPLOAD_OK"),
                    "salvo_ok": sum(1 for w in wfs.values() if w.get("status") == "SALVO_OK"),
                    "inativo": sum(1 for w in wfs.values() if w.get("status") == "INATIVO"),
                    "pulado": sum(1 for w in wfs.values() if w.get("status") == "PULADO"),
                    "erro": sum(1 for w in wfs.values() if w.get("status") == "ERRO"),
                    "pendente": sum(1 for w in wfs.values() if w.get("status") == "PENDENTE"),
                })
            return runs
        except Exception:
            return []

    @staticmethod
    def _inject_replicacao_d1_db_snapshot(settings: dict) -> dict:
        """Quando fonte_banco_ativa, congela snapshot PostgreSQL em settings."""
        from app.core.path_setup import ensure_project_root_on_path

        ensure_project_root_on_path()
        from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa, try_inject_execution_snapshot

        settings = dict(settings or {})
        if not is_fonte_banco_ativa(settings):
            return settings
        return try_inject_execution_snapshot(settings)

    @staticmethod
    def _validar_replicacao_aud_d1_escala(merged_config: dict) -> tuple[bool, str]:
        if not bool(merged_config.get("usar_escala_auditores", True)):
            return True, ""
        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots.replicacao_aud_d1_planning import (
                _ensure_d1_settings,
                validar_escala_replicacao_pre_exec,
            )

            settings = RobotProcessManager._inject_replicacao_d1_db_snapshot(
                _ensure_d1_settings(
                    {key: merged_config.get(key) for key in REPLICACAO_AUD_D1_CONFIG_DEFAULT}
                )
            )
            validar_escala_replicacao_pre_exec(settings)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def validar_plano_replicacao_d1(robot_config: dict | None = None) -> tuple[bool, str, dict]:
        merged = RobotProcessManager._normalize_robot_config(robot_config, "replicacao_auditoria_d1")

        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from datetime import datetime, timedelta

            from app.bots.replicacao_aud_d1_planning import (
                EscalaAuditoresAusenteError,
                _ensure_d1_settings,
                gerar_plano_replicacao_d1,
                validar_escala_replicacao_pre_exec,
                _resolver_data_escala_replicacao,
            )

            settings = RobotProcessManager._inject_replicacao_d1_db_snapshot(
                _ensure_d1_settings(
                    {key: merged.get(key) for key in REPLICACAO_AUD_D1_CONFIG_DEFAULT}
                )
            )
            settings["apenas_planejamento"] = True

            if bool(settings.get("usar_escala_auditores", True)):
                try:
                    validar_escala_replicacao_pre_exec(settings)
                except EscalaAuditoresAusenteError as exc:
                    data_ref = datetime.now() - timedelta(days=1)
                    raw_ref = str(settings.get("replicacao_aud_data_ref", "") or "").strip()
                    if raw_ref:
                        data_ref = datetime.strptime(raw_ref, "%Y%m%d")
                    data_escala = _resolver_data_escala_replicacao(data_ref, settings=settings)
                    return False, str(exc), {
                        "tipo": "escala_ausente",
                        "data_escala": data_escala.strftime("%Y%m%d"),
                        "data_escala_fmt": data_escala.strftime("%d/%m/%Y"),
                    }

            plano = gerar_plano_replicacao_d1(settings=settings)
            linhas = [r for r in plano.resumo if r.get("Workflow") != "TOTAL"]
            from app.bots.replicacao_d1_db_bridge import summarize_plan_warnings_db

            warning_groups = summarize_plan_warnings_db(list(plano.warnings or []))
            return True, "Plano D-1 validado com sucesso", {
                "run_id": plano.run_id,
                "warnings": [item for group in warning_groups for item in group["items"]],
                "warning_groups": warning_groups,
                "parquet_referencia": getattr(plano, "pasta_volumetria", "") or "",
                "workflows_pendentes_config": getattr(plano, "workflows_pendentes_config", []) or [],
                "workflows_pendentes_count": len(getattr(plano, "workflows_pendentes_config", []) or []),
                "workflows_pendentes_novos": getattr(plano, "workflows_pendentes_novos", []) or [],
                "workflows_pendentes_existentes": getattr(plano, "workflows_pendentes_existentes", []) or [],
                "workflows_pendentes_falha_sync": getattr(plano, "workflows_pendentes_falha_sync", []) or [],
                "default_xlsx_path": getattr(plano, "default_xlsx_path", "") or "",
                "workflows": len(plano.workflows),
                "workflows_sem_d1": len(plano.workflows_sem_registro),
                "protocolos": sum(
                    len(plano.protocolos_por_workflow.get(workflow, []) or [])
                    for workflow in plano.workflows
                ),
                "workflows_ok": len([r for r in linhas if r.get("Status") == "OK"]),
                "relatorio_excel": str(plano.relatorio_excel_path or ""),
                "resumo_csv": str(plano.relatorio_excel_path or plano.resumo_csv_path or ""),
                "dashboard_csv": str(plano.relatorio_excel_path or plano.dashboard_csv_path or ""),
                "pasta_protocolos": str(plano.pasta_protocolos),
                "parciais": len([r for r in linhas if r.get("Status") == "PARCIAL"]),
            }
        except FileNotFoundError as exc:
            return False, str(exc), {"tipo": "arquivo_ausente"}
        except Exception as exc:
            return False, str(exc), {"tipo": "erro"}

    @staticmethod
    def listar_runs_replicacao_d1(limite: int = 15) -> list[dict]:
        """Lista apenas planos persistidos e executáveis no PostgreSQL."""
        try:
            from app.core.path_setup import ensure_project_root_on_path

            ensure_project_root_on_path()
            from app.bots.replicacao_d1_db_bridge import list_plan_runs_db

            return list_plan_runs_db(limit=limite)
        except Exception as exc:
            logging.getLogger(__name__).warning("Falha ao listar planos D-1 do banco: %s", exc)
            return []

    @staticmethod
    def _normalize_robot_config(raw: dict | None, mode: str | None = None) -> dict:
        cfg = RobotProcessManager._default_robot_config(mode or "")
        if not isinstance(raw, dict):
            return cfg

        output_dir = str(raw.get("output_dir", "")).strip()
        cfg["output_dir"] = output_dir

        headless_raw = raw.get("headless", False)
        if isinstance(headless_raw, str):
            cfg["headless"] = headless_raw.strip().lower() in ("1", "true", "yes", "on")
        else:
            cfg["headless"] = bool(headless_raw)

        max_workers_raw = raw.get("max_workers", None)
        try:
            max_workers = int(max_workers_raw)
            cfg["max_workers"] = max(1, min(max_workers, 5))
        except (TypeError, ValueError):
            cfg["max_workers"] = None

        exec_now_raw = raw.get("executar_imediatamente", False)
        if isinstance(exec_now_raw, str):
            cfg["executar_imediatamente"] = exec_now_raw.strip().lower() in ("1", "true", "yes", "on")
        else:
            cfg["executar_imediatamente"] = bool(exec_now_raw)

        cfg["rotina_data_inicio"] = str(raw.get("rotina_data_inicio", "")).strip()
        cfg["rotina_data_fim"] = str(raw.get("rotina_data_fim", "")).strip()
        ged_execucao_imediata = str(raw.get("ged_execucao_imediata", "")).strip().lower()
        cfg["ged_execucao_imediata"] = ged_execucao_imediata if ged_execucao_imediata in ("diurno", "noturno") else ""
        sharepoint_url = str(raw.get("sharepoint_url", "")).strip()
        cfg["sharepoint_folders"] = default_sharepoint_folders(mode or "")
        cfg["sharepoint_url"] = sharepoint_url or (
            cfg["sharepoint_folders"][0]["url"] if cfg["sharepoint_folders"] else default_sharepoint_url(mode or "")
        )

        # Normalizar lista de tarefas (opcional)
        tarefas_raw = raw.get("tarefas", [])
        if isinstance(tarefas_raw, str):
            tarefas_raw = [tarefas_raw]
        if not isinstance(tarefas_raw, list):
            tarefas_raw = []
        cfg["tarefas"] = [str(t).strip().lower() for t in tarefas_raw if str(t).strip()]

        if (mode or "") == "replicacao_auditoria":
            RobotProcessManager._normalize_replicacao_aud_config(
                raw, cfg, REPLICACAO_AUD_CONFIG_DEFAULT
            )
        if (mode or "") == "replicacao_auditoria_d1":
            RobotProcessManager._normalize_replicacao_aud_config(
                raw, cfg, REPLICACAO_AUD_D1_CONFIG_DEFAULT
            )
        if (mode or "") == "tray_ui":
            RobotProcessManager._normalize_tray_ui_config(raw, cfg)
        if (mode or "") == "falhas_criticas":
            RobotProcessManager._normalize_falhas_criticas_config(raw, cfg)
        if (mode or "") == "produtividade_case":
            RobotProcessManager._normalize_produtividade_case_config(raw, cfg)
        if (mode or "") == "production":
            RobotProcessManager._normalize_production_config(raw, cfg)
        if (mode or "") == "controle_sla":
            RobotProcessManager._normalize_controle_sla_config(raw, cfg)
        if (mode or "") == "prioridades_nh":
            RobotProcessManager._normalize_prioridades_nh_config(raw, cfg)

        return cfg

    @staticmethod
    def _normalize_prioridades_nh_config(raw: dict, cfg: dict) -> None:
        defaults = PRIORIDADES_NH_CONFIG_DEFAULT
        try:
            cfg["prioridades_nh_hora"] = max(
                0, min(23, int(raw.get("prioridades_nh_hora", defaults["prioridades_nh_hora"])))
            )
        except (TypeError, ValueError):
            cfg["prioridades_nh_hora"] = defaults["prioridades_nh_hora"]
        try:
            cfg["prioridades_nh_minuto"] = max(
                0, min(59, int(raw.get("prioridades_nh_minuto", defaults["prioridades_nh_minuto"])))
            )
        except (TypeError, ValueError):
            cfg["prioridades_nh_minuto"] = defaults["prioridades_nh_minuto"]
        cfg["executar_imediatamente"] = RobotProcessManager._parse_bool(
            raw.get("executar_imediatamente", cfg.get("executar_imediatamente")),
            defaults["executar_imediatamente"],
        )
        cfg["headless"] = RobotProcessManager._parse_bool(
            raw.get("headless", cfg.get("headless")),
            defaults["headless"],
        )

    @staticmethod
    def _normalize_controle_sla_config(raw: dict, cfg: dict) -> None:
        defaults = CONTROLE_SLA_CONFIG_DEFAULT
        cfg["poll_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("poll_seconds", cfg.get("poll_seconds")),
            defaults["poll_seconds"],
            15,
            3600,
        )
        cfg["gap_seconds"] = RobotProcessManager._parse_bounded_int(
            raw.get("gap_seconds", cfg.get("gap_seconds")),
            defaults["gap_seconds"],
            60,
            86400,
        )
        alerta = RobotProcessManager._parse_bounded_int(
            raw.get("sla_alerta_pct", cfg.get("sla_alerta_pct")),
            defaults["sla_alerta_pct"],
            1,
            200,
        )
        medio = RobotProcessManager._parse_bounded_int(
            raw.get("sla_medio_pct", cfg.get("sla_medio_pct")),
            defaults["sla_medio_pct"],
            1,
            200,
        )
        alto = RobotProcessManager._parse_bounded_int(
            raw.get("sla_alto_pct", cfg.get("sla_alto_pct")),
            defaults["sla_alto_pct"],
            1,
            200,
        )
        critico = RobotProcessManager._parse_bounded_int(
            raw.get("sla_critico_pct", cfg.get("sla_critico_pct")),
            defaults["sla_critico_pct"],
            1,
            500,
        )
        if medio <= alerta:
            medio = alerta + 1
        if alto <= medio:
            alto = medio + 1
        if critico <= alto:
            critico = alto + 1
        cfg["sla_alerta_pct"] = alerta
        cfg["sla_medio_pct"] = medio
        cfg["sla_alto_pct"] = alto
        cfg["sla_critico_pct"] = critico
        try:
            dias = int(raw.get("protocolos_dias", cfg.get("protocolos_dias", defaults["protocolos_dias"])))
            cfg["protocolos_dias"] = max(0, dias)
        except (TypeError, ValueError):
            cfg["protocolos_dias"] = defaults["protocolos_dias"]

    @staticmethod
    def _resolve_falhas_excel_path(raw_path: str, default: str) -> str:
        """Normaliza caminho da master: migra Bases→Bots, aceita pasta ou arquivo."""
        path = str(raw_path or "").strip() or str(default)
        legacy_bases = "Planejamento - IDF - Bases\\Bots\\report-falhas-criticas"
        new_bots = "Planejamento - IDF - Bots\\report-falhas-criticas"
        if legacy_bases in path:
            path = path.replace(legacy_bases, new_bots)

        candidate = Path(path).expanduser()
        # Caminhos configurados podem apontar para uma pasta que ainda não foi
        # criada. A ausência de extensão continua representando diretório.
        if candidate.is_dir() or not candidate.suffix:
            candidate = candidate / "FALHAS_CRITICAS_MANUAL.xlsx"
        if candidate.is_file():
            return str(candidate)

        default_p = Path(default).expanduser()
        if default_p.is_file():
            return str(default_p)

        legacy_file = Path(path.replace(new_bots, legacy_bases)).expanduser()
        if legacy_file.is_file():
            return str(legacy_file)

        return str(candidate)

    @staticmethod
    def _validate_falhas_excel_path(excel_path: str) -> tuple[bool, str]:
        path = Path(str(excel_path).strip()).expanduser()
        if path.is_dir():
            path = path / "FALHAS_CRITICAS_MANUAL.xlsx"
        if not path.is_file():
            return (
                False,
                f"Planilha master não encontrada: {path}\n"
                f"Configure o caminho em Falhas Críticas ou copie FALHAS_CRITICAS_MANUAL.xlsx para:\n"
                f"  {DEFAULT_FALHAS_CRITICAS_EXCEL.parent}",
            )
        return True, ""

    @staticmethod
    def _normalize_production_config(raw: dict, cfg: dict) -> None:
        defaults = PRODUCTION_CONFIG_DEFAULT
        try:
            minutos = int(raw.get("tempo_espera_minutos", defaults["tempo_espera_minutos"]))
        except (TypeError, ValueError):
            minutos = defaults["tempo_espera_minutos"]
        cfg["tempo_espera_minutos"] = max(
            PRODUCTION_TEMPO_ESPERA_MIN_MINUTOS,
            min(PRODUCTION_TEMPO_ESPERA_MAX_MINUTOS, minutos),
        )
        try:
            dias_download = int(raw.get("dias_download_brflow", defaults["dias_download_brflow"]))
        except (TypeError, ValueError):
            dias_download = defaults["dias_download_brflow"]
        cfg["dias_download_brflow"] = max(
            PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MIN,
            min(PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MAX, dias_download),
        )
        cfg["executar_produtividade_case"] = RobotProcessManager._parse_bool(
            raw.get("executar_produtividade_case"),
            defaults["executar_produtividade_case"],
        )
        for flag in (
            "executar_confer",
            "executar_brflow",
            "baixar_monitor_com_producao",
            "baixar_log_eventos_com_producao",
            "executar_ged_irregularidade",
        ):
            cfg[flag] = RobotProcessManager._parse_bool(raw.get(flag), defaults.get(flag, True))

    @staticmethod
    def _normalize_falhas_criticas_config(raw: dict, cfg: dict) -> None:
        defaults = FALHAS_CRITICAS_CONFIG_DEFAULT
        cfg["falhas_excel_path"] = RobotProcessManager._resolve_falhas_excel_path(
            str(raw.get("falhas_excel_path", defaults["falhas_excel_path"])),
            defaults["falhas_excel_path"],
        )
        cfg["mes_referencia"] = str(raw.get("mes_referencia", defaults["mes_referencia"])).strip()
        cfg["email_from"] = str(raw.get("email_from", defaults["email_from"])).strip()
        try:
            cfg["falhas_criticas_hora"] = max(0, min(23, int(raw.get("falhas_criticas_hora", defaults["falhas_criticas_hora"]))))
        except (TypeError, ValueError):
            cfg["falhas_criticas_hora"] = defaults["falhas_criticas_hora"]
        try:
            cfg["falhas_criticas_minuto"] = max(0, min(59, int(raw.get("falhas_criticas_minuto", defaults["falhas_criticas_minuto"]))))
        except (TypeError, ValueError):
            cfg["falhas_criticas_minuto"] = defaults["falhas_criticas_minuto"]
        cfg["gerar_consolidado"] = RobotProcessManager._parse_bool(
            raw.get("gerar_consolidado"), defaults["gerar_consolidado"]
        )
        cfg["gerar_executivo"] = RobotProcessManager._parse_bool(
            raw.get("gerar_executivo"), defaults["gerar_executivo"]
        )
        cfg["preview_email"] = RobotProcessManager._parse_bool(
            raw.get("preview_email"), defaults["preview_email"]
        )
        cfg["falhas_refresh_queries"] = RobotProcessManager._parse_bool(
            raw.get("falhas_refresh_queries"), defaults["falhas_refresh_queries"]
        )
        cfg["modo_segundo_plano"] = RobotProcessManager._parse_bool(
            raw.get("modo_segundo_plano"), defaults["modo_segundo_plano"]
        )
        cfg["salvar_html_individuais"] = RobotProcessManager._parse_bool(
            raw.get("salvar_html_individuais"), defaults["salvar_html_individuais"]
        )

    @staticmethod
    def _normalize_produtividade_case_config(raw: dict, cfg: dict) -> None:
        defaults = PRODUTIVIDADE_CASE_CONFIG_DEFAULT
        allowed = set(PRODUTIVIDADE_CASE_TAREFAS_DEFAULT)
        tarefas_raw = raw.get("tarefas", defaults["tarefas"])
        if isinstance(tarefas_raw, str):
            tarefas_raw = [t.strip() for t in tarefas_raw.split(",") if t.strip()]
        if not isinstance(tarefas_raw, list):
            tarefas_raw = list(defaults["tarefas"])
        tarefas = []
        for item in tarefas_raw:
            key = str(item).strip().lower()
            if key in allowed and key not in tarefas:
                tarefas.append(key)
        cfg["tarefas"] = tarefas or list(defaults["tarefas"])
        for key in ("dir_hora", "dir_tempo_logado", "dir_consolidado", "dir_temp"):
            cfg[key] = str(raw.get(key, defaults.get(key, ""))).strip()

    def reveal_falhas_process(self) -> tuple[bool, str]:
        """Sinaliza ao bot Falhas Críticas para revelar Excel (modo 2º plano)."""
        try:
            from app.bots.falhas_criticas.reveal_process import request_reveal
        except ImportError as exc:
            return False, f"Módulo reveal não disponível: {exc}"
        running, _pid = self._mode_runtime("falhas_criticas")
        if not running:
            return False, "Robô Falhas Críticas não está em execução"
        request_reveal()
        with self._lock:
            self._runtime_ui["falhas_criticas"]["status"] = "Revelando processo (Excel)…"
            self._runtime_ui["falhas_criticas"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return True, "Sinal enviado — Excel ficará visível se estiver aberto"

    def _load_robot_configs(self):
        try:
            if not self.robot_config_file.exists():
                self._save_robot_configs()
                return

            loaded = json.loads(self.robot_config_file.read_text(encoding="utf-8") or "{}")
            if not isinstance(loaded, dict):
                return

            for mode in ROBOT_MODES:
                self._robot_configs[mode] = self._normalize_robot_config(loaded.get(mode), mode)
        except Exception:
            pass

    def _save_robot_configs(self):
        try:
            payload = {
                mode: self._normalize_robot_config(self._robot_configs.get(mode), mode)
                for mode in ROBOT_MODES
            }
            self.robot_config_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def robot_configs(self, mode: str | None = None) -> dict:
        with self._lock:
            if mode:
                if mode not in ROBOT_MODES:
                    return {}
                return {mode: self._normalize_robot_config(self._robot_configs.get(mode), mode)}
            return {
                name: self._normalize_robot_config(cfg, name)
                for name, cfg in self._robot_configs.items()
            }

    def update_robot_config(self, mode: str, config: dict) -> tuple[bool, str, dict]:
        if mode not in ROBOT_MODES:
            return False, "Modo inválido", {}
        normalized = self._normalize_robot_config(config, mode)
        with self._lock:
            self._robot_configs[mode] = normalized
            self._save_robot_configs()
            return True, f"Configuração do robô '{mode}' atualizada", {mode: normalized}

    def _credentials_fingerprint(self, matricula: str, senha: str) -> str:
        raw = f"{matricula}\0{senha}".encode("utf-8", errors="ignore")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _empty_okta_credentials_state() -> dict:
        return {
            "validated": False,
            "message": "Credenciais não verificadas",
            "last_checked_at": None,
            "checking": False,
            "fingerprint": None,
        }

    @staticmethod
    def _normalize_okta_session_id(session_id: str | None) -> str:
        sid = str(session_id or "").strip()
        if not sid:
            return ""
        # UUID / token curto; evita chaves absurdas
        return sid[:80]

    def _ensure_okta_session(self, session_id: str | None) -> tuple[str, dict]:
        sid = self._normalize_okta_session_id(session_id) or "default"
        state = self._okta_sessions.get(sid)
        if state is None:
            state = self._empty_okta_credentials_state()
            self._okta_sessions[sid] = state
        return sid, state

    def _set_credentials_validation(
        self,
        validated: bool,
        message: str,
        fingerprint: str | None = None,
        session_id: str | None = None,
    ):
        sid, state = self._ensure_okta_session(session_id or self._okta_active_session)
        state["validated"] = bool(validated)
        state["message"] = str(message)
        state["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
        state["checking"] = False
        state["fingerprint"] = fingerprint if validated else None
        # Espelho legado (Flask / testes antigos sem client_id)
        if sid == "default":
            self._credentials_validation = state
        if self._okta_active_session == sid:
            self._okta_active_session = None

    def _credentials_status_unlocked(self, session_id: str | None = None) -> dict:
        sid = self._normalize_okta_session_id(session_id)
        if sid:
            state = self._okta_sessions.get(sid) or self._empty_okta_credentials_state()
        elif self._okta_active_session:
            # Sem client_id: nunca vazar "checking" de outra sessão
            state = self._empty_okta_credentials_state()
        else:
            state = self._credentials_validation
        return {
            "validated": bool(state.get("validated")),
            "message": str(state.get("message") or ""),
            "last_checked_at": state.get("last_checked_at"),
            "checking": bool(state.get("checking")),
        }

    def credentials_status(self, session_id: str | None = None) -> dict:
        with self._lock:
            return self._credentials_status_unlocked(session_id)

    @staticmethod
    def _parse_okta_validate_output(stdout: str, stderr: str) -> tuple[bool | None, str]:
        parsed_ok = None
        parsed_message = ""
        output = (stdout or "").strip()
        if output:
            for line in reversed(output.splitlines()):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                    if "ok" in payload:
                        parsed_ok = bool(payload.get("ok"))
                    parsed_message = str(payload.get("message", "")).strip()
                    break
                except Exception:
                    continue

        if parsed_message:
            return parsed_ok, parsed_message

        err_tail = "\n".join((stderr or "").strip().splitlines()[-3:]).strip()
        if err_tail:
            return parsed_ok, err_tail[:300]

        out_tail = "\n".join(output.splitlines()[-3:]).strip() if output else ""
        if out_tail:
            return parsed_ok, out_tail[:300]

        return parsed_ok, ""

    def _okta_validate_pid_file(self) -> Path:
        tag = _okta_env_tag()
        return self.pids_dir / f"okta_validate_{tag}.pid"

    def _okta_validate_selenium_pids_file(self) -> Path:
        tag = _okta_env_tag()
        return self.pids_dir / f"okta_validate_selenium_{tag}.pids.json"

    def _write_okta_validate_pid(self, pid: int) -> None:
        try:
            self._okta_validate_pid_file().write_text(str(pid), encoding="utf-8")
        except Exception as exc:
            log.warning("Não foi possível gravar pid da validação Okta: %s", exc)

    def _read_okta_validate_pid(self) -> int | None:
        path = self._okta_validate_pid_file()
        if not path.is_file():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def _clear_okta_validate_pid(self) -> None:
        try:
            self._okta_validate_pid_file().unlink(missing_ok=True)
        except Exception:
            pass

    def _read_okta_validate_selenium_pids(self) -> list[int]:
        path = self._okta_validate_selenium_pids_file()
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [int(item) for item in payload if str(item).isdigit()]
        except Exception:
            pass
        return []

    def _clear_okta_validate_selenium_pids(self) -> None:
        try:
            self._okta_validate_selenium_pids_file().unlink(missing_ok=True)
        except Exception:
            pass

    def _collect_windows_descendants(self, root_pid: int) -> list[int]:
        collected: list[int] = []
        pending = [root_pid]
        seen: set[int] = set()
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        while pending:
            pid = pending.pop()
            if pid in seen or pid <= 0:
                continue
            seen.add(pid)
            collected.append(pid)
            try:
                result = subprocess.run(
                    ["wmic", "process", "where", f"ParentProcessId={pid}", "get", "ProcessId"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=creationflags,
                    check=False,
                )
                for line in (result.stdout or "").splitlines():
                    token = line.strip()
                    if token.isdigit():
                        pending.append(int(token))
            except Exception:
                pass
        return collected

    def _stop_okta_validate_process(self, proc: subprocess.Popen | None = None, *, force: bool = False) -> None:
        pids: set[int] = set()
        if proc and proc.poll() is None:
            pids.add(proc.pid)
        if force or (proc and proc.poll() is None):
            file_pid = self._read_okta_validate_pid()
            if file_pid:
                pids.add(file_pid)
        pids.update(self._read_okta_validate_selenium_pids())

        for pid in sorted(pids):
            if os.name == "nt":
                for target in reversed(self._collect_windows_descendants(pid)):
                    self._kill_pid(target)
            self._kill_pid(pid)

        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        self._clear_okta_validate_pid()
        self._clear_okta_validate_selenium_pids()

    def cancel_okta_credentials_validation(
        self, session_id: str | None = None
    ) -> tuple[bool, str, dict]:
        sid = self._normalize_okta_session_id(session_id) or "default"
        with self._lock:
            active = self._okta_active_session
            _, own_state = self._ensure_okta_session(sid)
            checking = bool(own_state.get("checking"))
            # Só o dono da validação ativa pode cancelar o processo Selenium
            if active and active != sid:
                status = self._credentials_status_unlocked(sid)
                return (
                    False,
                    "Não há validação Okta sua em andamento.",
                    status,
                )
            proc = self._okta_validate_proc
            self._okta_validate_cancel_requested = True

        proc_running = proc is not None and proc.poll() is None
        has_pid_file = self._read_okta_validate_pid() is not None
        has_selenium = bool(self._read_okta_validate_selenium_pids())

        if checking or proc_running or has_pid_file or has_selenium:
            self._stop_okta_validate_process(proc, force=True)
            log.info("Validação Okta cancelada pelo usuário (session=%s)", sid)

        with self._lock:
            self._okta_validate_proc = None
            if checking or proc_running or has_pid_file or has_selenium:
                self._set_credentials_validation(
                    False, "Validação cancelada pelo usuário", session_id=sid
                )
            status = self._credentials_status_unlocked(sid)
            if not checking and not proc_running and not has_pid_file and not has_selenium:
                message = "Nenhuma validação em andamento."
                return True, message, status
            return True, "Validação cancelada pelo usuário", status

    def start_okta_credentials_validation(
        self,
        matricula: str,
        senha: str,
        timeout: int = OKTA_VALIDATE_TIMEOUT_SECONDS,
        headless: bool = False,
        session_id: str | None = None,
    ) -> tuple[bool, str, dict]:
        matricula = (matricula or "").strip()
        senha = str(senha or "")
        sid = self._normalize_okta_session_id(session_id) or "default"
        if not matricula or not senha:
            with self._lock:
                self._set_credentials_validation(
                    False, "Informe matrícula e senha para validar no Okta", session_id=sid
                )
                status = self._credentials_status_unlocked(sid)
            return False, "Informe matrícula e senha para validar no Okta", status

        takeover_from: str | None = None
        with self._lock:
            active = self._okta_active_session
            if active and active != sid:
                other = self._okta_sessions.get(active) or {}
                if other.get("checking"):
                    # Outra aba/porta iniciou validação — assume o Selenium (único na máquina).
                    takeover_from = active
                    self._okta_validate_cancel_requested = True
                    other["checking"] = False
                    other["message"] = "Validação cancelada: outra sessão assumiu o Okta."
            _, state = self._ensure_okta_session(sid)
            if state.get("checking") and not takeover_from:
                status = self._credentials_status_unlocked(sid)
                return False, "Validação Okta já em andamento", status

        if takeover_from:
            with self._lock:
                proc = self._okta_validate_proc
            self._stop_okta_validate_process(proc, force=True)
            with self._lock:
                self._okta_validate_proc = None
            log.info(
                "Validação Okta: sessão %s assumiu o lugar de %s",
                sid,
                takeover_from,
            )

        with self._lock:
            _, state = self._ensure_okta_session(sid)
            state["checking"] = True
            state["message"] = (
                "Validando credenciais no Okta..."
                if not takeover_from
                else "Validando credenciais no Okta (assumiu outra sessão)..."
            )
            state["validated"] = False
            self._okta_active_session = sid
            self._okta_validate_cancel_requested = False
            self._okta_validate_proc = None
            if sid == "default":
                self._credentials_validation = state

        thread = threading.Thread(
            target=self._run_okta_credentials_validation,
            args=(matricula, senha, timeout, headless, sid),
            daemon=True,
            name="okta-credentials-validate",
        )
        self._okta_validate_thread = thread
        # #region agent log
        try:
            import json as _json
            import time as _time

            with open(r"c:\Users\c91763a\PPLID\debug-6a88aa.log", "a", encoding="utf-8") as _f:
                _f.write(
                    _json.dumps(
                        {
                            "sessionId": "6a88aa",
                            "hypothesisId": "A",
                            "location": "robot_manager.py:start_okta_credentials_validation",
                            "message": "okta_validation_started",
                            "data": {
                                "sessionId": sid[:80],
                                "headless": bool(headless),
                                "takeoverFrom": (takeover_from or "")[:80],
                                "timeout": int(timeout),
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        thread.start()
        with self._lock:
            msg = "Validação Okta iniciada"
            if takeover_from:
                msg = "Validação Okta iniciada (assumiu outra sessão)"
            return True, msg, self._credentials_status_unlocked(sid)

    def validate_okta_credentials(
        self,
        matricula: str,
        senha: str,
        timeout: int = OKTA_VALIDATE_TIMEOUT_SECONDS,
        headless: bool = False,
        session_id: str | None = None,
    ) -> tuple[bool, str, dict]:
        sid = self._normalize_okta_session_id(session_id) or "default"
        ok, message, status = self.start_okta_credentials_validation(
            matricula=matricula,
            senha=senha,
            timeout=timeout,
            headless=headless,
            session_id=sid,
        )
        if not ok:
            return False, message, status

        thread = self._okta_validate_thread
        if thread and thread.is_alive():
            thread.join(timeout=max(10, int(timeout)) + 15)

        with self._lock:
            status = self._credentials_status_unlocked(sid)
            return bool(status["validated"]), str(status["message"]), status

    def _run_okta_credentials_validation(
        self,
        matricula: str,
        senha: str,
        timeout: int,
        headless: bool,
        session_id: str = "default",
    ) -> None:
        sid = self._normalize_okta_session_id(session_id) or "default"
        self.runner_cwd, self.runner_file, self.runner_module = self._resolve_runner_path()
        self.okta_validate_script = self._resolve_okta_validate_script()
        if not self.okta_validate_script.exists():
            with self._lock:
                self._set_credentials_validation(
                    False, "Robô de validação Okta não encontrado", session_id=sid
                )
            return

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(self.project_root)
        # Força o modo pedido pela UI; evita herdar HEADLESS=1 do ambiente do runserver.
        env.pop("HEADLESS", None)
        env.pop("ROBOT_HEADLESS", None)
        self._clear_credential_env(env)
        env["OKTA_USER"] = matricula
        env["OKTA_PASS"] = senha
        env["MONITOR_USER"] = matricula
        env["MONITOR_PASS"] = senha
        env["NIVEL_USER"] = matricula
        env["NIVEL_PASS"] = senha
        env["ROBOT_USER"] = matricula
        env["ROBOT_PASS"] = senha
        env["OKTA_VALIDATE_HEADLESS"] = "1" if headless else "0"
        env[OKTA_VALIDATE_PIDS_ENV] = str(self._okta_validate_selenium_pids_file())

        # Nunca CREATE_NO_WINDOW no modo visível — senão o Chrome sobe sem UI.
        creationflags = 0
        if headless and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        log.info(
            "Validação Okta iniciando (session=%s headless=%s script=%s)",
            sid,
            headless,
            self.okta_validate_script,
        )

        proc: subprocess.Popen | None = None
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        readers: list[threading.Thread] = []

        def _drain(stream, chunks: list[str]) -> None:
            if not stream:
                return
            try:
                for line in stream:
                    chunks.append(line)
            except Exception:
                pass

        try:
            proc = subprocess.Popen(
                [str(self.python_bin), "-u", str(self.okta_validate_script)],
                cwd=str(self.project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            self._write_okta_validate_pid(proc.pid)
            with self._lock:
                self._okta_validate_proc = proc

            # Drena pipes em threads — sem isso o Chrome/chromedriver trava quando
            # stderr enche o buffer (~64KB) e a janela nunca aparece.
            readers = [
                threading.Thread(
                    target=_drain,
                    args=(proc.stdout, stdout_chunks),
                    daemon=True,
                    name="okta-validate-stdout",
                ),
                threading.Thread(
                    target=_drain,
                    args=(proc.stderr, stderr_chunks),
                    daemon=True,
                    name="okta-validate-stderr",
                ),
            ]
            for reader in readers:
                reader.start()

            deadline = time.monotonic() + max(10, int(timeout))
            while proc.poll() is None:
                if self._okta_validate_cancel_requested:
                    self._stop_okta_validate_process(proc, force=True)
                    with self._lock:
                        self._okta_validate_proc = None
                        self._set_credentials_validation(
                            False, "Validação cancelada pelo usuário", session_id=sid
                        )
                    return
                if time.monotonic() >= deadline:
                    self._stop_okta_validate_process(proc, force=True)
                    message = f"Validação Okta excedeu o tempo limite ({timeout}s)"
                    log.warning("Validação Okta timeout após %ss", timeout)
                    with self._lock:
                        self._okta_validate_proc = None
                        self._set_credentials_validation(False, message, session_id=sid)
                    return
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    continue

            for reader in readers:
                reader.join(timeout=3)
            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)

            with self._lock:
                if self._okta_validate_cancel_requested:
                    self._set_credentials_validation(
                        False, "Validação cancelada pelo usuário", session_id=sid
                    )
                    return

            parsed_ok, parsed_message = self._parse_okta_validate_output(stdout, stderr)
            ok = parsed_ok if parsed_ok is not None else proc.returncode == 0
            message = parsed_message or (
                "Credenciais Okta validadas com sucesso" if ok else "Falha na validação das credenciais no Okta"
            )

            if not ok:
                log.warning(
                    "Validação Okta falhou (exit=%s). stderr: %s | stdout: %s",
                    proc.returncode,
                    (stderr or "").strip()[-500:],
                    (stdout or "").strip()[-500:],
                )

            with self._lock:
                if self._okta_validate_cancel_requested:
                    self._set_credentials_validation(
                        False, "Validação cancelada pelo usuário", session_id=sid
                    )
                    return
                self._set_credentials_validation(
                    ok,
                    message,
                    self._credentials_fingerprint(matricula, senha) if ok else None,
                    session_id=sid,
                )
        except Exception as exc:
            if self._okta_validate_cancel_requested:
                message = "Validação cancelada pelo usuário"
            else:
                message = f"Erro ao validar credenciais no Okta: {exc}"
                log.exception("Erro inesperado na validação Okta")
            with self._lock:
                self._set_credentials_validation(False, message, session_id=sid)
        finally:
            for reader in readers:
                if reader.is_alive():
                    reader.join(timeout=1)
            with self._lock:
                self._okta_validate_proc = None
            if self._okta_validate_cancel_requested:
                self._stop_okta_validate_process(proc, force=True)
            else:
                self._clear_okta_validate_pid()
                self._clear_okta_validate_selenium_pids()
            # #region agent log
            try:
                import json as _json
                import time as _time

                with open(r"c:\Users\c91763a\PPLID\debug-6a88aa.log", "a", encoding="utf-8") as _f:
                    _f.write(
                        _json.dumps(
                            {
                                "sessionId": "6a88aa",
                                "hypothesisId": "D",
                                "location": "robot_manager.py:_run_okta_credentials_validation.finally",
                                "message": "okta_validation_finished",
                                "data": {
                                    "sessionId": sid[:80],
                                    "cancelRequested": bool(self._okta_validate_cancel_requested),
                                    "procReturncode": getattr(proc, "returncode", None) if proc else None,
                                },
                                "timestamp": int(_time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion

    def _resolve_runner_path(self) -> tuple[Path, Path | None, str | None]:
        """Return (cwd, runner_script, runner_module) for subprocess invocation."""
        if self.external_robots_dir:
            external_runner = self.external_robots_dir / "robot_runner.py"
            if self.external_robots_dir.exists() and external_runner.exists():
                import logging
                logging.getLogger(__name__).warning(
                    "SERASA_ROBOS_SRC is deprecated; prefer pip install -e . in the project root"
                )
                return self.external_robots_dir, external_runner, None

        if self.bundled_runner_file.exists():
            return self.project_root, None, "app.orchestration.robot_runner"

        return self.project_root, self.bundled_runner_file, None

    def _resolve_okta_validate_script(self) -> Path:
        if self.external_robots_dir:
            legacy = self.external_robots_dir / "bots" / "bot_okta_validate.py"
            if legacy.exists():
                return legacy
        return self.project_root / "app" / "bots" / "bot_okta_validate.py"

    def _resolve_suporte_claro_jira_script(self) -> Path:
        return self.project_root / "app" / "bots" / "suporte_claro_jira" / "runner.py"

    def suporte_claro_jira_status(self) -> dict:
        with self._lock:
            return dict(self._suporte_claro_jira_status)

    def _cleanup_suporte_claro_jira_proc(self) -> None:
        with self._lock:
            proc = self._suporte_claro_jira_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._lock:
            self._suporte_claro_jira_proc = None

    def cancel_suporte_claro_jira(self) -> tuple[bool, str, dict]:
        with self._lock:
            proc = self._suporte_claro_jira_proc
            running = bool(self._suporte_claro_jira_status.get("running"))
            if not running and (not proc or proc.poll() is not None):
                return True, "Nenhuma automação Jira em execução.", self.suporte_claro_jira_status()
            self._suporte_claro_jira_cancel = True
            self._suporte_claro_jira_status["running"] = False
            self._suporte_claro_jira_status["message"] = "Cancelamento solicitado"
            self._suporte_claro_jira_status["ended_at"] = datetime.now().isoformat(timespec="seconds")
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        return True, "Automação Jira cancelada.", self.suporte_claro_jira_status()

    def start_suporte_claro_jira(
        self,
        matricula: str,
        senha: str,
        payload: dict,
        *,
        headless: bool = False,
        timeout: int = 600,
    ) -> tuple[bool, str, dict]:
        matricula = (matricula or "").strip()
        senha = str(senha or "")
        if not matricula or not senha:
            return False, "Informe matrícula e senha Okta.", self.suporte_claro_jira_status()

        with self._lock:
            if self._suporte_claro_jira_status.get("running"):
                return False, "Automação Jira já em execução.", self.suporte_claro_jira_status()
        self._cleanup_suporte_claro_jira_proc()
        batch_total = (payload or {}).get("batch_total") or len((payload or {}).get("items") or [])
        if batch_total:
            timeout = max(int(timeout), int(batch_total) * 180)
        with self._lock:
            self._suporte_claro_jira_status = {
                "running": True,
                "message": "Iniciando Chrome…",
                "step": "init",
                "step_label": "Iniciando automação…",
                "detail": None,
                "result": None,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "ended_at": None,
                "kind": str((payload or {}).get("kind") or "").strip().lower() or None,
                "registro_id": (payload or {}).get("registro_id"),
                "batch_total": (payload or {}).get("batch_total"),
                "batch_index": None,
                "batch_items": [],
            }
            self._suporte_claro_jira_cancel = False

        thread = threading.Thread(
            target=self._run_suporte_claro_jira,
            args=(matricula, senha, dict(payload or {}), headless, timeout),
            daemon=True,
            name="suporte-claro-jira",
        )
        self._suporte_claro_jira_thread = thread
        thread.start()
        return True, "Automação Jira iniciada — o Chrome abrirá neste computador.", self.suporte_claro_jira_status()

    def _run_suporte_claro_jira(
        self,
        matricula: str,
        senha: str,
        payload: dict,
        headless: bool,
        timeout: int,
    ) -> None:
        script = self._resolve_suporte_claro_jira_script()
        if not script.exists():
            with self._lock:
                self._suporte_claro_jira_status.update(
                    {
                        "running": False,
                        "message": "Script Suporte Claro Jira não encontrado.",
                        "result": {"ok": False},
                        "ended_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            return

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(self.project_root)
        self._clear_credential_env(env)
        for key, val in (
            ("OKTA_USER", matricula),
            ("ROBOT_USER", matricula),
            ("MONITOR_USER", matricula),
            ("NIVEL_USER", matricula),
            ("OKTA_PASS", senha),
            ("ROBOT_PASS", senha),
            ("MONITOR_PASS", senha),
            ("NIVEL_PASS", senha),
        ):
            env[key] = val
        env["SUPORTE_CLARO_JIRA_PAYLOAD"] = json.dumps(payload, ensure_ascii=False)
        env["SUPORTE_CLARO_JIRA_HEADLESS"] = "1" if headless else "0"
        env["PYTHONUNBUFFERED"] = "1"

        JIRA_PROGRESS_PREFIX = "@@JIRA_PROGRESS@@"
        output_lines: list[str] = []
        proc: subprocess.Popen | None = None

        def _consume_jira_stdout() -> None:
            if not proc or not proc.stdout:
                return
            for line in proc.stdout:
                output_lines.append(line)
                stripped = line.strip()
                if not stripped.startswith(JIRA_PROGRESS_PREFIX):
                    continue
                try:
                    progress = json.loads(stripped[len(JIRA_PROGRESS_PREFIX) :])
                except json.JSONDecodeError:
                    continue
                with self._lock:
                    step = progress.get("step")
                    if step:
                        self._suporte_claro_jira_status["step"] = step
                    if progress.get("step_label"):
                        self._suporte_claro_jira_status["step_label"] = progress["step_label"]
                    if progress.get("message"):
                        self._suporte_claro_jira_status["message"] = progress["message"]
                    if progress.get("detail"):
                        self._suporte_claro_jira_status["detail"] = progress["detail"]
                    if progress.get("batch_total") is not None:
                        self._suporte_claro_jira_status["batch_total"] = progress["batch_total"]
                    if progress.get("batch_index") is not None:
                        self._suporte_claro_jira_status["batch_index"] = progress["batch_index"]
                    if progress.get("batch_items") is not None:
                        self._suporte_claro_jira_status["batch_items"] = progress["batch_items"]
                    if progress.get("batch_current_registro_id") is not None:
                        self._suporte_claro_jira_status["batch_current_registro_id"] = (
                            progress["batch_current_registro_id"]
                        )
                    terminal = step in ("error", "done", "batch_done")
                    if terminal and (step != "done" or progress.get("ok")):
                        self._suporte_claro_jira_status["running"] = False
                        self._suporte_claro_jira_status["ended_at"] = datetime.now().isoformat(
                            timespec="seconds"
                        )

        try:
            with self._lock:
                self._suporte_claro_jira_status["message"] = "Abrindo Okta…"
                self._suporte_claro_jira_status["step"] = "init"
            proc = subprocess.Popen(
                [str(self.python_bin), "-u", str(script)],
                cwd=str(self.project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            with self._lock:
                self._suporte_claro_jira_proc = proc

            reader = threading.Thread(
                target=_consume_jira_stdout,
                daemon=True,
                name="suporte-claro-jira-stdout",
            )
            reader.start()

            try:
                proc.wait(timeout=max(180, int(timeout)))
                stderr = ""
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                reader.join(timeout=3)
                result = {"ok": False, "message": "Tempo esgotado na automação Jira.", "step": "error"}
            else:
                reader.join(timeout=5)
                stdout = "".join(output_lines)
                result = None
                for line in reversed((stdout or "").splitlines()):
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    if line.startswith(JIRA_PROGRESS_PREFIX):
                        continue
                    try:
                        result = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
                if result is None:
                    err = stdout.strip() or f"exit={proc.returncode}"
                    result = {"ok": False, "message": err[:800], "step": "error"}

            with self._lock:
                ok = bool(result.get("ok"))
                msg = str(result.get("message") or ("Concluído" if ok else "Falha"))
                step = result.get("step")
                step_label = self._suporte_claro_jira_status.get("step_label")
                if step and not ok:
                    label = step_label or step
                    msg = f"Falhou em «{label}»: {msg}"
                elif ok and step_label:
                    msg = str(self._suporte_claro_jira_status.get("message") or msg)
                self._suporte_claro_jira_status.update(
                    {
                        "running": False,
                        "message": msg,
                        "step": step or self._suporte_claro_jira_status.get("step"),
                        "result": result,
                        "ended_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                if isinstance(result.get("items"), list):
                    self._suporte_claro_jira_status["batch_items"] = result["items"]
                if result.get("total") is not None:
                    self._suporte_claro_jira_status["batch_total"] = result.get("total")
        except Exception as exc:
            with self._lock:
                self._suporte_claro_jira_status.update(
                    {
                        "running": False,
                        "message": f"Erro: {exc}",
                        "result": {"ok": False, "message": str(exc)},
                        "ended_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
        finally:
            with self._lock:
                self._suporte_claro_jira_proc = None

    def _runner_command(self, mode: str) -> list[str]:
        if self.runner_module:
            return [str(self.python_bin), "-m", self.runner_module, "--mode", mode]
        return [str(self.python_bin), str(self.runner_file), "--mode", mode]

    def _load_execution_history(self):
        try:
            if not self.execution_history_file.exists():
                self._save_execution_history()
                return

            loaded = json.loads(self.execution_history_file.read_text(encoding="utf-8") or "{}")
            if not isinstance(loaded, dict):
                return

            for mode in ROBOT_MODES:
                persisted = loaded.get(mode, {})
                if not isinstance(persisted, dict):
                    continue
                self._execution[mode]["started_at"] = persisted.get("started_at")
                self._execution[mode]["ended_at"] = persisted.get("ended_at")
                self._execution[mode]["duration_seconds"] = persisted.get("duration_seconds")
                self._execution[mode]["result"] = persisted.get("result")
                self._execution[mode]["output_paths"] = persisted.get("output_paths")
        except Exception:
            pass

    def _load_cycle_runs(self):
        try:
            if self.cycle_runs_file.exists():
                loaded = json.loads(self.cycle_runs_file.read_text(encoding="utf-8") or "{}")
                if isinstance(loaded, dict):
                    for mode in ROBOT_MODES:
                        raw_points = loaded.get(mode, [])
                        if not isinstance(raw_points, list):
                            continue
                        points: list[dict] = []
                        for item in raw_points:
                            if not isinstance(item, dict):
                                continue
                            ended_at = item.get("ended_at")
                            duration = item.get("duration_seconds")
                            result = item.get("result")
                            if not ended_at or duration is None:
                                continue
                            try:
                                duration_f = float(duration)
                            except (TypeError, ValueError):
                                continue
                            if duration_f < 0:
                                continue
                            point = {
                                "ended_at": str(ended_at),
                                "duration_seconds": round(duration_f, 1),
                                "result": str(result or "").strip() or "desconhecido",
                            }
                            label = str(item.get("label", "")).strip()
                            if label:
                                point["label"] = label
                            points.append(point)
                        self._cycle_runs[mode] = points[-EXECUTION_RUNS_MAX:]
        except Exception:
            pass

    def _save_cycle_runs(self):
        try:
            payload = {mode: self._cycle_runs.get(mode, []) for mode in ROBOT_MODES}
            self.cycle_runs_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _infer_cycle_result(msg: str) -> str:
        lowered = (msg or "").lower()
        if "falha" in lowered or "erro" in lowered:
            return "erro"
        return "sucesso"

    def _reset_cycle_state(self, mode: str, started_at: str | None = None):
        now_iso = started_at or datetime.now().isoformat(timespec="seconds")
        self._cycle_state[mode] = {
            "started_at": now_iso,
            "last_progress": 0,
            "last_cycle_recorded_at": None,
        }
        self._runtime_ui[mode]["cycle_started_at"] = now_iso

    def _enter_cycle_idle(self, mode: str) -> None:
        """Espera entre ciclos não conta como ciclo ativo."""
        state = self._cycle_state.setdefault(
            mode,
            {"started_at": None, "last_progress": 0, "last_cycle_recorded_at": None},
        )
        state["started_at"] = None
        self._runtime_ui[mode]["cycle_started_at"] = None

    def _maybe_start_cycle_from_progress(self, mode: str, pct: int, now_iso: str) -> None:
        if pct >= 100:
            return
        state = self._cycle_state.setdefault(
            mode,
            {"started_at": None, "last_progress": 0, "last_cycle_recorded_at": None},
        )
        if state.get("started_at") is not None:
            return
        state["started_at"] = now_iso
        self._runtime_ui[mode]["cycle_started_at"] = now_iso

    def _append_cycle_run(
        self,
        mode: str,
        duration: float,
        result: str,
        label: str,
        ended_at: str,
    ):
        if duration < 0:
            return

        point: dict = {
            "ended_at": str(ended_at),
            "duration_seconds": round(duration, 1),
            "result": str(result or "").strip() or "desconhecido",
        }
        clean_label = str(label or "").strip()
        if clean_label:
            point["label"] = clean_label

        runs = self._cycle_runs.setdefault(mode, [])
        if runs and runs[-1].get("ended_at") == point["ended_at"]:
            runs[-1] = point
        else:
            runs.append(point)
        if len(runs) > EXECUTION_RUNS_MAX:
            self._cycle_runs[mode] = runs[-EXECUTION_RUNS_MAX:]
        self._save_cycle_runs()

    def _record_cycle_from_progress(self, mode: str, msg: str, now_iso: str):
        state = self._cycle_state.setdefault(
            mode,
            {"started_at": None, "last_progress": 0, "last_cycle_recorded_at": None},
        )
        started_at = state.get("started_at")
        if not started_at:
            return

        now = datetime.fromisoformat(now_iso)
        started = datetime.fromisoformat(str(started_at))
        duration = (now - started).total_seconds()

        last_recorded = state.get("last_cycle_recorded_at")
        if last_recorded:
            try:
                last_dt = datetime.fromisoformat(str(last_recorded))
                if (now - last_dt).total_seconds() < CYCLE_RUNS_DEDUP_SECONDS:
                    self._enter_cycle_idle(mode)
                    return
            except ValueError:
                pass

        result = self._infer_cycle_result(msg)
        self._append_cycle_run(mode, duration, result, msg, now_iso)
        state["last_cycle_recorded_at"] = now_iso
        self._enter_cycle_idle(mode)

    def _maybe_append_interrupted_cycle(self, mode: str, ended_at: datetime, exit_code: int | None):
        state = self._cycle_state.get(mode, {})
        started_at = state.get("started_at")
        if not started_at:
            return

        if state.get("last_progress", 0) >= 100:
            return

        try:
            started = datetime.fromisoformat(str(started_at))
        except ValueError:
            return

        duration = (ended_at - started).total_seconds()
        if duration <= 5:
            return

        last_recorded = state.get("last_cycle_recorded_at")
        if last_recorded:
            try:
                last_dt = datetime.fromisoformat(str(last_recorded))
                if (ended_at - last_dt).total_seconds() < CYCLE_RUNS_DEDUP_SECONDS:
                    return
            except ValueError:
                pass

        if self._stop_requested.get(mode):
            result = "interrompido"
        elif exit_code == 0:
            return
        else:
            result = "erro"

        ended_iso = ended_at.isoformat(timespec="seconds")
        self._append_cycle_run(mode, duration, result, "Ciclo interrompido", ended_iso)
        state["last_cycle_recorded_at"] = ended_iso

    def execution_trends(self, limite: int = EXECUTION_RUNS_MAX, modes: list[str] | None = None) -> dict:
        limite = max(1, min(int(limite), EXECUTION_RUNS_MAX_API))
        with self._lock:
            target_modes = [m for m in (modes or ROBOT_MODES) if m in ROBOT_MODES]
            series: dict[str, dict] = {}
            for mode in target_modes:
                points = list(self._cycle_runs.get(mode, [])[-limite:])
                series[mode] = {
                    "label": MODE_LABELS.get(mode, mode),
                    "points": points,
                }
            return {"limite": limite, "series": series}

    def _save_execution_history(self):
        try:
            payload = {mode: self._execution.get(mode, {}) for mode in ROBOT_MODES}
            self.execution_history_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _write_pid(self, mode: str, pid: int):
        try:
            self._pid_files[mode].write_text(str(pid), encoding="utf-8")
        except Exception:
            pass

    def _read_pid(self, mode: str) -> int | None:
        try:
            raw = self._pid_files[mode].read_text(encoding="utf-8").strip()
            return int(raw) if raw else None
        except Exception:
            return None

    def _clear_pid(self, mode: str):
        try:
            pid_file = self._pid_files[mode]
            if pid_file.exists():
                pid_file.unlink()
        except Exception:
            pass

    def _is_pid_running(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                out = (result.stdout or "").strip().lower()
                if not out:
                    return False
                if "nenhuma tarefa" in out or "no tasks are running" in out:
                    return False
                return str(pid) in out
            except Exception:
                return False

        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _mode_runtime(self, mode: str) -> tuple[bool, int | None]:
        proc = self._processes.get(mode)
        if proc and proc.poll() is None:
            return True, proc.pid

        pid = self._read_pid(mode)
        if pid and self._is_pid_running(pid):
            return True, pid

        self._processes.pop(mode, None)
        self._clear_pid(mode)
        return False, None

    def register_production_saved_callback(self, callback: Callable[[str], None]) -> None:
        self._production_saved_callbacks.append(callback)

    def register_production_ged_irregularidade_saved_callback(
        self, callback: Callable[[str], None]
    ) -> None:
        self._production_ged_irregularidade_saved_callbacks.append(callback)

    def register_rotina_bruto_saved_callback(self, callback: Callable[[str, str], None]) -> None:
        self._rotina_bruto_saved_callbacks.append(callback)

    def register_monitor_eventos_saved_callback(self, callback: Callable[[str], None]) -> None:
        self._monitor_eventos_saved_callbacks.append(callback)

    def register_replicacao_d1_saved_callback(self, callback: Callable[[str, str], None]) -> None:
        self._replicacao_d1_saved_callbacks.append(callback)

    def register_replicacao_d1_replicados_saved_callback(self, callback: Callable[[str], None]) -> None:
        self._replicacao_d1_replicados_saved_callbacks.append(callback)

    def register_produtividade_case_saved_callback(self, callback: Callable[[str, str], None]) -> None:
        self._produtividade_case_saved_callbacks.append(callback)

    def register_prioridades_nh_saved_callback(self, callback: Callable[[str], None]) -> None:
        self._prioridades_nh_saved_callbacks.append(callback)

    def _export_prioridades_nh_list(self) -> Path:
        """Exporta HierarchicalLevel ativos para JSON consumido pelo subprocesso do bot."""
        try:
            from apps.escala_flex.models import HierarchicalLevel
        except Exception as exc:
            raise RuntimeError(
                "Não foi possível carregar HierarchicalLevel (Django). "
                "Execute o robô via portal Automações."
            ) from exc

        items = []
        for row in HierarchicalLevel.objects.filter(active=True).order_by("name"):
            items.append(
                {
                    "id": str(row.id),
                    "name": row.name,
                    "sharepoint_id": row.sharepoint_id,
                }
            )
        out_dir = self.config_dir / "prioridades_nh"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "nh_list.json"
        path.write_text(
            json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"), "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def _notify_production_saved(self, file_path: str) -> None:
        key = str(Path(file_path).resolve()) if file_path else ""
        if key:
            now = time.monotonic()
            # Limpa entradas antigas
            stale = [k for k, ts in _RECENT_PROD_SYNC_PATHS.items() if now - ts > _RECENT_PROD_SYNC_TTL_S]
            for k in stale:
                _RECENT_PROD_SYNC_PATHS.pop(k, None)
            last = _RECENT_PROD_SYNC_PATHS.get(key)
            if last is not None and now - last < _RECENT_PROD_SYNC_TTL_S:
                return
            _RECENT_PROD_SYNC_PATHS[key] = now

        for callback in list(self._production_saved_callbacks):
            try:
                callback(file_path)
            except Exception as exc:
                log.exception("production saved callback failed")
                self._append_log("production", f"[produtividade-sync-callback-error] {exc}", notify=False)

    def _notify_production_ged_irregularidade_saved(
        self,
        file_path: str,
        *,
        raise_callback_errors: bool = False,
    ) -> None:
        callbacks = list(self._production_ged_irregularidade_saved_callbacks)
        if raise_callback_errors and not callbacks:
            raise RuntimeError("nenhum callback de sync GED registrado")

        errors: list[Exception] = []
        for callback in callbacks:
            try:
                callback(file_path)
            except Exception as exc:
                log.exception("production ged irregularidade saved callback failed")
                self._append_log(
                    "production",
                    f"[reinspecao-ged-sync-callback-error] {exc}",
                    notify=False,
                )
                errors.append(exc)

        if raise_callback_errors and errors:
            raise RuntimeError(
                f"{len(errors)} callback(s) de sync GED falharam"
            ) from errors[0]

    def _drain_bot_sync_drops(self) -> int:
        """Processa markers gravados quando o stdout do bot quebra (Errno 22)."""
        try:
            from app.infrastructure.bot_sync_drop import (
                DOMAIN_MONITOR_EVENTOS,
                DOMAIN_PRODUTIVIDADE,
                DOMAIN_REINSPECAO_GED,
                drain_sync_drops,
            )
        except Exception as exc:
            log.debug("bot_sync_drop indisponível: %s", exc)
            return 0

        handlers = {
            DOMAIN_PRODUTIVIDADE: self._notify_production_saved,
            DOMAIN_MONITOR_EVENTOS: self._notify_monitor_eventos_saved,
            DOMAIN_REINSPECAO_GED: lambda path: self._notify_production_ged_irregularidade_saved(
                path,
                raise_callback_errors=True,
            ),
        }
        try:
            n = drain_sync_drops(handlers)
            if n:
                self._append_log(
                    "production",
                    f"[sync-drop] {n} marker(s) processado(s) (fallback stdout)",
                    notify=False,
                )
            return n
        except Exception as exc:
            log.exception("drain sync_drop failed: %s", exc)
            return 0

    def _notify_rotina_bruto_saved(self, report_type: str, file_path: str) -> None:
        for callback in list(self._rotina_bruto_saved_callbacks):
            try:
                callback(report_type, file_path)
            except Exception as exc:
                log.exception("rotina bruto saved callback failed")
                self._append_log(
                    "rotina",
                    f"[rotina-bruto-sync-callback-error] {exc}",
                    notify=False,
                )

    def _notify_monitor_eventos_saved(self, file_path: str) -> None:
        for callback in list(self._monitor_eventos_saved_callbacks):
            try:
                callback(file_path)
            except Exception as exc:
                log.exception("monitor eventos saved callback failed")
                self._append_log(
                    "production",
                    f"[monitor-eventos-sync-callback-error] {exc}",
                    notify=False,
                )

    def _notify_replicacao_d1_saved(self, run_id: str, file_path: str) -> None:
        for callback in list(self._replicacao_d1_saved_callbacks):
            try:
                callback(run_id, file_path)
            except Exception as exc:
                log.exception("replicacao d1 saved callback failed")
                self._append_log(
                    "replicacao_auditoria_d1",
                    f"[replicacao-d1-sync-callback-error] {exc}",
                    notify=False,
                )

    def _notify_replicacao_d1_replicados_saved(self, file_path: str) -> None:
        for callback in list(self._replicacao_d1_replicados_saved_callbacks):
            try:
                callback(file_path)
            except Exception as exc:
                log.exception("replicacao d1 replicados saved callback failed")
                self._append_log(
                    "rotina",
                    f"[replicacao-d1-replicados-sync-callback-error] {exc}",
                    notify=False,
                )

    def _notify_produtividade_case_saved(self, report_type: str, file_path: str) -> None:
        for callback in list(self._produtividade_case_saved_callbacks):
            try:
                callback(report_type, file_path)
            except Exception as exc:
                log.exception("produtividade_case saved callback failed")
                self._append_log(
                    "produtividade_case",
                    f"[produtividade-case-sync-callback-error] {exc}",
                    notify=False,
                )

    def _notify_prioridades_nh_saved(self, file_path: str) -> None:
        for callback in list(self._prioridades_nh_saved_callbacks):
            try:
                callback(file_path)
            except Exception as exc:
                log.exception("prioridades_nh saved callback failed")
                self._append_log(
                    "prioridades_nh",
                    f"[prioridades-nh-sync-callback-error] {exc}",
                    notify=False,
                )

    @staticmethod
    def _strip_log_file_prefix(line: str) -> str:
        return _LOG_FILE_TS_RE.sub("", (line or "").rstrip("\r\n"))

    @staticmethod
    def _tail_file_lines(path: Path, n: int, block_size: int = 8192) -> list[str]:
        """Lê as últimas N linhas de um arquivo sem carregar o conteúdo inteiro."""
        if n <= 0 or not path.exists():
            return []
        try:
            with path.open("rb") as handler:
                handler.seek(0, os.SEEK_END)
                pos = handler.tell()
                if pos == 0:
                    return []
                chunks: list[bytes] = []
                newline_count = 0
                while pos > 0 and newline_count <= n:
                    read_size = min(block_size, pos)
                    pos -= read_size
                    handler.seek(pos)
                    chunk = handler.read(read_size)
                    chunks.append(chunk)
                    newline_count += chunk.count(b"\n")
                data = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
            lines = data.splitlines()
            return lines[-n:]
        except Exception:
            return []

    def _read_log_file_lines(self, mode: str, tail: int) -> list[str]:
        path = self._log_files.get(mode)
        if not path:
            return []
        raw = self._tail_file_lines(path, max(1, int(tail)))
        return [self._strip_log_file_prefix(line) for line in raw if line.strip()]

    def _apply_runtime_ui_from_line(
        self, mode: str, text: str, now_iso: str | None = None, *, record_cycles: bool = True
    ) -> None:
        """Atualiza runtime por eventos v1 e pelas linhas legadas (sem gravar disco)."""
        if not text:
            return
        stamp = now_iso or datetime.now().isoformat(timespec="seconds")
        if text.startswith(PLAN_EVENT_PREFIX):
            self._apply_runtime_ui_from_plan_event(mode, text, stamp)
            return
        if text.startswith("STATUS|"):
            msg = text.split("|", 1)[1].strip()
            if msg:
                self._runtime_ui[mode]["status"] = msg
            self._runtime_ui[mode]["updated_at"] = stamp
            self._runtime_ui[mode]["heartbeat_at"] = stamp
            return
        if text.startswith("PROGRESS|"):
            parts = text.split("|", 2)
            if len(parts) < 2:
                return
            try:
                pct = max(0, min(100, int(float(parts[1]))))
            except Exception:
                pct = None
            msg = parts[2].strip() if len(parts) > 2 else ""
            if pct is not None:
                if record_cycles:
                    self._maybe_start_cycle_from_progress(mode, pct, stamp)
                self._runtime_ui[mode]["progress"] = pct
                self._cycle_state.setdefault(
                    mode,
                    {"started_at": None, "last_progress": 0, "last_cycle_recorded_at": None},
                )["last_progress"] = pct
                if record_cycles and pct == 100:
                    self._record_cycle_from_progress(mode, msg, stamp)
            if msg:
                self._runtime_ui[mode]["status"] = msg
            self._runtime_ui[mode]["updated_at"] = stamp
            self._runtime_ui[mode]["heartbeat_at"] = stamp

    def _apply_runtime_ui_from_plan_event(self, mode: str, text: str, stamp: str) -> bool:
        """Aplica PLAN_EVENT v1; payload inválido permanece apenas como log bruto."""
        try:
            event = json.loads(text[len(PLAN_EVENT_PREFIX) :])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(event, dict) or event.get("version") != PLAN_EVENT_VERSION:
            return False

        runtime = self._runtime_ui[mode]
        phase = str(event.get("phase") or "").strip()[:32]
        occurred_at = str(event.get("occurred_at") or stamp).strip()[:64]
        if phase and phase != runtime.get("phase"):
            runtime["phase_started_at"] = occurred_at
        if phase:
            runtime["phase"] = phase
        phase_label = str(event.get("phase_label") or "").strip()[:120]
        if phase_label:
            runtime["phase_label"] = phase_label
        phase_state = str(event.get("state") or "").strip()[:32]
        if phase_state:
            runtime["phase_state"] = phase_state
        run_id = str(event.get("run_id") or "").strip()[:64]
        if run_id:
            runtime["run_id"] = run_id

        message = str(event.get("message") or "").strip()[:500]
        if message:
            runtime["status"] = message
        try:
            phase_progress = max(0, min(100, int(float(event.get("progress_pct")))))
        except (TypeError, ValueError):
            phase_progress = None
        if phase_progress is not None:
            runtime["phase_progress"] = phase_progress
            runtime["progress_mode"] = "determinate"
        else:
            runtime["progress_mode"] = "indeterminate"

        for key in ("current", "total", "elapsed_ms"):
            try:
                value = max(0, int(float(event.get(key))))
            except (TypeError, ValueError):
                continue
            runtime[key] = value
        runtime["phase_updated_at"] = occurred_at
        runtime["heartbeat_at"] = occurred_at
        runtime["updated_at"] = stamp
        return True

    def _rehydrate_runtime_ui_from_lines(self, mode: str, lines: list[str]) -> None:
        """Reaplica STATUS/PROGRESS; [stop] da execução atual prevalece como 'Parada manual'."""
        saw_manual_stop = False
        for text in lines:
            lower = (text or "").strip().lower()
            if lower.startswith("[start]"):
                saw_manual_stop = False
            if text.startswith("STATUS|") or text.startswith("PROGRESS|") or text.startswith(PLAN_EVENT_PREFIX):
                self._apply_runtime_ui_from_line(mode, text, record_cycles=False)
            elif self._is_manual_stop_log_line(text):
                saw_manual_stop = True
        if saw_manual_stop:
            now_iso = datetime.now().isoformat(timespec="seconds")
            self._runtime_ui[mode]["status"] = "Parada manual"
            self._runtime_ui[mode]["progress"] = 0
            self._runtime_ui[mode]["updated_at"] = now_iso

    @staticmethod
    def _is_manual_stop_log_line(text: str) -> bool:
        value = (text or "").strip().lower()
        if not value:
            return False
        if value.startswith("[stop]"):
            return True
        return "parada solicitada" in value

    def _logs_indicate_manual_stop(self, mode: str) -> bool:
        """True se a execução atual (após o último [start]) teve parada manual."""
        saw = False
        for text in list(self._logs.get(mode) or [])[-80:]:
            lower = (text or "").strip().lower()
            if lower.startswith("[start]"):
                saw = False
            elif self._is_manual_stop_log_line(text):
                saw = True
        return saw

    def _sync_runtime_status_from_execution(self, mode: str) -> None:
        """Com robô parado, o result da execução manda no texto de status (não o último STATUS|)."""
        alive, _ = self._mode_runtime(mode)
        if alive:
            return
        result = self._execution[mode].get("result")
        if result == "interrompido" or (
            result == "erro" and self._logs_indicate_manual_stop(mode)
        ):
            self._runtime_ui[mode]["status"] = "Parada manual"
            self._runtime_ui[mode]["progress"] = 0
        elif result == "sucesso":
            self._runtime_ui[mode]["status"] = "Concluído com sucesso"
            self._runtime_ui[mode]["progress"] = 100
        elif result == "erro":
            status = str(self._runtime_ui[mode].get("status") or "")
            if "parada manual" not in status.lower():
                self._runtime_ui[mode]["status"] = "Finalizado com erro"

    def _hydrate_mode_logs_from_disk(self, mode: str, tail: int = 400) -> list[str]:
        """Preenche o buffer em memória a partir do arquivo quando estiver vazio/curto."""
        disk = self._read_log_file_lines(mode, tail)
        if not disk:
            return list(self._logs[mode])[-tail:]
        if len(self._logs[mode]) < len(disk):
            self._logs[mode].clear()
            self._logs[mode].extend(disk)
        return disk[-tail:] if len(disk) >= tail else disk

    def _reconcile_stale_execution(self, mode: str, alive: bool) -> None:
        """Corrige 'em execução' persistido quando o processo já não está vivo."""
        if alive:
            return
        if self._execution[mode].get("result") != "em execução":
            return
        ended_at = datetime.now()
        started_at_raw = self._execution[mode].get("started_at")
        started_at = datetime.fromisoformat(started_at_raw) if started_at_raw else None
        duration = (ended_at - started_at).total_seconds() if started_at else None
        self._execution[mode]["ended_at"] = ended_at.isoformat(timespec="seconds")
        self._execution[mode]["duration_seconds"] = round(duration, 1) if duration else None
        self._execution[mode]["result"] = "interrompido"
        self._runtime_ui[mode]["progress"] = 0
        self._runtime_ui[mode]["status"] = "Parada manual"
        self._runtime_ui[mode]["updated_at"] = ended_at.isoformat(timespec="seconds")
        self._runtime_ui[mode]["modo_segundo_plano"] = False
        self._stop_requested[mode] = False
        self._cycle_state[mode] = {
            "started_at": None,
            "last_progress": 0,
            "last_cycle_recorded_at": None,
        }
        self._runtime_ui[mode]["cycle_started_at"] = None
        self._save_execution_history()

    def _hydrate_runtime_from_disk(self) -> None:
        """Chamado no init: logs + progresso do disco e limpa histórico órfão."""
        with self._lock:
            for mode in ROBOT_MODES:
                alive, _pid = self._mode_runtime(mode)
                lines = self._hydrate_mode_logs_from_disk(mode, 400)
                if lines and (
                    alive
                    or self._execution[mode].get("result") == "em execução"
                    or self._runtime_ui[mode].get("updated_at") is None
                ):
                    self._rehydrate_runtime_ui_from_lines(mode, lines)
                self._reconcile_stale_execution(mode, alive)
                self._sync_runtime_status_from_execution(mode)

    def _append_log(self, mode: str, line: str, *, notify: bool = True):
        text = (line or "").strip()
        if not text:
            return

        if notify and text.startswith(PRODUCTION_DETALHADO_SAVED_PREFIX):
            file_path = text.split("|", 1)[1].strip() if "|" in text else ""
            if file_path:
                self._notify_production_saved(file_path)

        if notify and text.startswith(PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX):
            file_path = text[len(PRODUCTION_GED_IRREGULARIDADE_SAVED_PREFIX) :].strip()
            if file_path:
                self._notify_production_ged_irregularidade_saved(file_path)

        if notify and text.startswith(ROTINA_BRUTO_SAVED_PREFIX):
            rest = text[len(ROTINA_BRUTO_SAVED_PREFIX) :].strip()
            parts = rest.split("|", 1)
            if len(parts) == 2:
                report_type, file_path = parts[0].strip(), parts[1].strip()
                if report_type and file_path:
                    self._notify_rotina_bruto_saved(report_type, file_path)

        if notify and text.startswith(MONITOR_EVENTOS_SAVED_PREFIX):
            file_path = text.split("|", 1)[1].strip() if "|" in text else ""
            if file_path:
                self._notify_monitor_eventos_saved(file_path)

        if notify and text.startswith(REPLICACAO_D1_SAVED_PREFIX):
            rest = text[len(REPLICACAO_D1_SAVED_PREFIX) :].strip()
            parts = rest.split("|", 1)
            if len(parts) == 2:
                run_id, file_path = parts[0].strip(), parts[1].strip()
                if run_id and file_path:
                    self._notify_replicacao_d1_saved(run_id, file_path)

        if notify and text.startswith(REPLICACAO_D1_REPLICADOS_SAVED_PREFIX):
            file_path = text[len(REPLICACAO_D1_REPLICADOS_SAVED_PREFIX) :].strip()
            if file_path:
                self._notify_replicacao_d1_replicados_saved(file_path)

        if notify and text.startswith(PRODUTIVIDADE_CASE_SAVED_PREFIX):
            rest = text[len(PRODUTIVIDADE_CASE_SAVED_PREFIX) :].strip()
            parts = rest.split("|", 1)
            if len(parts) == 2:
                report_type, file_path = parts[0].strip(), parts[1].strip()
                if report_type and file_path:
                    self._notify_produtividade_case_saved(report_type, file_path)

        if notify and text.startswith(CASE_FILA_SAVED_PREFIX):
            file_path = text[len(CASE_FILA_SAVED_PREFIX) :].strip()
            if file_path:
                self._notify_produtividade_case_saved("fila_aberta", file_path)

        if notify and text.startswith(PRIORIDADES_NH_SAVED_PREFIX):
            file_path = text[len(PRIORIDADES_NH_SAVED_PREFIX) :].strip()
            if file_path:
                self._notify_prioridades_nh_saved(file_path)

        if text.startswith(FALHAS_OUTPUT_PREFIX):
            payload_raw = text[len(FALHAS_OUTPUT_PREFIX) :].strip()
            if payload_raw:
                try:
                    payload = json.loads(payload_raw)
                    if isinstance(payload, dict):
                        with self._lock:
                            self._execution[mode]["output_paths"] = payload
                            self._save_execution_history()
                except Exception:
                    pass

        now_iso = datetime.now().isoformat(timespec="seconds")
        if text.startswith("STATUS|") or text.startswith("PROGRESS|") or text.startswith(PLAN_EVENT_PREFIX):
            with self._lock:
                # Após Parar, stdout bufferizado ainda pode emitir STATUS|/PROGRESS| —
                # não sobrescrever "Parada solicitada" / "Parada manual".
                if not self._stop_requested.get(mode):
                    self._apply_runtime_ui_from_line(mode, text, now_iso)
        elif self._is_manual_stop_log_line(text):
            # stop() já segura o lock; não reentrar em self._lock aqui.
            locked = self._lock.acquire(blocking=False)
            try:
                self._runtime_ui[mode]["status"] = "Parada solicitada"
                self._runtime_ui[mode]["progress"] = 0
                self._runtime_ui[mode]["updated_at"] = now_iso
            finally:
                if locked:
                    self._lock.release()

        self._logs[mode].append(text)
        try:
            file_path = self._log_files[mode]
            if not file_path.exists():
                file_path.touch()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with file_path.open("a", encoding="utf-8") as handler:
                handler.write(f"[{ts}] {text}\n")
        except Exception:
            pass

    def _stream_process_output(self, mode: str, proc: subprocess.Popen):
        try:
            if proc.stdout:
                for raw in proc.stdout:
                    self._append_log(mode, raw)
                    if mode == "production" and "DETALHADO_SAVED|" in (raw or ""):
                        try:
                            self._drain_bot_sync_drops()
                        except Exception:
                            pass
        except Exception as exc:
            self._append_log(mode, f"[stream-error] {exc}")
        finally:
            try:
                self._drain_bot_sync_drops()
            except Exception:
                pass
            try:
                exit_code = proc.wait(timeout=5)
            except Exception:
                exit_code = proc.poll()
            self._append_log(mode, f"[process-finished] exit={exit_code}")
            self._clear_pid(mode)
            self._finalize_execution(mode, exit_code)

    def _finalize_execution(self, mode: str, exit_code: int | None):
        with self._lock:
            ended_at = datetime.now()
            started_at_raw = self._execution[mode].get("started_at")
            started_at = datetime.fromisoformat(started_at_raw) if started_at_raw else None
            duration = (ended_at - started_at).total_seconds() if started_at else None

            if self._stop_requested.get(mode):
                result = "interrompido"
            elif exit_code == 0:
                result = "sucesso"
            else:
                result = "erro"

            self._execution[mode]["ended_at"] = ended_at.isoformat(timespec="seconds")
            self._execution[mode]["duration_seconds"] = round(duration, 1) if duration else None
            self._execution[mode]["result"] = result
            if result == "sucesso":
                self._runtime_ui[mode]["progress"] = 100
                self._runtime_ui[mode]["status"] = "Concluído com sucesso"
                if self._runtime_ui[mode].get("phase"):
                    self._runtime_ui[mode]["phase_state"] = "completed"
            elif result == "interrompido":
                self._runtime_ui[mode]["progress"] = 0
                self._runtime_ui[mode]["status"] = "Parada manual"
                if self._runtime_ui[mode].get("phase"):
                    self._runtime_ui[mode]["phase_state"] = "cancelled"
            else:
                self._runtime_ui[mode]["status"] = "Finalizado com erro"
                if self._runtime_ui[mode].get("phase"):
                    self._runtime_ui[mode]["phase_state"] = "failed"
            self._runtime_ui[mode]["updated_at"] = ended_at.isoformat(timespec="seconds")
            self._runtime_ui[mode]["modo_segundo_plano"] = False
            self._stop_requested[mode] = False
            self._maybe_append_interrupted_cycle(mode, ended_at, exit_code)
            self._cycle_state[mode] = {
                "started_at": None,
                "last_progress": 0,
                "last_cycle_recorded_at": None,
            }
            self._runtime_ui[mode]["cycle_started_at"] = None
            self._save_execution_history()

    def status(self) -> dict:
        # Fora do lock: callbacks de sync podem enfileirar jobs Django.
        try:
            self._drain_bot_sync_drops()
        except Exception:
            pass
        with self._lock:
            payload = {}
            for mode in ROBOT_MODES:
                alive, pid = self._mode_runtime(mode)
                # Worker novo / reentrada: rehidratar progresso e limpar "em execução" órfão
                if alive and self._runtime_ui[mode].get("updated_at") is None:
                    lines = self._hydrate_mode_logs_from_disk(mode, 400)
                    if lines:
                        self._rehydrate_runtime_ui_from_lines(mode, lines)
                elif not alive and self._runtime_ui[mode].get("updated_at") is None:
                    lines = self._hydrate_mode_logs_from_disk(mode, 400)
                    if lines:
                        self._rehydrate_runtime_ui_from_lines(mode, lines)
                self._reconcile_stale_execution(mode, alive)
                self._sync_runtime_status_from_execution(mode)
                payload[mode] = {
                    "running": alive,
                    "pid": pid if alive else None,
                    "execution": self._execution[mode],
                    "runtime": self._runtime_ui.get(
                        mode, {"progress": 0, "status": "-", "updated_at": None}
                    ),
                }
            return payload

    def logs(self, mode: str | None = None, tail: int = 80) -> dict:
        """Retorna as últimas linhas — prioriza arquivo em disco (sobrevive a restart/outro worker)."""
        tail = max(1, min(int(tail), 400))
        modes = [mode] if mode else list(ROBOT_MODES)
        result: dict[str, list[str]] = {}
        with self._lock:
            for name in modes:
                if name not in ROBOT_MODES:
                    continue
                disk = self._read_log_file_lines(name, tail)
                if disk:
                    if not self._logs[name]:
                        self._logs[name].extend(disk)
                    result[name] = disk
                else:
                    result[name] = list(self._logs[name])[-tail:]
        return result

    def clear_logs(self, mode: str) -> tuple[bool, str]:
        if mode not in ROBOT_MODES:
            return False, "Modo inválido"
        with self._lock:
            self._logs[mode].clear()
            try:
                self._log_files[mode].write_text("", encoding="utf-8")
                return True, f"Logs de '{mode}' foram limpos"
            except Exception as exc:
                return False, f"Falha ao limpar logs de '{mode}': {exc}"

    def log_file_path(self, mode: str) -> Path | None:
        if mode not in ROBOT_MODES:
            return None
        file_path = self._log_files[mode]
        if not file_path.exists():
            file_path.touch()
        return file_path

    def open_folder(self, folder_path: str) -> tuple[bool, str]:
        """Abre pasta no Explorer da máquina onde o serviço de automações roda."""
        raw = str(folder_path or "").strip().strip('"')
        if not raw:
            return False, "Caminho vazio"
        target = Path(raw).expanduser()
        try:
            target = target.resolve()
        except Exception:
            target = Path(raw)
        if not target.is_dir():
            return False, f"Pasta não encontrada: {target}"
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return True, f"Pasta aberta: {target}"
        except Exception as exc:
            return False, f"Não foi possível abrir a pasta: {exc}"

    def _kill_pid(self, pid: int):
        if pid <= 0:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        try:
            os.kill(pid, 9)
        except Exception:
            pass

    def _clear_credential_env(self, env: dict):
        for key in (
            "NIVEL_USER",
            "NIVEL_PASS",
            "MONITOR_USER",
            "MONITOR_PASS",
            "OKTA_USER",
            "OKTA_PASS",
            "ROBOT_USER",
            "ROBOT_PASS",
        ):
            env.pop(key, None)

    @staticmethod
    def _parse_yyyy_mm_dd(raw_value: str) -> date | None:
        value = str(raw_value or "").strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _validate_rotina_period(
        self,
        rotina_data_inicio: str,
        rotina_data_fim: str,
    ) -> tuple[bool, str, date | None, date | None]:
        today = date.today()
        min_allowed = today - timedelta(days=60)

        start_raw = str(rotina_data_inicio or "").strip()
        end_raw = str(rotina_data_fim or "").strip()

        if not start_raw and not end_raw:
            return True, "", today, today

        if not start_raw or not end_raw:
            return False, "Informe data inicial e final para executar a rotina por período", None, None

        start_date = self._parse_yyyy_mm_dd(start_raw)
        end_date = self._parse_yyyy_mm_dd(end_raw)
        if not start_date or not end_date:
            return False, "Período inválido. Use datas no formato YYYY-MM-DD", None, None

        if start_date > end_date:
            return False, "A data inicial não pode ser maior que a data final", None, None
        if end_date > today:
            return False, "A data final não pode ser maior que hoje", None, None

        return True, "", start_date, end_date

    @staticmethod
    def _validate_produtividade_case_deps() -> tuple[bool, str]:
        try:
            from app.bots.produtividade_case.mongo import validate_docdb_connection
        except ImportError as exc:
            return False, f"Módulo produtividade_case indisponível: {exc}"
        return validate_docdb_connection()

    @staticmethod
    def _validate_falhas_criticas_deps() -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return (
                False,
                "Dependência 'playwright' não instalada no Python dos robôs. "
                "Execute: pip install -e ../automacoes && playwright install msedge",
            )
        return True, ""

    def start(
        self,
        mode: str,
        matricula: str = "",
        senha: str = "",
        executar_imediatamente: bool = False,
        rotina_data_inicio: str = "",
        rotina_data_fim: str = "",
        robot_config: dict | None = None,
        require_okta_validation: bool = True,
        modo_segundo_plano: bool = False,
        okta_session_id: str | None = None,
    ) -> tuple[bool, str]:
        if mode not in ROBOT_MODES:
            return False, "Modo inválido"

        if mode == "controle_sla":
            return (
                False,
                "Controle de SLA é gerenciado pelo poller do portal. Use Iniciar na API/UI de Automações.",
            )

        requires_credentials = mode not in ("tray_ui", "falhas_criticas", "produtividade_case")
        if requires_credentials and (not matricula or not senha):
            return False, "Informe matrícula e senha no painel para executar o robô"

        if requires_credentials and mode != "ged" and require_okta_validation:
            sid = self._normalize_okta_session_id(okta_session_id) or "default"
            with self._lock:
                _, cred_state = self._ensure_okta_session(sid)
                credentials_ok = bool(cred_state.get("validated"))
                same_credentials = cred_state.get("fingerprint") == self._credentials_fingerprint(
                    matricula, senha
                )
                if not credentials_ok or not same_credentials:
                    return False, "Credenciais não validadas no Okta. Clique em 'Validar credenciais' antes de iniciar."

        if mode == "ged":
            from app.core.credentials import get_ged_credentials

            ged_user, ged_pass = get_ged_credentials()
            if not ged_user or not ged_pass:
                return (
                    False,
                    "Credenciais GED ausentes. Configure GED_USER e GED_PASS em backend/.env "
                    "(ou automacoes/.env) e reinicie o backend.",
                )

        self.runner_cwd, self.runner_file, self.runner_module = self._resolve_runner_path()
        if not self.runner_cwd.exists() or (not self.runner_module and not self.runner_file.exists()):
            return False, "Runner de robôs não encontrado"

        periodo_inicio = None
        periodo_fim = None
        if mode == "rotina" and executar_imediatamente:
            valid_period, period_message, periodo_inicio, periodo_fim = self._validate_rotina_period(
                rotina_data_inicio=rotina_data_inicio,
                rotina_data_fim=rotina_data_fim,
            )
            if not valid_period:
                return False, period_message

        merged_for_validation = self._normalize_robot_config(
            robot_config if isinstance(robot_config, dict) else self._robot_configs.get(mode),
            mode,
        )
        if mode == "replicacao_auditoria":
            escala_ok, escala_msg = self._validar_replicacao_aud_escala(merged_for_validation)
            if not escala_ok:
                return False, escala_msg
        if mode == "replicacao_auditoria_d1":
            escala_ok, escala_msg = self._validar_replicacao_aud_d1_escala(merged_for_validation)
            if not escala_ok:
                return False, escala_msg
            agendamento_ativo = bool(merged_for_validation.get("agendamento_ativo"))
            if not agendamento_ativo:
                try:
                    from apps.replicacao_d1.config_models import ReplicacaoD1ConfigGeral

                    agendamento_ativo = bool(ReplicacaoD1ConfigGeral.get_solo().agendamento_ativo)
                except Exception:
                    agendamento_ativo = False
            if not agendamento_ativo and not bool(merged_for_validation.get("apenas_planejamento")):
                try:
                    from app.bots.replicacao_d1_db_bridge import (
                        ensure_plan_approved_db,
                        latest_plan_run_id_db,
                    )

                    approved_run_id = str(merged_for_validation.get("run_id") or "").strip()
                    if not approved_run_id:
                        approved_run_id = str(latest_plan_run_id_db(approved_only=True) or "")
                    ensure_plan_approved_db(approved_run_id)
                    robot_config = dict(robot_config or {})
                    robot_config["run_id"] = approved_run_id
                    merged_for_validation["run_id"] = approved_run_id
                except Exception as exc:
                    return False, (
                        f"Execução D-1 aguardando aprovação do plano: {exc}. "
                        "Abra Configuração > Validar plano."
                    )
        if mode == "falhas_criticas":
            deps_ok, deps_msg = self._validate_falhas_criticas_deps()
            if not deps_ok:
                return False, deps_msg
            excel_ok, excel_msg = self._validate_falhas_excel_path(
                str(merged_for_validation.get("falhas_excel_path", ""))
            )
            if not excel_ok:
                return False, excel_msg
        if mode == "produtividade_case":
            deps_ok, deps_msg = self._validate_produtividade_case_deps()
            if not deps_ok:
                return False, deps_msg
            tarefas_cfg = merged_for_validation.get("tarefas") or []
            if not isinstance(tarefas_cfg, list) or not tarefas_cfg:
                return False, "Selecione ao menos uma tarefa em Produtividade Case Manager"
        if mode == "production" and merged_for_validation.get("executar_produtividade_case"):
            deps_ok, deps_msg = self._validate_produtividade_case_deps()
            if not deps_ok:
                return False, deps_msg
            case_cfg = self._normalize_robot_config(
                self._robot_configs.get("produtividade_case"), "produtividade_case"
            )
            tarefas_cfg = case_cfg.get("tarefas") or []
            if not isinstance(tarefas_cfg, list) or not tarefas_cfg:
                return (
                    False,
                    "Habilite tarefas em Produtividade Case Manager antes de marcar "
                    "'Também extrair Case Manager' na Produção (H/H).",
                )
            case_running, _ = self._mode_runtime("produtividade_case")
            if case_running:
                return (
                    False,
                    "Produtividade Case Manager já está em execução. Pare-o ou desmarque "
                    "'Também extrair Case Manager'.",
                )

        with self._lock:
            running, _ = self._mode_runtime(mode)
            if running:
                return False, f"Robô '{mode}' já está em execução"

            merged_config = self._normalize_robot_config(self._robot_configs.get(mode), mode)
            if isinstance(robot_config, dict):
                merged_config = self._normalize_robot_config(robot_config, mode)
                self._robot_configs[mode] = merged_config
                self._save_robot_configs()

            if mode == "falhas_criticas" and modo_segundo_plano:
                merged_config = dict(merged_config)
                merged_config["modo_segundo_plano"] = True

            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            self._clear_credential_env(env)
            env["NIVEL_USER"] = matricula
            env["MONITOR_USER"] = matricula
            env["OKTA_USER"] = matricula
            env["ROBOT_USER"] = matricula
            env["NIVEL_PASS"] = senha
            env["MONITOR_PASS"] = senha
            env["OKTA_PASS"] = senha
            env["ROBOT_PASS"] = senha

            output_dir = str(merged_config.get("output_dir", "")).strip()
            if output_dir:
                env["ROBOT_OUTPUT_DIR"] = output_dir
                env["GED_OUTPUT_DIR"] = output_dir

            headless_flag = "1" if bool(merged_config.get("headless", False)) else "0"
            env["ROBOT_HEADLESS"] = headless_flag
            env["GED_HEADLESS"] = headless_flag

            max_workers = merged_config.get("max_workers")
            if max_workers is not None:
                env["ROBOT_MAX_WORKERS"] = str(max_workers)
                env["GED_MAX_WORKERS"] = str(max_workers)
            else:
                env.pop("ROBOT_MAX_WORKERS", None)
                env.pop("GED_MAX_WORKERS", None)

            if mode == "rotina":
                env["ROTINA_EXECUTAR_IMEDIATAMENTE"] = "1" if executar_imediatamente else "0"
                if executar_imediatamente and periodo_inicio and periodo_fim:
                    env["ROTINA_DATA_INICIO"] = periodo_inicio.strftime("%Y-%m-%d")
                    env["ROTINA_DATA_FIM"] = periodo_fim.strftime("%Y-%m-%d")
                    env["ROTINA_DATA_EXECUCAO"] = periodo_inicio.strftime("%Y-%m-%d")
                else:
                    env["ROTINA_DATA_INICIO"] = ""
                    env["ROTINA_DATA_FIM"] = ""
                    env["ROTINA_DATA_EXECUCAO"] = ""

            # Passar listas de 'tarefas' para o runner via variável de ambiente
            tarefas_list = merged_config.get("tarefas", []) or []
            if isinstance(tarefas_list, list):
                if tarefas_list:
                    env["ROBOT_TAREFAS"] = ",".join(
                        [str(x).strip().lower() for x in tarefas_list if str(x).strip()]
                    )
                else:
                    # Lista vazia salva explicitamente = nenhuma tarefa
                    env["ROBOT_TAREFAS"] = ""
            else:
                env.pop("ROBOT_TAREFAS", None)

            if mode == "ged":
                env["GED_EXECUCAO_IMEDIATA"] = str(merged_config.get("ged_execucao_imediata", "")).strip().lower()

            if mode == "production":
                env["PRODUCTION_TEMPO_ESPERA_MINUTOS"] = str(
                    merged_config.get(
                        "tempo_espera_minutos",
                        PRODUCTION_CONFIG_DEFAULT["tempo_espera_minutos"],
                    )
                )
                env["PRODUCTION_DIAS_DOWNLOAD_BRFLOW"] = str(
                    merged_config.get(
                        "dias_download_brflow",
                        PRODUCTION_CONFIG_DEFAULT["dias_download_brflow"],
                    )
                )
                production_flags = (
                    ("executar_confer", "PRODUCTION_EXECUTAR_CONFER"),
                    ("executar_brflow", "PRODUCTION_EXECUTAR_BRFLOW"),
                    ("baixar_monitor_com_producao", "PRODUCTION_BAIXAR_MONITOR"),
                    ("baixar_log_eventos_com_producao", "PRODUCTION_BAIXAR_LOG_EVENTOS"),
                    ("executar_ged_irregularidade", "PRODUCTION_EXECUTAR_GED_IRREGULARIDADE"),
                    ("executar_produtividade_case", "PRODUCTION_EXECUTAR_PRODUTIVIDADE_CASE"),
                )
                for cfg_key, env_key in production_flags:
                    enabled = bool(merged_config.get(cfg_key, PRODUCTION_CONFIG_DEFAULT.get(cfg_key, True)))
                    env[env_key] = "1" if enabled else "0"
                run_case = bool(merged_config.get("executar_produtividade_case"))
                if run_case:
                    case_cfg = self._normalize_robot_config(
                        self._robot_configs.get("produtividade_case"), "produtividade_case"
                    )
                    case_settings = {
                        key: case_cfg.get(key) for key in PRODUTIVIDADE_CASE_CONFIG_DEFAULT
                    }
                    case_settings["tarefas"] = list(case_cfg.get("tarefas") or [])
                    env["PRODUTIVIDADE_CASE_SETTINGS"] = json.dumps(
                        case_settings, ensure_ascii=False
                    )

            if mode == "replicacao_auditoria":
                env["REPLICACAO_APENAS_PLANEJAMENTO"] = (
                    "1" if bool(merged_config.get("apenas_planejamento")) else "0"
                )
                env["REPLICACAO_RUN_ID"] = str(merged_config.get("run_id", "")).strip()
                replicacao_settings = {
                    key: merged_config.get(key)
                    for key in REPLICACAO_AUD_CONFIG_DEFAULT
                }
                replicacao_settings["headless"] = bool(merged_config.get("headless", False))
                env["REPLICACAO_AUD_SETTINGS"] = json.dumps(replicacao_settings, ensure_ascii=False)

            if mode == "replicacao_auditoria_d1":
                from app.bots.replicacao_aud_d1_planning import _ensure_d1_settings

                env["REPLICACAO_D1_APENAS_PLANEJAMENTO"] = (
                    "1" if bool(merged_config.get("apenas_planejamento")) else "0"
                )
                env["REPLICACAO_D1_RUN_ID"] = str(merged_config.get("run_id", "")).strip()
                replicacao_d1_settings = _ensure_d1_settings(
                    {key: merged_config.get(key) for key in REPLICACAO_AUD_D1_CONFIG_DEFAULT}
                )
                replicacao_d1_settings = RobotProcessManager._inject_replicacao_d1_db_snapshot(
                    replicacao_d1_settings
                )
                from app.bots.replicacao_d1_db_bridge import (
                    resolve_agendamento_settings,
                    settings_for_d1_env_json,
                )

                replicacao_d1_settings = resolve_agendamento_settings(replicacao_d1_settings)
                replicacao_d1_settings["headless"] = bool(merged_config.get("headless", False))

                env["REPLICACAO_AUD_D1_SETTINGS"] = json.dumps(
                    settings_for_d1_env_json(replicacao_d1_settings),
                    ensure_ascii=False,
                )
                backend_root = self.project_root.parent / "backend"
                if backend_root.is_dir():
                    env["PPLID_BACKEND_DIR"] = str(backend_root)

            if mode == "tray_ui":
                RobotProcessManager._apply_tray_ui_env(env, merged_config)
            if mode == "falhas_criticas":
                exec_imediata_falhas = bool(
                    executar_imediatamente or merged_config.get("executar_imediatamente")
                )
                env["FALHAS_CRITICAS_EXECUTAR_IMEDIATAMENTE"] = "1" if exec_imediata_falhas else "0"
                backend_root = self.project_root.parent / "backend"
                if backend_root.is_dir():
                    env["REPORT_FALHAS_BACKEND_PATH"] = str(backend_root)
                falhas_settings = {
                    key: merged_config.get(key)
                    for key in FALHAS_CRITICAS_CONFIG_DEFAULT
                }
                falhas_settings["headless"] = bool(merged_config.get("headless", False))
                falhas_settings["executar_imediatamente"] = exec_imediata_falhas
                falhas_settings["output_dir"] = str(merged_config.get("output_dir", "")).strip()
                env["FALHAS_CRITICAS_SETTINGS"] = json.dumps(falhas_settings, ensure_ascii=False)
                excel_path = str(merged_config.get("falhas_excel_path", "")).strip()
                if excel_path:
                    env["FALHAS_CRITICAS_EXCEL_PATH"] = excel_path
                    env["REPORT_EXCEL_PATH"] = excel_path
                refresh_flag = "1" if bool(merged_config.get("falhas_refresh_queries", True)) else "0"
                env["FALHAS_CRITICAS_REFRESH_QUERIES"] = refresh_flag

            if mode == "prioridades_nh":
                exec_imediata_nh = bool(
                    executar_imediatamente or merged_config.get("executar_imediatamente")
                )
                env["PRIORIDADES_NH_EXECUTAR_IMEDIATAMENTE"] = "1" if exec_imediata_nh else "0"
                nh_list_path = self._export_prioridades_nh_list()
                env["PRIORIDADES_NH_LIST_PATH"] = str(nh_list_path)
                nh_settings = {
                    key: merged_config.get(key) for key in PRIORIDADES_NH_CONFIG_DEFAULT
                }
                nh_settings["headless"] = bool(merged_config.get("headless", False))
                nh_settings["executar_imediatamente"] = exec_imediata_nh
                nh_settings["output_dir"] = str(merged_config.get("output_dir", "")).strip()
                nh_settings["nh_list_path"] = str(nh_list_path)
                env["PRIORIDADES_NH_SETTINGS"] = json.dumps(nh_settings, ensure_ascii=False)

            if mode == "produtividade_case":
                case_settings = {
                    key: merged_config.get(key)
                    for key in PRODUTIVIDADE_CASE_CONFIG_DEFAULT
                }
                case_settings["tarefas"] = list(merged_config.get("tarefas") or [])
                env["PRODUTIVIDADE_CASE_SETTINGS"] = json.dumps(case_settings, ensure_ascii=False)

            self._logs[mode].clear()
            self._stop_requested[mode] = False
            self._execution[mode]["started_at"] = datetime.now().isoformat(timespec="seconds")
            self._execution[mode]["ended_at"] = None
            self._execution[mode]["duration_seconds"] = None
            self._execution[mode]["result"] = "em execução"
            if mode == "falhas_criticas":
                self._execution[mode]["output_paths"] = None
            self._save_execution_history()
            self._append_log(mode, f"[start] iniciando robô '{mode}'")
            self._append_log(mode, f"[runner] {'-m ' + self.runner_module if self.runner_module else self.runner_file}")
            self._append_log(mode, f"[config] {json.dumps(merged_config, ensure_ascii=False)}")
            self._runtime_ui[mode]["progress"] = 0
            self._runtime_ui[mode]["status"] = "Inicializando"
            self._runtime_ui[mode]["updated_at"] = datetime.now().isoformat(timespec="seconds")
            for key in (
                "run_id",
                "phase",
                "phase_label",
                "phase_state",
                "phase_started_at",
                "phase_updated_at",
                "phase_progress",
                "current",
                "total",
                "elapsed_ms",
                "heartbeat_at",
                "progress_mode",
            ):
                self._runtime_ui[mode].pop(key, None)
            self._runtime_ui[mode]["modo_segundo_plano"] = bool(
                merged_config.get("modo_segundo_plano") if mode == "falhas_criticas" else False
            )
            self._reset_cycle_state(mode, self._execution[mode]["started_at"])
            if mode == "rotina" and executar_imediatamente and periodo_inicio and periodo_fim:
                self._append_log(
                    mode,
                    f"[periodo] execução imediata de {periodo_inicio.strftime('%d/%m/%Y')} até {periodo_fim.strftime('%d/%m/%Y')}",
                )

            try:
                # Em Windows, desacopla do Waitress (novo process group + detached)
                # para stop_env/kill na árvore HTTP não derrubar robôs em produção.
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform == "win32":
                    creationflags |= getattr(
                        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
                    )
                    creationflags |= 0x00000008  # DETACHED_PROCESS
                proc = subprocess.Popen(
                    self._runner_command(mode),
                    cwd=str(self.runner_cwd),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                    start_new_session=(sys.platform != "win32"),
                    close_fds=(sys.platform != "win32"),
                )
                self._processes[mode] = proc
                self._write_pid(mode, proc.pid)
                threading.Thread(target=self._stream_process_output, args=(mode, proc), daemon=True).start()
                return True, f"Robô '{mode}' iniciado"
            except Exception as exc:
                return False, f"Erro ao iniciar robô '{mode}': {exc}"

    def stop(self, mode: str) -> tuple[bool, str]:
        if mode not in ROBOT_MODES:
            return False, "Modo inválido"

        with self._lock:
            running, pid = self._mode_runtime(mode)
            if not running or not pid:
                self._processes.pop(mode, None)
                return False, f"Robô '{mode}' não está em execução"

            self._stop_requested[mode] = True
            self._append_log(mode, f"[stop] parada solicitada para '{mode}'")
            self._runtime_ui[mode]["status"] = "Parada solicitada"
            self._runtime_ui[mode]["progress"] = 0
            self._runtime_ui[mode]["updated_at"] = datetime.now().isoformat(timespec="seconds")
            try:
                self._kill_pid(pid)
                self._processes.pop(mode, None)
                self._clear_pid(mode)
                return True, f"Robô '{mode}' interrompido"
            except Exception as exc:
                return False, f"Erro ao parar robô '{mode}': {exc}"

    def stop_all(self) -> dict:
        payload = {}
        for mode in ROBOT_MODES:
            ok, message = self.stop(mode)
            payload[mode] = {"ok": ok, "message": message}
        return payload


robot_manager = RobotProcessManager()
