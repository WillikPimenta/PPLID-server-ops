"""Normalização da config do bot Produção (H/H)."""
from app.services.robot_manager import (
    PRODUCTION_CONFIG_DEFAULT,
    PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MAX,
    PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MIN,
    PRODUCTION_TEMPO_ESPERA_MAX_MINUTOS,
    PRODUCTION_TEMPO_ESPERA_MIN_MINUTOS,
    RobotProcessManager,
)


def test_production_config_default_tempo_espera():
    cfg = RobotProcessManager._normalize_robot_config({}, "production")
    assert cfg["tempo_espera_minutos"] == PRODUCTION_CONFIG_DEFAULT["tempo_espera_minutos"]


def test_production_config_clamp_minimo_5():
    cfg = RobotProcessManager._normalize_robot_config(
        {"tempo_espera_minutos": 2}, "production"
    )
    assert cfg["tempo_espera_minutos"] == PRODUCTION_TEMPO_ESPERA_MIN_MINUTOS


def test_production_config_clamp_maximo():
    cfg = RobotProcessManager._normalize_robot_config(
        {"tempo_espera_minutos": 999}, "production"
    )
    assert cfg["tempo_espera_minutos"] == PRODUCTION_TEMPO_ESPERA_MAX_MINUTOS


def test_production_config_valor_valido():
    cfg = RobotProcessManager._normalize_robot_config(
        {"tempo_espera_minutos": 15}, "production"
    )
    assert cfg["tempo_espera_minutos"] == 15


def test_production_config_default_dias_download_brflow():
    cfg = RobotProcessManager._normalize_robot_config({}, "production")
    assert cfg["dias_download_brflow"] == PRODUCTION_CONFIG_DEFAULT["dias_download_brflow"]


def test_production_config_dias_download_brflow_clamp():
    minimo = RobotProcessManager._normalize_robot_config(
        {"dias_download_brflow": 0}, "production"
    )
    maximo = RobotProcessManager._normalize_robot_config(
        {"dias_download_brflow": 999}, "production"
    )
    assert minimo["dias_download_brflow"] == PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MIN
    assert maximo["dias_download_brflow"] == PRODUCTION_DIAS_DOWNLOAD_BRFLOW_MAX


def test_production_config_dias_download_brflow_valido():
    cfg = RobotProcessManager._normalize_robot_config(
        {"dias_download_brflow": "7"}, "production"
    )
    assert cfg["dias_download_brflow"] == 7


def test_production_config_case_flag_default_false():
    cfg = RobotProcessManager._normalize_robot_config({}, "production")
    assert cfg["executar_produtividade_case"] is False


def test_production_config_case_flag_true():
    cfg = RobotProcessManager._normalize_robot_config(
        {"executar_produtividade_case": True}, "production"
    )
    assert cfg["executar_produtividade_case"] is True


def test_production_config_case_flag_string():
    cfg = RobotProcessManager._normalize_robot_config(
        {"executar_produtividade_case": "1"}, "production"
    )
    assert cfg["executar_produtividade_case"] is True


def test_production_config_sistemas_defaults_true():
    cfg = RobotProcessManager._normalize_robot_config({}, "production")
    assert cfg["executar_confer"] is True
    assert cfg["executar_brflow"] is True
    assert cfg["baixar_monitor_com_producao"] is True
    assert cfg["baixar_log_eventos_com_producao"] is True
    assert cfg["executar_ged_irregularidade"] is True


def test_production_config_sistemas_desligados():
    cfg = RobotProcessManager._normalize_robot_config(
        {
            "executar_confer": False,
            "executar_brflow": "0",
            "baixar_monitor_com_producao": "false",
            "baixar_log_eventos_com_producao": 0,
            "executar_ged_irregularidade": "off",
        },
        "production",
    )
    assert cfg["executar_confer"] is False
    assert cfg["executar_brflow"] is False
    assert cfg["baixar_monitor_com_producao"] is False
    assert cfg["baixar_log_eventos_com_producao"] is False
    assert cfg["executar_ged_irregularidade"] is False
