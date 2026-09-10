"""Smoke tests: import all bot modes and Flask app without Selenium execution."""

import importlib

import pytest

from app.orchestration.robot_runner import MODE_MODULES


@pytest.mark.parametrize("mode,module_path", list(MODE_MODULES.items()))
def test_bot_mode_imports(mode, module_path):
    mod = importlib.import_module(module_path)
    assert hasattr(mod, "start"), f"{module_path} must expose start()"
    assert hasattr(mod, "set_status_callback")
    assert hasattr(mod, "set_progress_callback")


def test_create_app():
    from app import create_app

    app = create_app()
    assert app is not None
