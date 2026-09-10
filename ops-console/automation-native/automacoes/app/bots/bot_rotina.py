"""
Bot Rotina - Fachada de compatibilidade.

Implementação em app.bots.rotina.*
Scripts novos devem importar de app.bots.rotina.* diretamente;
este módulo mantém re-exports legados para robot_runner e scripts FY27.
"""

from app.bots.rotina.constants import COLUNAS_MONITOR_UNIFICADO
from app.bots.rotina.io import _ler_csv_tratamento, _preparar_dataframe_para_parquet
from app.bots.rotina.orchestration import executar_novo_bot, start, stop
from app.bots.rotina.state import parar_event, set_progress_callback, set_status_callback
from app.bots.rotina.tasks.monitor import (
    _normalizar_df_monitor_sessoes,
    _pretratar_monitor_verifica_usuario,
    tratar_arquivo,
)
from app.bots.rotina.tasks.unificados import _juntar_monitor_tratado_com_monitor_confer_dia

__all__ = [
    "start",
    "stop",
    "executar_novo_bot",
    "parar_event",
    "set_status_callback",
    "set_progress_callback",
    "tratar_arquivo",
    "COLUNAS_MONITOR_UNIFICADO",
    "_juntar_monitor_tratado_com_monitor_confer_dia",
    "_normalizar_df_monitor_sessoes",
    "_preparar_dataframe_para_parquet",
    "_pretratar_monitor_verifica_usuario",
    "_ler_csv_tratamento",
]

if __name__ == "__main__":
    from app.bots.rotina.state import _set_status

    parar_event.clear()
    try:
        executar_novo_bot()
        _set_status("Robô completou com sucesso!")
    except KeyboardInterrupt:
        stop()
        _set_status("Robô interrompido pelo usuário")
    except Exception as exc:
        stop()
        _set_status(f"Erro: {exc}")
