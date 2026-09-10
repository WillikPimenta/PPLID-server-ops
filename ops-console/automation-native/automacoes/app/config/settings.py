"""Application settings and Config class."""

import logging

from app.config.paths import (
    BASE_DIR,
    DEFAULT_SHAREPOINT_USUARIOS,
    PLAN_IDF_SERASA_BOTS,
    PROJECT_ROOT,
    _ensure_directory,
)


def validate_config() -> bool:
    """Validate that required paths and dependencies exist."""
    from app.config.paths import DEFAULT_DOWNLOAD, DEFAULT_SHAREPOINT_USUARIOS, XLSX_PATH

    issues = []
    for path, desc in [(DEFAULT_SHAREPOINT_USUARIOS, "SharePoint"), (DEFAULT_DOWNLOAD, "Downloads")]:
        try:
            _ensure_directory(path, desc)
        except Exception as exc:
            issues.append(f"Cannot access {desc}: {exc}")

    if not XLSX_PATH.exists():
        pass

    if issues:
        pass

    return len(issues) == 0


class Config:
    """Central configuration holder for paths, logs and credentials."""

    BASE_DIR = BASE_DIR
    PROJECT_ROOT = PROJECT_ROOT

    SHAREPOINT_DIR = DEFAULT_SHAREPOINT_USUARIOS
    LOG_DIR = PLAN_IDF_SERASA_BOTS / "logs"

    APP_LOG_FILE = LOG_DIR / "application.log"
    ROBOTS_LOG_FILE = LOG_DIR / "robots.log"
    ROBOT_LOG_FILES = {
        "nivel": LOG_DIR / "robot_nivel.log",
        "monitor": LOG_DIR / "robot_monitor.log",
        "excel": LOG_DIR / "robot_excel.log",
        "production": LOG_DIR / "robot_production.log",
        "rotina": LOG_DIR / "robot_rotina.log",
        "confer": LOG_DIR / "robot_confer.log",
        "replicacao_auditoria": LOG_DIR / "robot_replicacao_auditoria.log",
        "replicacao_auditoria_d1": LOG_DIR / "robot_replicacao_auditoria_d1.log",
        "falhas_criticas": LOG_DIR / "falhas_criticas.log",
    }
    LOG_FILE = APP_LOG_FILE

    USER_CREDENTIALS = {"username": "", "password": ""}

    def __init__(self) -> None:
        self._ensure_directories()

    @classmethod
    def _ensure_directories(cls) -> None:
        for directory in [cls.LOG_DIR, cls.SHAREPOINT_DIR]:
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def set_credentials(username: str, password: str) -> None:
        Config.USER_CREDENTIALS["username"] = username
        Config.USER_CREDENTIALS["password"] = password

    @staticmethod
    def get_credentials() -> tuple:
        return Config.USER_CREDENTIALS["username"], Config.USER_CREDENTIALS["password"]


Config._ensure_directories()

try:
    validate_config()
except Exception as exc:
    logging.error("Configuration validation failed: %s", exc)
