"""Pytest configuration — ensures project root is on sys.path."""

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()
