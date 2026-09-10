#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compatibilidade — use tools/producao-brflow-kit/tratar_producao.py"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    kit_script = Path(__file__).resolve().parent / "producao-brflow-kit" / "tratar_producao.py"
    runpy.run_path(str(kit_script), run_name="__main__")
