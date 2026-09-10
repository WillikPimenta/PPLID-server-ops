"""Isolated PostgreSQL settings for disposable performance benchmarks."""

from config.settings import *  # noqa: F403


# Never let the performance harness point at the configured application database.
# Django creates and destroys this explicit database around the benchmark test run.
DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_pplid_codex_performance"  # noqa: F405

# Query timing is captured with connection.execute_wrapper, independently of DEBUG.
DEBUG = False
