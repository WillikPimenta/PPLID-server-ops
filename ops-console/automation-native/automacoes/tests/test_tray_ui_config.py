"""Testes de normalização e env do robô tray_ui."""

import os

from app.services.robot_manager import RobotProcessManager, TRAY_UI_CONFIG_DEFAULT


def test_normalize_tray_ui_config_defaults():
    cfg = RobotProcessManager._normalize_robot_config({}, "tray_ui")
    for key, value in TRAY_UI_CONFIG_DEFAULT.items():
        assert cfg[key] == value


def test_normalize_tray_ui_config_custom():
    cfg = RobotProcessManager._normalize_robot_config(
        {
            "tray_scan_region": "50,90,50,10",
            "tray_ui_check_interval_seconds": 120,
            "onedrive_ui_sync_stuck_window_seconds": 300,
            "tray_match_scales": "0.75,1.0",
            "cisco_ui_res_dir": r"C:\Cisco\res",
        },
        "tray_ui",
    )
    assert cfg["tray_scan_region"] == "50,90,50,10"
    assert cfg["tray_ui_check_interval_seconds"] == 120
    assert cfg["onedrive_ui_sync_stuck_window_seconds"] == 300
    assert cfg["tray_match_scales"] == "0.75,1.0"
    assert cfg["cisco_ui_res_dir"] == r"C:\Cisco\res"


def test_apply_tray_ui_env():
    env = os.environ.copy()
    cfg = RobotProcessManager._normalize_robot_config(
        {"tray_ui_check_interval_seconds": 90, "cisco_ui_res_dir": ""},
        "tray_ui",
    )
    RobotProcessManager._apply_tray_ui_env(env, cfg)
    assert env["TRAY_UI_CHECK_INTERVAL_SECONDS"] == "90"
    assert env["ONEDRIVE_UI_SYNC_STUCK_WINDOW_SECONDS"] == "180"
    assert "CISCO_UI_RES_DIR" not in env
    assert "TRAY_MATCH_SCALES" not in env
