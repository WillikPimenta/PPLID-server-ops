"""Lazy import do robot_manager do pacote automacoes (pip install -e ../automacoes)."""


class AutomacoesPackageNotInstalledError(RuntimeError):
    """Pacote sistematest (automacoes) nao instalado no venv do backend."""


def _ensure_automacoes_importable() -> None:
    try:
        import app  # noqa: F401
    except ModuleNotFoundError as exc:
        raise AutomacoesPackageNotInstalledError(
            "Pacote de automacoes nao instalado. Execute: pip install -e ../automacoes "
            "no venv do backend (na release/deploy, isso ocorre no build)."
        ) from exc


def get_robot_manager():
    _ensure_automacoes_importable()
    from app.services.robot_manager import robot_manager

    return robot_manager


def get_mode_labels():
    _ensure_automacoes_importable()
    from app.config.constants import MODE_LABELS, ROBOT_MODES

    return ROBOT_MODES, MODE_LABELS
