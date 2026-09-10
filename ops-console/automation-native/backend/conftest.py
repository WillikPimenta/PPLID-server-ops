"""Orquestra testes que exigem bancos isolados sem mudar o comando oficial."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


_PERFORMANCE_SETTINGS = "config.settings_performance"
_PERFORMANCE_TEST_PREFIX = "apps/auditoria/tests/test_dashboard_performance.py::"


@pytest.fixture(autouse=True)
def _isolate_process_local_test_state():
    """Alinha caches/buffers de processo à transação de cada teste Django.

    ``django.test.TestCase`` reverte o banco, mas não limpa LocMemCache,
    ``threading.local`` ou buffers de telemetria. Esses estados não podem
    carregar snapshots de uma transação revertida para o teste seguinte.
    """
    from django.core.cache import cache

    from apps.ops_monitoring.middleware import clear_metrics_buffers_for_tests
    from apps.produtividade.services.monitor_bridge import (
        clear_monitor_bridge_cache_for_tests,
    )
    from apps.qualidade_operacional.services.contestacao_metrics import (
        clear_contestacao_tipo_cache,
    )

    cache.clear()
    clear_monitor_bridge_cache_for_tests()
    clear_contestacao_tipo_cache()
    clear_metrics_buffers_for_tests()
    yield
    clear_metrics_buffers_for_tests()
    clear_monitor_bridge_cache_for_tests()
    clear_contestacao_tipo_cache()


def _configured_settings(config) -> str:
    try:
        return str(config.getoption("--ds") or "")
    except ValueError:
        return ""


def pytest_collection_modifyitems(session, config, items):
    """Retira o benchmark do processo principal; ele roda no banco dedicado ao final."""
    if config.option.collectonly or _configured_settings(config) == _PERFORMANCE_SETTINGS:
        return

    performance_items = [
        item for item in items if item.nodeid.startswith(_PERFORMANCE_TEST_PREFIX)
    ]
    if not performance_items:
        return

    performance_ids = {item.nodeid for item in performance_items}
    items[:] = [item for item in items if item.nodeid not in performance_ids]
    config._pplid_performance_nodeids = sorted(performance_ids)
    config.hook.pytest_deselected(items=performance_items)


def pytest_sessionfinish(session, exitstatus):
    """Executa os itens separados e faz sua falha participar do exit code global."""
    nodeids = getattr(session.config, "_pplid_performance_nodeids", ())
    if not nodeids:
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_sep("=", "benchmark no banco dedicado")
        sys.stdout.flush()

    command = [
        sys.executable,
        "-m",
        "pytest",
        *nodeids,
        f"--ds={_PERFORMANCE_SETTINGS}",
        "--reuse-db",
        "-q",
    ]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parent, check=False)
    if result.returncode and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    elif session.exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED and result.returncode == 0:
        # Permite executar somente o benchmark pelo comando oficial, sem --ds.
        session.exitstatus = pytest.ExitCode.OK
