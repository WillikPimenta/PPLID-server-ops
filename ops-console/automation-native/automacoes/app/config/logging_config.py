"""Centralized logging configuration for application and robots."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config.paths import LOG_LEVEL_NUM
from app.config.settings import Config


def _is_robot_record(record: logging.LogRecord) -> bool:
    try:
        name = (record.name or "").lower()
        return any(tok in name for tok in ("robot", "robots", "monitor", "monitor_excel", "nivel", "falhas"))
    except Exception:
        return False


_ROBOT_LOGGER_PREFIXES = {
    "nivel": ("robots.nivel_h", "bots.nivel_h", "nivel_h"),
    "monitor": ("robots.monitor", "bots.monitor", "monitor"),
    "excel": ("robots.monitor_excel", "bots.monitor_excel", "monitor_excel"),
    "production": ("robots.bot_production", "bots.bot_production", "bot_production", "robots.production"),
    "rotina": ("robots.bot_rotina", "bots.bot_rotina", "bot_rotina", "robots.rotina"),
    "confer": ("robots.bot_confer", "bots.bot_conferMonitor", "bots.bot_confer_monitor", "bot_conferMonitor", "bot_confer_monitor", "robots.confer"),
    "replicacao_auditoria": (
        "robots.bot_replicacaoAud",
        "bots.bot_replicaçãoAud",
        "bots.bot_replicacao_aud",
        "bot_replicaçãoAud",
        "bot_replicacao_aud",
        "robots.replicacao_auditoria",
    ),
    "replicacao_auditoria_d1": (
        "robots.bot_replicacao_aud_d1",
        "bots.bot_replicacao_aud_d1",
        "bot_replicacao_aud_d1",
        "robots.replicacao_aud_d1_planning",
        "bots.replicacao_aud_d1_planning",
        "robots.replicacao_d1_db_bridge",
        "bots.replicacao_d1_db_bridge",
    ),
    "falhas_criticas": (
        "robots.bot_falhas_criticas",
        "bots.bot_falhas_criticas",
        "bot_falhas_criticas",
    ),
    "prioridades_nh": (
        "robots.prioridades_nh",
        "bots.prioridades_nh",
        "bot_prioridades_nh",
    ),
}


def _record_robot_mode(record: logging.LogRecord) -> str:
    try:
        logger_name = (record.name or "").lower()
        for mode, prefixes in _ROBOT_LOGGER_PREFIXES.items():
            for prefix in prefixes:
                if logger_name == prefix or logger_name.startswith(prefix + "."):
                    return mode
        return ""
    except Exception:
        return ""


class _RobotsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return _is_robot_record(record)


class _ApplicationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _is_robot_record(record)


class _SpecificRobotFilter(logging.Filter):
    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    def filter(self, record: logging.LogRecord) -> bool:
        return _record_robot_mode(record) == self.mode


def _add_file_handler(logger, filename: Path, handler_name: str, level: int, filter_obj=None):
    existing_names = {getattr(h, "name", None) for h in logger.handlers}
    if handler_name in existing_names:
        return

    try:
        handler = logging.FileHandler(filename, encoding="utf-8")
        handler.name = handler_name
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        if filter_obj:
            handler.addFilter(filter_obj)
        logger.addHandler(handler)
    except Exception:
        pass


def configure_logging(app_file: Path = None, robots_file: Path = None, level: int = None):
    """Configure root logging with separate application and robot log files."""
    app_file = app_file or Config.APP_LOG_FILE
    robots_file = robots_file or Config.ROBOTS_LOG_FILE
    level = level or LOG_LEVEL_NUM

    root = logging.getLogger()
    root.setLevel(level)

    _add_file_handler(root, app_file, "app_file", level, _ApplicationFilter())
    _add_file_handler(root, robots_file, "robots_file", level, _RobotsFilter())
    for mode, path in Config.ROBOT_LOG_FILES.items():
        _add_file_handler(root, path, f"robot_file_{mode}", level, _SpecificRobotFilter(mode))

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(ch)
