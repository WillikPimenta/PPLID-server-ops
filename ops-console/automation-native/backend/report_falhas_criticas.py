# -*- coding: utf-8 -*-
"""Ponto de entrada legado — delega para report_falhas.cli."""
from report_falhas.cli import main
from report_falhas.legacy_main import _legacy_main_impl
from report_falhas.outlook_render import render_projecoes_insuficientes_outlook

__all__ = [
    'main',
    '_legacy_main_impl',
    'render_projecoes_insuficientes_outlook',
]

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nExecução interrompida pelo usuário.')
