"""Pacote de robôs — automação Serasa/BRFlow."""

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

__all__ = [
    "nivel_h",
    "monitor",
    "monitor_excel",
    "bot_production",
    "bot_onedrive",
    "bot_tray_ui",
    "bot_rotina",
    "bot_confer_monitor",
    "bot_ged",
    "bot_replicacao_aud",
    "bot_replicacao_aud_d1",
    "bot_okta_validate",
    "replicacao_aud_planning",
    "replicacao_aud_d1_planning",
    "replicacao_aud_excel_format",
]
