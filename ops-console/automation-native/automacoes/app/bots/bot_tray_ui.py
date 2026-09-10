"""Bot unificado: monitora OneDrive e Cisco na bandeja do Windows."""
from __future__ import annotations

import logging

from app.bots.tray_ui_runner import CombinedTrayUiBot, DEFAULT_TRAY_SERVICES

_combined = CombinedTrayUiBot(DEFAULT_TRAY_SERVICES)

parar_event = _combined.parar_event
set_status_callback = _combined.set_status_callback
set_progress_callback = _combined.set_progress_callback
_run_cycle = _combined.run_cycle
_run_loop = _combined.run_loop
start = _combined.start
stop = _combined.stop

# Acesso aos bots individuais (testes / ferramentas)
_onedrive_bot = _combined.get_service_bot("onedrive")
_cisco_bot = _combined.get_service_bot("cisco")


def main():
    logging.basicConfig(level=logging.INFO)
    set_status_callback(lambda msg: print(f"STATUS|{msg}", flush=True))
    set_progress_callback(lambda pct, msg: print(f"PROGRESS|{pct}|{msg}", flush=True))
    parar_event.clear()
    try:
        _run_loop()
    except KeyboardInterrupt:
        stop()


if __name__ == "__main__":
    main()
