"""Centralized sys.path setup for package imports."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_project_root_on_path() -> Path:
    """Add project root to sys.path so `app` package resolves."""
    root = Path(__file__).resolve().parent.parent.parent
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


def ensure_serasa_src_on_path() -> Path:
    """Deprecated alias — kept for backward compatibility."""
    return ensure_project_root_on_path()
